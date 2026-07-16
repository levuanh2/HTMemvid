"""Stage phụ (Summary v3 Phase 2) — source pointer từ metadata chunk. 0 LLM, thuần.

Pointer cho mỗi chunk_id để FE "quay lại đúng đoạn/trang/mục" review — suy DETERMINISTIC
từ metadata mm_input (page/source/heading), KHÔNG hỏi LLM (không bịa nguồn/trang/mục).
heading_path trong mm_input là CHUỖI (ingest join bằng ' > '); pointer tách thành list ở
đây, KHÔNG đổi mm_input (mindmap skeleton còn split chuỗi đó)."""
from __future__ import annotations

_HEADING_SEP = " > "   # khớp separator ingest (chunking._heading_path / skeleton._SEP)


def _chunk_meta_index(mm_input: dict) -> dict[str, dict]:
    """Map MỖI chunk key (kể cả sub-key trong chunk_keys) → metadata của chunk cha.
    setdefault: key trùng giữ lần gặp đầu (chunk cha ghi trước sub)."""
    index: dict[str, dict] = {}
    for c in mm_input.get("chunks") or []:
        meta = {
            "source_id": c.get("source_id"),
            "source_stem": c.get("source_stem"),
            "page": c.get("page"),
            "heading_path": (c.get("heading_path") or "").strip(),
            "chunk_index": c.get("chunk_index"),
        }
        keys = c.get("chunk_keys") or ([c["key"]] if c.get("key") is not None else [])
        for k in keys:
            index.setdefault(str(k), meta)
    return index


def _heading_list(hp: str) -> list[str]:
    return [p.strip() for p in (hp or "").split(_HEADING_SEP) if p.strip()]


def _pointer(chunk_id: str, meta: dict) -> dict:
    hp = _heading_list(meta.get("heading_path") or "")
    return {
        "chunk_id": chunk_id,
        "source_id": meta.get("source_id") or None,
        "source_stem": meta.get("source_stem") or None,
        "page": meta.get("page"),
        # section_title = mục sâu nhất trong heading_path; rỗng → None
        "section_title": hp[-1] if hp else None,
        "heading_path": hp,
        "chunk_index": meta.get("chunk_index"),
    }


def build_pointers(mm_input: dict, chunk_ids: list[str]) -> list[dict]:
    """Trả list pointer theo THỨ TỰ chunk_ids, dedupe giữ lần đầu, BỎ id không có thật
    (không bịa pointer cho id lạ). Metadata thiếu → None (page/source/section_title)."""
    index = _chunk_meta_index(mm_input)
    out: list[dict] = []
    seen: set[str] = set()
    for cid in chunk_ids or []:
        cid = str(cid)
        if cid in seen:
            continue
        meta = index.get(cid)
        if meta is None:
            continue          # id không có trong nguồn → bỏ, không bịa
        seen.add(cid)
        out.append(_pointer(cid, meta))
    return out


def attach_pointers(sections: list[dict], mm_input: dict) -> list[dict]:
    """Gắn section["pointers"] suy từ chunk_refs mỗi section (in-place, trả lại list).
    Section không có ref nào khớp nguồn → pointers=[] (schema bỏ key rỗng ở sanitize)."""
    for s in sections or []:
        s["pointers"] = build_pointers(mm_input, s.get("chunk_refs") or [])
    return sections
