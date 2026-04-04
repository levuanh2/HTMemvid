"""
Logic sinh mindmap (tách khỏi main.py để async job gọi, tránh import vòng).
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ollama_utils import SLM_MODEL
from mindmap_utils import generate_mindmap_flat, generate_mindmap_cmgn, get_main_branches


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

            if strategy_requested in {"cmgn", "semantic", "coreference"}:
                try:
                    print(f"   → Thử CMGN strategy...")
                    flat_nodes = generate_mindmap_cmgn(final_chunks, model=SLM_MODEL)
                    strategy_used = "cmgn"
                    print(f"   ✓ CMGN thành công: {len(flat_nodes)} nodes")
                except Exception as exc:
                    print(f"   ⚠️ CMGN failed: {exc}, fallback iterative")
                    try:
                        flat_nodes = generate_mindmap_flat(final_chunks, model=SLM_MODEL)
                        strategy_used = "iterative"
                        print(f"   ✓ Iterative thành công: {len(flat_nodes)} nodes")
                    except Exception as exc2:
                        print(f"   ❌ Iterative cũng failed: {exc2}")
                        flat_nodes = None
            else:
                try:
                    print(f"   → Thử Iterative strategy...")
                    flat_nodes = generate_mindmap_flat(final_chunks, model=SLM_MODEL)
                    strategy_used = "iterative"
                    print(f"   ✓ Iterative thành công: {len(flat_nodes)} nodes")
                except Exception as exc:
                    print(f"   ❌ Iterative failed: {exc}")
                    flat_nodes = None

            _prog(78)

            if not flat_nodes or len(flat_nodes) == 0:
                print(f"   ⚠️ Tất cả strategies failed, tạo fallback mindmap")
                try:
                    mains = get_main_branches(final_chunks[:10], model=SLM_MODEL)
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
