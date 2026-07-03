"""Gom input mindmap TẠI MONOLITH — worker/service không tự đọc đĩa (spec §4.1)."""
from __future__ import annotations

import json
from pathlib import Path

from shared.source_id import canonical_source_stem


def _load_tree_sections(stems: set[str]) -> list[dict]:
    """Section node từ memory tree của các source (fallback skeleton)."""
    try:
        from app.domains.memory.tree import _load_memory_trees
        out: list[dict] = []
        for tree in _load_memory_trees() or []:
            if canonical_source_stem(tree.get("source_stem") or "") not in stems:
                continue
            for n in tree.get("nodes") or []:
                if n.get("type") == "section" and (n.get("title") or "").strip():
                    out.append({"title": n["title"].strip(),
                                "chunk_refs": [str(r) for r in (n.get("chunk_refs") or [])]})
        return out
    except Exception:
        return []


def _title_for(source_names: list[str]) -> str:
    stems = [Path(s).stem for s in source_names if Path(s).stem]
    if not stems:
        return "Mind Map tổng hợp"
    if len(source_names) == 1:
        return stems[0]
    preview = ", ".join(stems[:3])
    if len(stems) > 3:
        preview += f" + {len(stems) - 3} nguồn"
    return f"Tổng hợp: {preview}"


def collect_mindmap_input(index_meta_path: Path, source_names: list[str]) -> dict:
    from app.domains.vectorstore import chunk_text_store

    wanted = {canonical_source_stem(s) for s in (source_names or []) if (s or "").strip()}
    wanted.discard("")
    with open(index_meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    def _text(key: str, m: dict) -> str:
        try:
            t = (chunk_text_store.get_text(int(key)) or "").strip()
        except Exception:
            t = ""
        return t or (m.get("text") or "").strip()

    parents: dict[str, dict] = {}
    subs: dict[str, list[tuple[int, str, str]]] = {}
    order: list[str] = []
    for key, m in (meta or {}).items():
        if not isinstance(m, dict):
            continue
        stem = canonical_source_stem(m.get("source_stem") or m.get("video") or "")
        if not stem or stem not in wanted:
            continue
        if m.get("is_subchunk") and m.get("parent_id"):
            pk = str(m["parent_id"]).strip()
            subs.setdefault(pk, []).append((int(m.get("sub_order") or 0), str(key), _text(key, m)))
        else:
            parents[str(key)] = {"key": str(key), "text": _text(key, m),
                                 "heading_path": (m.get("heading_path") or "").strip(),
                                 "chunk_keys": [str(key)]}
            order.append(str(key))

    chunks: list[dict] = []
    for key in order:
        c = parents[key]
        for _so, sk, st in sorted(subs.get(key, [])):
            if st:
                c["text"] = (c["text"] + "\n\n" + st).strip()
                c["chunk_keys"].append(sk)
        if c["text"]:
            chunks.append(c)
    # sub-group mồ côi (parent không nằm trong selection) → chunk logic riêng
    for pk, group in subs.items():
        if pk in parents:
            continue
        group = sorted(group)
        text = "\n\n".join(t for _o, _k, t in group if t).strip()
        if text:
            chunks.append({"key": pk, "text": text, "heading_path": "",
                           "chunk_keys": [k for _o, k, t in group if t]})

    return {"title": _title_for(source_names), "sources": sorted(wanted),
            "chunks": chunks, "tree_sections": _load_tree_sections(wanted)}
