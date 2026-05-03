"""
Logic sinh mindmap (tách khỏi main.py để async job gọi, tránh import vòng).
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import re
import unicodedata
import uuid
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

import os

from pydantic import BaseModel, Field
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer

from llm_factory import ask_ai, get_embedding_model
from mindmap_utils import generate_mindmap_flat, generate_mindmap_cmgn, get_main_branches

try:
    from env_loader import load_project_env

    load_project_env(override=False)
except Exception:
    pass

# Model cho mindmap (ưu tiên env MINDMAP_MODEL theo Phase 2 plan).
MINDMAP_MODEL = (
    (os.environ.get("MINDMAP_MODEL") or "").strip()
    or (os.environ.get("SLM_MODEL_MINDMAP") or "").strip()
    or "qwen2.5:14b"
)

# Mindmap cần ít randomness để ổn định schema
MINDMAP_OPTIONS = {"temperature": 0.2}

_mindmap_job_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mindmap_job_id", default=None
)


def attach_mindmap_job_context(job_id: Optional[str]) -> None:
    """Gắn job_id cho luồng hiện tại để ghi progress/current_node (tiếng Việt) qua jobs.sqlite."""
    _mindmap_job_id_ctx.set(job_id)


def _notify_progress(
    progress_cb: Optional[Callable[[int], None]],
    p: int,
    msg_vi: str,
) -> None:
    if progress_cb is not None:
        progress_cb(int(p))
    jid = _mindmap_job_id_ctx.get(None)
    if jid:
        try:
            from jobs_store import update_job

            update_job(jid, progress=int(p), current_node=msg_vi)
        except Exception:
            pass


class MindmapLeaf(BaseModel):
    label: str
    children: list[str] = Field(default_factory=list)


class MindmapBranch(BaseModel):
    label: str
    children: list[MindmapLeaf] = Field(default_factory=list)


class MindmapOutput(BaseModel):
    title: str
    branches: list[MindmapBranch] = Field(default_factory=list)


def _mindmap_output_to_flat_nodes(out: MindmapOutput, root_title_fallback: str) -> list[dict]:
    title = (out.title or "").strip() or root_title_fallback
    nodes: list[dict] = [{"id": "root", "parent": None, "title": title}]
    for bi, br in enumerate(out.branches):
        bid = f"b-{bi}"
        blabel = (br.label or "").strip() or f"Nhánh {bi + 1}"
        nodes.append({"id": bid, "parent": "root", "title": blabel})
        for lj, leaf in enumerate(br.children):
            lid = f"b-{bi}-l-{lj}"
            llabel = (leaf.label or "").strip() or f"Mục {lj + 1}"
            nodes.append({"id": lid, "parent": bid, "title": llabel})
            for ck, s in enumerate(leaf.children):
                st = (s or "").strip()
                if not st:
                    continue
                sid = f"b-{bi}-l-{lj}-c-{ck}"
                nodes.append({"id": sid, "parent": lid, "title": st})
    return nodes


def _concat_cluster_summary(texts: list[str], max_chars: int = 900) -> str:
    parts: list[str] = []
    total = 0
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        chunk = t[:350].strip()
        if total + len(chunk) + 2 > max_chars:
            break
        parts.append(chunk)
        total += len(chunk) + 2
    return " | ".join(parts) if parts else "(trống)"


def _parse_mindmap_output_json(raw: str) -> MindmapOutput:
    raw = (raw or "").strip()
    try:
        return MindmapOutput.model_validate_json(raw)
    except Exception:
        pass
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```\s*$", "", raw)
        raw = raw.strip()
        try:
            return MindmapOutput.model_validate_json(raw)
        except Exception:
            pass
    m = re.search(r"\{[\s\S]*\}\s*$", raw)
    if m:
        return MindmapOutput.model_validate_json(m.group(0))
    raise ValueError("Không parse được JSON MindmapOutput")


def _invoke_mindmap_ollama_once(
    system_prompt: str,
    user_prompt: str,
    model: str,
    timeout_sec: float,
) -> MindmapOutput:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_ollama import ChatOllama

    from llm_factory import lc_ai_message_text

    host = (os.getenv("OLLAMA_HOST") or "http://localhost:11434").strip().rstrip("/")
    schema_dict = MindmapOutput.model_json_schema()

    def _run() -> MindmapOutput:
        llm = ChatOllama(
            model=model,
            base_url=host,
            temperature=0.1,
            num_predict=2000,
            format=schema_dict,
        )
        msgs = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        resp = llm.invoke(msgs, stream=False)
        txt = lc_ai_message_text(resp).strip()
        return _parse_mindmap_output_json(txt)

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(_run)
        try:
            return fut.result(timeout=timeout_sec)
        except FuturesTimeout:
            raise TimeoutError(f"Hết thời gian chờ LLM mindmap ({timeout_sec:.0f}s)") from None


def _call_llm_schema(system_prompt: str, user_prompt: str, model: str, timeout_sec: float) -> MindmapOutput:
    """Một lần invoke LLM với MindmapOutput JSON schema (Ollama)."""
    return _invoke_mindmap_ollama_once(system_prompt, user_prompt, model, timeout_sec)


def _mindmap_cache_path(index_meta_path: Path) -> Path:
    return index_meta_path.resolve().parent.parent / "memory" / "mindmap_content_cache.json"


def _mindmap_cache_load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _mindmap_cache_save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _mindmap_cache_put(path: Path, hkey: str, payload: dict[str, Any]) -> None:
    c = _mindmap_cache_load(path)
    c[hkey] = payload
    _mindmap_cache_save(path, c)


def _content_hash(source_names: list[str], chunks: list[dict]) -> str:
    """MD5(sorted source_names | len(chunks) | chunk[0].text[:100])."""
    key = "|".join(sorted(str(s).strip() for s in source_names if str(s).strip()))
    n = len(chunks)
    tail = ""
    if chunks:
        tail = (chunks[0].get("text") or "")[:100]
    raw = f"{key}|{n}|{tail}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def _cluster_and_label_no_llm(chunks: list[dict], n_clusters: int = 6) -> list[dict]:
    """
    KMeans trên embedding có sẵn + TF-IDF top-3 keyword / cụm.
    Trả về list[{topic, keywords, summary, chunk_count}].
    """
    rows: list[tuple[str, np.ndarray]] = []
    for c in chunks:
        tx = (c.get("text") or "").strip()
        emb = c.get("embedding")
        if not tx:
            continue
        if isinstance(emb, list) and len(emb) > 0:
            rows.append((tx, np.asarray(emb, dtype=np.float32)))

    if not rows:
        raise ValueError("Không có chunk kèm embedding")

    texts = [r[0] for r in rows]
    X = np.vstack([r[1] for r in rows])
    n = X.shape[0]
    k = max(1, min(int(n_clusters), n))
    labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(X)

    max_feat = min(1024, max(64, n * 16))
    vec = TfidfVectorizer(max_features=max_feat, token_pattern=r"(?u)\b\w\w+\b", lowercase=True)
    try:
        M = vec.fit_transform(texts)
    except ValueError:
        vec = TfidfVectorizer(max_features=64, token_pattern=r"(?u)\b\w+\b", lowercase=True)
        M = vec.fit_transform(texts)
    terms = vec.get_feature_names_out()

    out: list[dict] = []
    for cid in range(k):
        idxs = np.where(labels == cid)[0]
        if idxs.size == 0:
            continue
        centroid = np.asarray(M[idxs].mean(axis=0)).ravel()
        top_i = centroid.argsort()[-5:][::-1]
        keywords = [str(terms[i]) for i in top_i if centroid[i] > 1e-9][:3]
        summary = _concat_cluster_summary([texts[int(i)] for i in idxs.tolist()])
        topic = " · ".join(keywords) if keywords else f"Cụm {cid + 1}"
        out.append(
            {
                "topic": topic,
                "keywords": keywords,
                "summary": summary,
                "chunk_count": int(idxs.size),
            }
        )
    if not out:
        raise ValueError("Không tạo được cụm TF-IDF")
    return out


def _build_mindmap_v2(
    chunks: list[dict],
    source_names: list[str],
    model: str,
    embed_fn: Callable[[list[str]], Any],
    root_title: str,
    progress_notify: Callable[[int, str], None],
) -> tuple[list[dict], str]:
    """
    TF-IDF + KMeans (embedding ingest) → prompt gọn → 1 lần LLM schema.
    Lỗi → fallback _build_mindmap_single_call (giữ hành vi cũ).
    """
    texts_only = [(c.get("text") or "").strip() for c in chunks if (c.get("text") or "").strip()]
    sources_line = ", ".join(source_names[:12])
    if len(source_names) > 12:
        sources_line += f" (+{len(source_names) - 12} nguồn)"

    try:
        progress_notify(42, "Đang phân cụm + nhãn TF-IDF (không gọi LLM)…")
        cluster_rows = _cluster_and_label_no_llm(chunks, n_clusters=6)
        lines: list[str] = []
        for i, row in enumerate(cluster_rows):
            kw = ", ".join(row.get("keywords") or [])
            summ = (row.get("summary") or "")[:420]
            lines.append(
                f"[{i + 1}] {row.get('topic')!s} ({row.get('chunk_count')} đoạn)\n"
                f"keywords: {kw}\ntóm tắt: {summ}"
            )
        prompt_body = "\n\n".join(lines)

        sys_prompt = (
            "Từ các cụm đã gán nhãn TF-IDF (topic/keywords/tóm tắt), xây MindmapOutput JSON đúng schema: "
            "title ngắn; branches với label và children (MindmapLeaf: label + children là list string). "
            "Tiếng Việt nếu dữ liệu là tiếng Việt. Không thêm ý không có trong cụm."
        )
        user_prompt = (
            f"Tiêu đề gốc: {root_title}\nNguồn: {sources_line}\n\n"
            f"Cụm (đã xử lý offline, không LLM):\n\n{prompt_body}"
        )

        progress_notify(55, "Đang gọi LLM một lần (mindmap v2 / schema)…")
        timeout_sec = float(os.getenv("MINDMAP_TIMEOUT_SEC", "90"))
        out = _call_llm_schema(sys_prompt, user_prompt, model, timeout_sec)
        if not out.branches:
            raise ValueError("MindmapOutput không có branches")
        flat = _mindmap_output_to_flat_nodes(out, root_title)
        if len(flat) < 3:
            raise ValueError("Quá ít node sau mindmap v2")
        return flat, "mindmap_v2"
    except Exception as exc:
        print(f"[MM v2] lỗi {exc} → fallback single_call_schema")
        return _build_mindmap_single_call(texts_only, source_names, model, embed_fn, root_title, progress_notify)


def _build_mindmap_single_call(
    chunks: list[str],
    source_names: list[str],
    model: str,
    embed_fn: Callable[[list[str]], Any],
    root_title: str,
    progress_notify: Callable[[int, str], None],
) -> tuple[list[dict], str]:
    """Một lần gọi LLM với JSON schema; cụm KMeans + tóm tắt nối chuỗi không dùng LLM."""
    texts = [c.strip() for c in chunks if c and c.strip()]
    if not texts:
        raise ValueError("Không có chunk")

    progress_notify(40, "Đang gom cụm đoạn văn (KMeans trên embedding, không gọi LLM)…")

    emb = np.asarray(embed_fn(texts), dtype=np.float32)
    if emb.ndim != 2 or emb.shape[0] != len(texts):
        raise ValueError("Embedding không khớp số chunk")

    n = len(texts)
    if n == 1:
        labels = np.zeros(1, dtype=np.int32)
        n_clusters = 1
    else:
        n_clusters = min(max(2, int(round(np.sqrt(n)))), n, 12)
        km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = km.fit_predict(emb).astype(np.int32)

    clusters: dict[int, list[str]] = {}
    for lab, tx in zip(labels.tolist(), texts):
        clusters.setdefault(int(lab), []).append(tx)

    ordered_keys = sorted(clusters.keys())
    cluster_summaries: list[str] = []
    for ki, k in enumerate(ordered_keys):
        summary = _concat_cluster_summary(clusters[k])
        cluster_summaries.append(f"[Cụm {ki + 1}]\n{summary}")

    sources_line = ", ".join(source_names[:12])
    if len(source_names) > 12:
        sources_line += f" (+{len(source_names) - 12} nguồn)"

    progress_notify(55, "Đang gọi mô hình một lần (JSON có schema Pydantic / Ollama)…")

    timeout_sec = float(os.getenv("MINDMAP_TIMEOUT_SEC", "90"))

    sys_full = (
        "Bạn chuyển các cụm ý sau thành một sơ đồ tư duy PHÂN CẤP.\n"
        "Trả về ĐÚNG một JSON thỏa schema (title, branches[].label, branches[].children[].label, "
        "branches[].children[].children[] là mảng các chuỗi chi tiết).\n"
        "Ngôn ngữ: tiếng Việt nếu nội dung cụm là tiếng Việt; không bịa thông tin ngoài cụm.\n"
        "title ngắn gọn; khoảng 4–8 nhánh; mỗi nhánh 2–5 leaf; mỗi leaf 1–4 chuỗi con."
    )
    user_full = (
        f"Tiêu đề gợi ý gốc: {root_title}\n"
        f"Nguồn đã chọn: {sources_line}\n\n"
        "Dữ liệu cụm (đã rút gọn bằng nối chuỗi, không qua LLM):\n\n"
        + "\n\n".join(cluster_summaries)
    )

    sys_simple = (
        "Trả về JSON đúng schema: title + branches; mỗi branch có children (leaf: label + children: list string). "
        "Tiếng Việt. Chỉ JSON hợp lệ."
    )
    user_simple = (
        f"Tiêu đề gốc: {root_title}\nNguồn: {sources_line}\n\n"
        + "\n".join(cluster_summaries[: min(len(cluster_summaries), 8)])
    )

    last_err: BaseException | None = None
    for attempt in range(3):
        try:
            sys_p = sys_simple if attempt > 0 else sys_full
            user_p = user_simple if attempt > 0 else user_full
            out = _invoke_mindmap_ollama_once(sys_p, user_p, model, timeout_sec)
            if not out.branches:
                raise ValueError("MindmapOutput không có branches")
            flat = _mindmap_output_to_flat_nodes(out, root_title)
            if len(flat) < 3:
                raise ValueError("Quá ít node sau khi chuyển đổi")
            return flat, "single_call_schema"
        except BaseException as e:
            last_err = e
            print(f"[MM single_call] attempt {attempt + 1}/3 failed: {e}")
            continue

    raise RuntimeError(f"single_call_schema failed sau 3 lần: {last_err}")


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
    topics_raw = ask_ai(user_topics, system_prompt=sys_topics, model=MINDMAP_MODEL, options=MINDMAP_OPTIONS)
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
        sub_raw = ask_ai(user_sub, system_prompt=sys_sub, model=MINDMAP_MODEL, options=MINDMAP_OPTIONS)
        subs0 = _dedupe_short(_parse_json_list(sub_raw), max_items=6)
        subs0 = [s for s in subs0 if _word_count(s) >= 3 and not _is_vague(s)]
        subs = _semantic_dedupe(subs0, model=model, threshold=0.90, max_items=5)
        if not subs:
            subs = _dedupe_short(get_main_branches((group_chunks or final_chunks)[:8], model=MINDMAP_MODEL) or [], max_items=3)
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
            facts_raw = ask_ai(user_fact, system_prompt=sys_fact, model=MINDMAP_MODEL, options=MINDMAP_OPTIONS)
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
    def _prog_msg(p: int, msg_vi: str) -> None:
        _notify_progress(progress_cb, p, msg_vi)

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

    _prog_msg(5, "Đang đọc chỉ mục và chuẩn bị nguồn tài liệu…")

    with open(index_meta_path, encoding="utf-8") as f:
        meta = json.load(f)

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
                "embedding": m.get("embedding"),
            })

    print(f"📦 Tìm thấy {len(all_chunks_with_meta)} chunks từ {len(meta)} entries trong index")
    _prog_msg(20, "Đã lọc chỉ mục; đang gộp chunk phụ (sub-chunk) nếu có…")

    if not all_chunks_with_meta:
        flat_nodes = [
            {"id": "root", "parent": None, "title": root_title},
            {"id": "root-0", "parent": "root", "title": "No content available"}
        ]
        strategy_used = "fallback"
    else:
        merged_logical: list[dict] = []
        sub_groups = {}
        logical_normal: list[dict] = []
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
                logical_normal.append({"text": text, "embedding": item.get("embedding")})
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
                emb_vecs: list[np.ndarray] = []
                for sub in subs:
                    e = sub.get("embedding")
                    if isinstance(e, list) and len(e) > 0:
                        emb_vecs.append(np.asarray(e, dtype=np.float32))
                avg_emb: list[float] | None = None
                if emb_vecs:
                    avg_emb = np.mean(np.stack(emb_vecs, axis=0), axis=0).astype(float).tolist()
                merged_logical.append({"text": merged_text.strip(), "embedding": avg_emb})
            else:
                print(f"⚠️ Warning: Parent {parent_key} merge ra text rỗng")

        final_logical_chunks = logical_normal + merged_logical
        final_chunks = [c["text"] for c in final_logical_chunks]

        print(
            f"📊 Mindmap generation: {len(all_chunks_with_meta)} total items, "
            f"{len(logical_normal)} normal chunks, {len(merged_logical)} merged chunks, {len(final_chunks)} final chunks"
        )

        if not final_chunks and all_chunks_with_meta:
            print(f"⚠️ ERROR: Có {len(all_chunks_with_meta)} chunks nhưng final_chunks rỗng!")
            print(f"   - Normal chunks: {len(logical_normal)}")
            print(f"   - Sub groups: {len(sub_groups)}")
            print(f"   - Merged chunks: {len(merged_logical)}")
            fallback_chunks = [item.get("text", "").strip() for item in all_chunks_with_meta if item.get("text", "").strip()]
            if fallback_chunks:
                print(f"   - Using fallback: {len(fallback_chunks)} chunks")
                final_chunks = fallback_chunks
                final_logical_chunks = [{"text": t, "embedding": None} for t in fallback_chunks]

        if not final_chunks:
            print(f"❌ Không có chunks để sinh mindmap")
            flat_nodes = [
                {"id": "root", "parent": None, "title": root_title},
                {"id": "root-0", "parent": "root", "title": "No content available"}
            ]
            strategy_used = "fallback"
        else:
            print(f"🚀 Bắt đầu sinh mindmap với {len(final_chunks)} chunks...")
            flat_nodes = None
            strategy_used = None

            cache_path = _mindmap_cache_path(Path(index_meta_path))
            content_hash_key = _content_hash(source_names, final_logical_chunks)
            cached_blob = _mindmap_cache_load(cache_path).get(content_hash_key)
            if cached_blob and isinstance(cached_blob.get("nodes"), list) and len(cached_blob["nodes"]) >= 2:
                _prog_msg(100, "Có kết quả lưu sẵn")
                flat_nodes = cached_blob["nodes"]
                strategy_used = cached_blob.get("strategy") or "cache_hit"

            embed_model = get_embedding_model()
            has_any_emb = any(
                isinstance(c.get("embedding"), list) and len(c.get("embedding") or []) > 0
                for c in final_logical_chunks
            )

            if flat_nodes is None and embed_model is not None and has_any_emb:
                try:

                    def _embed_fn(tx: list[str]) -> Any:
                        return embed_model.encode(tx, convert_to_numpy=True, show_progress_bar=False)

                    flat_nodes, strategy_used = _build_mindmap_v2(
                        final_logical_chunks,
                        source_names,
                        MINDMAP_MODEL,
                        _embed_fn,
                        root_title,
                        _prog_msg,
                    )
                    print(f"   ✓ mindmap v2 chain ({strategy_used}): {len(flat_nodes)} nodes")
                    if flat_nodes and strategy_used in ("mindmap_v2", "single_call_schema"):
                        _mindmap_cache_put(
                            cache_path,
                            content_hash_key,
                            {"nodes": flat_nodes, "strategy": strategy_used},
                        )
                except Exception as exc_v2:
                    print(f"   ⚠️ mindmap_v2 chain failed: {exc_v2} → single_call / multilevel")
                    flat_nodes = None
                    strategy_used = None

            if flat_nodes is None and embed_model is not None:
                try:

                    def _embed_fn(tx: list[str]) -> Any:
                        return embed_model.encode(tx, convert_to_numpy=True, show_progress_bar=False)

                    flat_nodes, strategy_used = _build_mindmap_single_call(
                        final_chunks,
                        source_names,
                        MINDMAP_MODEL,
                        _embed_fn,
                        root_title,
                        _prog_msg,
                    )
                    print(f"   ✓ single_call_schema (ngoài): {len(flat_nodes)} nodes")
                    if flat_nodes and strategy_used == "single_call_schema":
                        _mindmap_cache_put(
                            cache_path,
                            content_hash_key,
                            {"nodes": flat_nodes, "strategy": strategy_used},
                        )
                except Exception as exc_sc:
                    print(f"   ⚠️ single_call_schema failed: {exc_sc} → fallback multilevel")
                    flat_nodes = None
                    strategy_used = None

            if flat_nodes is None:
                _prog_msg(40, "Đang chạy pipeline dự phòng (phân tầng LLM, nhiều bước)…")
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

            if flat_nodes is None:
                _prog_msg(55, "Đang thử chiến lược CMGN hoặc iterative (dự phòng)…")
                if strategy_requested in {"cmgn", "semantic", "coreference"}:
                    try:
                        print(f"   → Thử CMGN strategy...")
                        flat_nodes = generate_mindmap_cmgn(final_chunks, model=MINDMAP_MODEL)
                        strategy_used = "cmgn"
                        print(f"   ✓ CMGN thành công: {len(flat_nodes)} nodes")
                    except Exception as exc2:
                        print(f"   ⚠️ CMGN failed: {exc2}, fallback iterative")
                        try:
                            flat_nodes = generate_mindmap_flat(final_chunks, model=MINDMAP_MODEL)
                            strategy_used = "iterative"
                            print(f"   ✓ Iterative thành công: {len(flat_nodes)} nodes")
                        except Exception as exc3:
                            print(f"   ❌ Iterative cũng failed: {exc3}")
                            flat_nodes = None
                else:
                    try:
                        print(f"   → Thử Iterative strategy...")
                        flat_nodes = generate_mindmap_flat(final_chunks, model=MINDMAP_MODEL)
                        strategy_used = "iterative"
                        print(f"   ✓ Iterative thành công: {len(flat_nodes)} nodes")
                    except Exception as exc4:
                        print(f"   ❌ Iterative failed: {exc4}")
                        flat_nodes = None

            if not flat_nodes or len(flat_nodes) == 0:
                print(f"   ⚠️ Tất cả strategies failed, tạo fallback mindmap")
                try:
                    mains = get_main_branches(final_chunks[:10], model=MINDMAP_MODEL)
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

    if strategy_used != "cache_hit":
        _prog_msg(90, "Đang lưu mind map vào bộ nhớ…")

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

    append_mindmap(mindmap_record)
    _prog_msg(100, "Hoàn thành sơ đồ tư duy.")
    return mindmap_record
