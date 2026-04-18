"""
Logic sinh mindmap (tách khỏi main.py để async job gọi, tránh import vòng).
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from embedding_model import get_embedding_model
import os
from ai_provider import ask_ai
from mindmap_utils import generate_mindmap_flat, generate_mindmap_cmgn, get_main_branches

# Chỉ dùng cho local Ollama (Gemini sẽ bỏ qua model).
SLM_MODEL_MINDMAP = os.environ.get("SLM_MODEL_MINDMAP", "gemma2:2b")


STRUCTURE_LABELS: list[tuple[str, str]] = [
    ("overview", "Overview / Definition"),
    ("components", "Components / Structure"),
    ("process", "Process / Workflow"),
    ("applications", "Applications / Use cases"),
    ("issues", "Issues / Limitations / Challenges"),
]

VAGUE_WORDS = {
    "introduction", "introduce", "general", "content", "summary",
    "tổng quan", "giới thiệu", "chung", "nội dung",
}

STOPWORDS_VI = {
    "và", "là", "của", "trong", "cho", "với", "các", "một", "những", "được", "khi", "từ", "đến", "theo", "này", "đó",
    "trên", "dưới", "hơn", "ít", "nhiều", "vì", "do", "để", "có", "không", "tại", "về", "như", "cũng", "đang", "sẽ",
}


def _word_count(s: str) -> int:
    return len([w for w in re.split(r"\s+", (s or "").strip()) if w])


def _is_vague(title: str) -> bool:
    t = (title or "").strip().lower()
    if len(t) < 3:
        return True
    return any(v in t for v in VAGUE_WORDS)


def _dedupe_short(items: list[str], max_items: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        it = (it or "").strip()
        if not it:
            continue
        key = re.sub(r"\s+", " ", it).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= max_items:
            break
    return out


def _parse_json_list(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x) for x in val]
    except Exception:
        pass

    m = re.search(r"\[[\s\S]*\]", raw)
    if not m:
        return []
    try:
        val = json.loads(m.group(0))
        if isinstance(val, list):
            return [str(x) for x in val]
    except Exception:
        return []
    return []


def _unit(x: np.ndarray) -> np.ndarray:
    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-12)


def _semantic_dedupe(items: list[str], model, threshold: float, max_items: int) -> list[str]:
    """
    Dedupe bằng cosine similarity. Giữ thứ tự ưu tiên ban đầu.
    """
    items = [i.strip() for i in items if i and i.strip()]
    items = _dedupe_short(items, max_items=max_items * 2)
    if len(items) <= 1:
        return items[:max_items]

    emb = model.encode(items, convert_to_numpy=True, show_progress_bar=False).astype("float32")
    emb = _unit(emb)
    keep: list[int] = []
    for i in range(len(items)):
        ok = True
        for j in keep:
            if float(emb[i] @ emb[j]) >= threshold:
                ok = False
                break
        if ok:
            keep.append(i)
        if len(keep) >= max_items:
            break
    return [items[i] for i in keep]


def _extract_keywords(chunks: list[str], top_k: int = 24) -> list[str]:
    toks: list[str] = []
    for c in chunks:
        c = (c or "").lower()
        for w in re.findall(r"[\w\-À-ỹ]{3,}", c):
            if w in STOPWORDS_VI:
                continue
            toks.append(w)
    freq = Counter(toks)
    return [w for w, _ in freq.most_common(top_k)]


def _topic_keyword_overlap(topic: str, keywords: set[str]) -> bool:
    t = (topic or "").lower()
    topic_tokens = set(re.findall(r"[\w\-À-ỹ]{3,}", t))
    return len(topic_tokens & keywords) > 0


def _select_diverse_chunks(all_chunks: list[str], model, k_sim: int = 18, k_rand: int = 8) -> list[str]:
    """
    Chọn context tránh bias: top-sim (so với centroid) + random (diversity).
    """
    chunks = [c.strip() for c in all_chunks if c and c.strip()]
    if not chunks:
        return []
    if len(chunks) <= (k_sim + k_rand):
        return chunks

    pool = chunks[: min(len(chunks), 240)]
    emb = model.encode(pool, convert_to_numpy=True, show_progress_bar=False).astype("float32")
    emb_u = _unit(emb)
    centroid = _unit(np.mean(emb_u, axis=0, keepdims=True))
    sims = (emb_u @ centroid.T).reshape(-1)
    top_idx = np.argsort(-sims)[:k_sim].tolist()
    remaining = [i for i in range(len(pool)) if i not in set(top_idx)]
    rand_idx = random.sample(remaining, k=min(k_rand, len(remaining)))
    return [pool[i] for i in (top_idx + rand_idx)]


def _soft_cluster(
    topics: list[str],
    chunks: list[str],
    model,
    threshold: float = 0.56,
) -> tuple[dict[str, list[str]], dict[str, np.ndarray]]:
    """
    Soft clustering: 1 chunk có thể thuộc nhiều topics nếu sim >= threshold.
    Luôn đảm bảo chunk thuộc ít nhất 1 topic (best-match fallback).
    """
    topics = [t.strip() for t in topics if t and t.strip()]
    chunks = [c.strip() for c in chunks if c and c.strip()]
    groups: dict[str, list[str]] = {t: [] for t in topics}
    if not topics or not chunks:
        return groups, {}

    t_emb = _unit(model.encode(topics, convert_to_numpy=True, show_progress_bar=False).astype("float32"))
    c_emb = _unit(model.encode(chunks, convert_to_numpy=True, show_progress_bar=False).astype("float32"))
    sims = c_emb @ t_emb.T  # (C, T)

    for i, row in enumerate(sims):
        assigned = False
        for j, s in enumerate(row.tolist()):
            if float(s) >= threshold:
                groups[topics[j]].append(chunks[i])
                assigned = True
        if not assigned:
            j = int(np.argmax(row))
            groups[topics[j]].append(chunks[i])

    t_map = {topics[i]: t_emb[i] for i in range(len(topics))}
    return groups, t_map


def _rerank_topics(topics: list[str], groups: dict[str, list[str]], top_n: int = 8) -> list[str]:
    scored = [(float(len(groups.get(t) or [])), t) for t in topics]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:top_n]]


def _classify_topic_to_structure(topic: str, model) -> str:
    labels = [lbl for _, lbl in STRUCTURE_LABELS]
    lab_u = _unit(model.encode(labels, convert_to_numpy=True, show_progress_bar=False).astype("float32"))
    t_u = _unit(model.encode([topic], convert_to_numpy=True, show_progress_bar=False).astype("float32"))
    sims = (t_u @ lab_u.T).reshape(-1)
    return STRUCTURE_LABELS[int(np.argmax(sims))][0]


def _cosine_assign(groups: list[str], texts: list[str], model) -> dict[str, list[str]]:
    if not groups or not texts:
        return {g: [] for g in groups}

    g_emb = model.encode(groups, convert_to_numpy=True, show_progress_bar=False).astype("float32")
    t_emb = model.encode(texts, convert_to_numpy=True, show_progress_bar=False).astype("float32")

    g_norm = g_emb / (np.linalg.norm(g_emb, axis=1, keepdims=True) + 1e-12)
    t_norm = t_emb / (np.linalg.norm(t_emb, axis=1, keepdims=True) + 1e-12)

    sims = t_norm @ g_norm.T
    best = np.argmax(sims, axis=1)

    out: dict[str, list[str]] = {g: [] for g in groups}
    for i, gi in enumerate(best.tolist()):
        out[groups[int(gi)]].append(texts[i])
    return out


def _build_multilevel_mindmap(
    root_title: str,
    final_chunks: list[str],
    progress_cb: Optional[Callable[[int], None]] = None,
    enable_level3: bool = False,
) -> tuple[list[dict], str]:
    def _prog(p: int) -> None:
        if progress_cb is not None:
            progress_cb(p)

    model = get_embedding_model()
    if model is None:
        raise RuntimeError("Embedding model not available (CI mode)")

    # Context selection tránh bias: top-sim + random diversity
    sample_for_topics = _select_diverse_chunks(final_chunks, model, k_sim=18, k_rand=8)
    if not sample_for_topics:
        raise ValueError("No chunks for mindmap")

    _prog(56)

    sys_topics = (
        "You are an expert knowledge organizer.\n"
        "Extract HIGH-LEVEL conceptual topics from the document.\n\n"
        "Rules:\n"
        "- Topics must be DISTINCT (no overlap)\n"
        "- Topics must follow logical structure if possible:\n"
        "  Definition / Overview\n"
        "  Components / Structure\n"
        "  Process / Workflow\n"
        "  Applications / Use cases\n"
        "  Issues / Limitations / Challenges\n"
        "- Avoid vague words like: 'Introduction', 'General', 'Content'\n"
        "- Each topic should represent a MEANINGFUL concept cluster\n"
        "- Each topic should be 3–6 words, no long sentences\n\n"
        "Return JSON array only:\n"
        "[\"Topic 1\", \"Topic 2\", ...]"
    )
    user_topics = "Nội dung:\n\n" + "\n\n---\n\n".join(sample_for_topics)
    topics_raw = ask_ai(user_topics, system_prompt=sys_topics, model=SLM_MODEL_MINDMAP)
    topics_0 = _dedupe_short(_parse_json_list(topics_raw), max_items=10)
    topics_0 = [t for t in topics_0 if not _is_vague(t)]
    topics = _semantic_dedupe(topics_0, model=model, threshold=0.85, max_items=8)

    # Keyword extraction support: topic phải bám dữ liệu
    keywords = set(_extract_keywords(sample_for_topics, top_k=26))
    topics = [t for t in topics if _topic_keyword_overlap(t, keywords)]
    topics = _semantic_dedupe(topics, model=model, threshold=0.85, max_items=8)

    print(f"[MM] topics generated: {len(topics)}")
    if len(topics) < 3:
        raise ValueError("LLM topics extraction failed")

    _prog(64)

    chunks_for_cluster = [c.strip() for c in (final_chunks[:450] if final_chunks else []) if c and c.strip()]
    topic_groups, _topic_vecs = _soft_cluster(
        topics=topics,
        chunks=chunks_for_cluster,
        model=model,
        threshold=0.56,
    )
    print("[MM] clustering done")

    _prog(72)

    # Rerank topics theo độ phủ nhóm
    topics = _rerank_topics(topics, topic_groups, top_n=8)

    # Structure enforcement: luôn có 5 nhánh chuẩn
    nodes: list[dict] = [{"id": "root", "parent": None, "title": root_title}]
    structure_ids: dict[str, str] = {}
    for key, label in STRUCTURE_LABELS:
        sid = f"s-{key}"
        structure_ids[key] = sid
        nodes.append({"id": sid, "parent": "root", "title": label})

    # Bucket topics vào 5 nhánh chuẩn
    bucketed: dict[str, list[str]] = {k: [] for k, _ in STRUCTURE_LABELS}
    for t in topics:
        bucketed[_classify_topic_to_structure(t, model)].append(t)

    topic_ids: dict[str, str] = {}
    for key, _label in STRUCTURE_LABELS:
        for ti, topic in enumerate(bucketed.get(key) or []):
            tid = f"t-{key}-{ti}"
            topic_ids[topic] = tid
            nodes.append({"id": tid, "parent": structure_ids[key], "title": topic})

    _prog(78)

    max_nodes = 120
    auto_level3 = enable_level3 or (len(final_chunks) <= 220)

    for topic in topics:
        tid = topic_ids.get(topic)
        if not tid:
            continue
        group_chunks = [c for c in (topic_groups.get(topic) or []) if c and c.strip()]
        group_sample = _select_diverse_chunks(group_chunks, model, k_sim=10, k_rand=4)[:14]

        sys_sub = (
            "Bạn đang tạo mindmap nhiều tầng.\n"
            f"Chủ đề chính: {topic}\n\n"
            "Từ nội dung bên dưới, hãy tạo 3–5 Ý NHỎ (subtopics) thuộc chủ đề này.\n"
            "YÊU CẦU:\n"
            "- Mỗi subtopic 3–7 từ\n"
            "- Break down topic thành các phần có ý nghĩa\n"
            "- Có liên kết logic, tránh lặp ý / từ mơ hồ\n"
            "- Nên bám cấu trúc: definition / components / process / examples / issues (nếu phù hợp)\n"
            "- Không copy nguyên văn câu dài\n"
            "Chỉ trả về JSON array of strings."
        )
        user_sub = "Nội dung:\n\n" + "\n\n---\n\n".join(group_sample) if group_sample else "Nội dung: (không có mẫu rõ ràng)"
        sub_raw = ask_ai(user_sub, system_prompt=sys_sub, model=SLM_MODEL_MINDMAP)
        subs0 = _dedupe_short(_parse_json_list(sub_raw), max_items=6)
        subs0 = [s for s in subs0 if _word_count(s) >= 3 and not _is_vague(s)]
        subs = _semantic_dedupe(subs0, model=model, threshold=0.90, max_items=5)
        if not subs:
            subs = _dedupe_short(get_main_branches((group_chunks or final_chunks)[:8], model=SLM_MODEL_MINDMAP) or [], max_items=3)
        if not subs:
            continue

        for si, sub in enumerate(subs):
            if len(nodes) >= max_nodes:
                break
            sid = f"{tid}-s-{si}"
            nodes.append({"id": sid, "parent": tid, "title": sub})

            if not auto_level3:
                continue

            if len(nodes) >= (max_nodes - 4):
                continue
            sys_fact = (
                "Bạn đang tạo mindmap.\n"
                f"Topic: {topic}\n"
                f"Subtopic: {sub}\n\n"
                "Từ nội dung bên dưới, trích xuất 2–3 key points NGẮN, rõ nghĩa.\n"
                "YÊU CẦU:\n"
                "- Mỗi point <= 12 từ\n"
                "- Không lặp ý\n"
                "- Không copy nguyên văn câu dài\n"
                "Chỉ trả về JSON array of strings."
            )
            user_fact = "Nội dung:\n\n" + "\n\n---\n\n".join(group_sample[:6])
            facts_raw = ask_ai(user_fact, system_prompt=sys_fact, model=SLM_MODEL_MINDMAP)
            facts0 = _dedupe_short(_parse_json_list(facts_raw), max_items=3)
            facts0 = [f for f in facts0 if _word_count(f) >= 3 and not _is_vague(f)]
            facts = _semantic_dedupe(facts0, model=model, threshold=0.90, max_items=3)
            for fi, fact in enumerate(facts):
                if len(nodes) >= max_nodes:
                    break
                fid = f"{sid}-f-{fi}"
                nodes.append({"id": fid, "parent": sid, "title": fact})

    print("[MM] subtopics generated")

    # Fail-safe quality check
    has_sub = any(isinstance(n.get("id"), str) and "-s-" in n.get("id", "") for n in nodes)
    if not has_sub:
        raise ValueError("No subtopics generated")

    titles = [str(n.get("title") or "").strip().lower() for n in nodes if isinstance(n, dict)]
    uniq = len(set([t for t in titles if t]))
    if uniq < max(10, int(len(titles) * 0.65)):
        raise ValueError("Too many duplicates in mindmap nodes")

    _prog(86)
    print(f"[MM] final nodes: {len(nodes)}")
    return nodes, "advanced_v2"

def run_mindmap_generation(
    index_meta_path: Path,
    source_names: list[str],
    strategy_requested: str,
    append_mindmap: Callable[[dict], None],
    progress_cb: Optional[Callable[[int], None]] = None,
) -> dict:
    """
    Logic cũ của POST /generate-mindmap (synchronous body).
    Gọi append_mindmap khi đã có record hoàn chỉnh.
    """
    def _prog(p: int) -> None:
        if progress_cb is not None:
            progress_cb(p)

    if len(source_names) == 1:
        root_title = Path(source_names[0]).stem or "Mind Map"
    else:
        display_candidates = [Path(name).stem for name in source_names if Path(name).stem]
        if not display_candidates:
            root_title = "Mind Map tổng hợp"
        else:
            preview = ", ".join(display_candidates[:3])
            if len(display_candidates) > 3:
                preview += f" + {len(display_candidates) - 3} nguồn"
            root_title = f"Tổng hợp: {preview}"

    _prog(12)

    with open(index_meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    _prog(22)

    def normalize_video_name(name: str) -> str:
        if not name:
            return ""
        name = Path(name).name if '/' in name or '\\' in name else name
        cleaned = unicodedata.normalize('NFKD', name.strip()).replace('\u00a0', ' ')
        cleaned = cleaned.replace('.mp4', '')
        cleaned = re.sub(r'_\d{8}_\d{6}$', '', cleaned)
        return cleaned.strip().lower()

    normalized_sources = set()
    for s in source_names:
        normalized = normalize_video_name(s)
        if normalized:
            normalized_sources.add(normalized)

    print(f"🔍 Normalized sources: {normalized_sources}")

    all_chunks_with_meta = []
    for key, m in meta.items():
        video_raw = m.get("video", "").strip()
        if not video_raw:
            continue
        video_clean = normalize_video_name(video_raw)

        if video_clean in normalized_sources:
            all_chunks_with_meta.append({
                "text": m.get("text", ""),
                "parent_id": m.get("parent_id"),
                "sub_order": m.get("sub_order"),
                "total_parts": m.get("total_parts"),
                "is_subchunk": m.get("is_subchunk", False),
                "key": key,
            })

    print(f"📦 Tìm thấy {len(all_chunks_with_meta)} chunks từ {len(meta)} entries trong index")
    _prog(38)

    if not all_chunks_with_meta:
        flat_nodes = [
            {"id": "root", "parent": None, "title": root_title},
            {"id": "root-0", "parent": "root", "title": "No content available"}
        ]
        strategy_used = "fallback"
    else:
        merged_chunks = []
        sub_groups = {}
        normal_chunks = []
        chunk_keys_by_parent = {}

        for item in all_chunks_with_meta:
            is_subchunk = item.get("is_subchunk", False)
            parent_id = item.get("parent_id")
            item_key = str(item.get("key", "")).strip()
            text = item.get("text", "").strip()

            if not text:
                continue

            if is_subchunk and parent_id:
                parent_key = str(parent_id).strip()
                if parent_key:
                    if parent_key not in sub_groups:
                        sub_groups[parent_key] = []
                    sub_groups[parent_key].append(item)
            else:
                normal_chunks.append(text)
                if item_key:
                    chunk_keys_by_parent[item_key] = item

        for parent_key, subs in sub_groups.items():
            if parent_key in chunk_keys_by_parent:
                print(f"ℹ️ Parent {parent_key} đã có chunk gốc, bỏ qua {len(subs)} sub-chunks")
                continue

            subs.sort(key=lambda x: (x.get("sub_order") or 0, x.get("text", "")))

            total_parts = subs[0].get("total_parts") if subs else None
            if total_parts and len(subs) < total_parts:
                print(f"⚠️ Warning: Parent {parent_key} có {len(subs)}/{total_parts} sub-chunks (thiếu {total_parts - len(subs)} parts)")

            merged_text = "\n\n".join(
                sub.get("text", "").strip()
                for sub in subs
                if sub.get("text", "").strip()
            )

            if merged_text.strip():
                merged_chunks.append(merged_text)
            else:
                print(f"⚠️ Warning: Parent {parent_key} merge ra text rỗng")

        final_chunks = normal_chunks + merged_chunks

        print(
            f"📊 Mindmap generation: {len(all_chunks_with_meta)} total items, "
            f"{len(normal_chunks)} normal chunks, {len(merged_chunks)} merged chunks, {len(final_chunks)} final chunks"
        )

        if not final_chunks and all_chunks_with_meta:
            print(f"⚠️ ERROR: Có {len(all_chunks_with_meta)} chunks nhưng final_chunks rỗng!")
            print(f"   - Normal chunks: {len(normal_chunks)}")
            print(f"   - Sub groups: {len(sub_groups)}")
            print(f"   - Merged chunks: {len(merged_chunks)}")
            fallback_chunks = [item.get("text", "").strip() for item in all_chunks_with_meta if item.get("text", "").strip()]
            if fallback_chunks:
                print(f"   - Using fallback: {len(fallback_chunks)} chunks")
                final_chunks = fallback_chunks

        if not final_chunks:
            print(f"❌ Không có chunks để sinh mindmap")
            flat_nodes = [
                {"id": "root", "parent": None, "title": root_title},
                {"id": "root-0", "parent": "root", "title": "No content available"}
            ]
            strategy_used = "fallback"
        else:
            print(f"🚀 Bắt đầu sinh mindmap với {len(final_chunks)} chunks...")
            _prog(52)
            flat_nodes = None
            strategy_used = None

            # NEW: Multilevel pipeline (NotebookLM-like). Nếu fail → fallback về one-shot (giữ tương thích).
            try:
                enable_level3 = False  # optional nâng cấp sau
                flat_nodes, strategy_used = _build_multilevel_mindmap(
                    root_title=root_title,
                    final_chunks=final_chunks,
                    progress_cb=progress_cb,
                    enable_level3=enable_level3,
                )
                print(f"   ✓ Multilevel mindmap thành công: {len(flat_nodes)} nodes")
            except Exception as exc:
                print(f"   ⚠️ Multilevel failed: {exc} -> fallback one-shot")
                flat_nodes = None
                strategy_used = None

                if strategy_requested in {"cmgn", "semantic", "coreference"}:
                    try:
                        print(f"   → Thử CMGN strategy...")
                        flat_nodes = generate_mindmap_cmgn(final_chunks, model=SLM_MODEL_MINDMAP)
                        strategy_used = "cmgn"
                        print(f"   ✓ CMGN thành công: {len(flat_nodes)} nodes")
                    except Exception as exc2:
                        print(f"   ⚠️ CMGN failed: {exc2}, fallback iterative")
                        try:
                            flat_nodes = generate_mindmap_flat(final_chunks, model=SLM_MODEL_MINDMAP)
                            strategy_used = "iterative"
                            print(f"   ✓ Iterative thành công: {len(flat_nodes)} nodes")
                        except Exception as exc3:
                            print(f"   ❌ Iterative cũng failed: {exc3}")
                            flat_nodes = None
                else:
                    try:
                        print(f"   → Thử Iterative strategy...")
                        flat_nodes = generate_mindmap_flat(final_chunks, model=SLM_MODEL_MINDMAP)
                        strategy_used = "iterative"
                        print(f"   ✓ Iterative thành công: {len(flat_nodes)} nodes")
                    except Exception as exc4:
                        print(f"   ❌ Iterative failed: {exc4}")
                        flat_nodes = None

            _prog(78)

            if not flat_nodes or len(flat_nodes) == 0:
                print(f"   ⚠️ Tất cả strategies failed, tạo fallback mindmap")
                try:
                    mains = get_main_branches(final_chunks[:10], model=SLM_MODEL_MINDMAP)
                    if mains:
                        flat_nodes = [
                            {"id": "root", "parent": None, "title": root_title}
                        ]
                        for idx, main in enumerate(mains):
                            flat_nodes.append({
                                "id": f"root-{idx}",
                                "parent": "root",
                                "title": main
                            })
                        strategy_used = "fallback_branches"
                        print(f"   ✓ Fallback branches thành công: {len(flat_nodes)} nodes")
                    else:
                        raise ValueError("Không tạo được branches")
                except Exception as exc3:
                    print(f"   ❌ Fallback cũng failed: {exc3}")
                    flat_nodes = [
                        {"id": "root", "parent": None, "title": root_title},
                        {"id": "root-0", "parent": "root", "title": "Không thể sinh mindmap từ dữ liệu"}
                    ]
                    strategy_used = "error"

    _prog(88)

    if flat_nodes:
        root_node = next((n for n in flat_nodes if n.get("parent") is None), flat_nodes[0])
        root_node["title"] = root_title or root_node.get("title") or "Mind Map"

    mindmap_record = {
        "id": str(uuid.uuid4()),
        "title": root_title,
        "nodes": flat_nodes,
        "sources": source_names,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "strategy": strategy_used,
    }

    _prog(95)
    append_mindmap(mindmap_record)
    _prog(100)
    return mindmap_record
