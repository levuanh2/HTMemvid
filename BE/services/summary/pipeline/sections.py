"""Stage 0 — dựng danh sách section từ skeleton mindmap (deterministic, 0-1 LLM call)."""
from __future__ import annotations

from services.mindmap.pipeline.enrich import descendant_refs
from services.mindmap.pipeline.skeleton import build_skeleton


def _flatten(nodes: list[dict]) -> list[dict]:
    """Section top-level + toàn bộ chunk_refs con cháu (đúng cách enrich gom context)."""
    root = next((n for n in nodes if n.get("kind") == "root"), None)
    if root is None:
        return []
    out = []
    branches = [n for n in nodes if n.get("parent") == root["id"] and n.get("kind") == "section"]
    for i, b in enumerate(sorted(branches, key=lambda n: n.get("order", 0))):
        refs: list[str] = []
        for k in descendant_refs(b["id"], nodes):
            k = str(k)
            if k not in refs:
                refs.append(k)
        out.append({"id": b["id"], "title": b["title"], "chunk_refs": refs, "order": i})
    return out


def build_sections(mm_input: dict, *, outline_fn=None) -> tuple[list[dict], str]:
    """Trả (sections, method). method="single" = không có cấu trúc → 1 section toàn doc
    (caller đánh dấu degraded "skeleton").

    outline_fn: callable(mm_input) -> nodes|None — LLM outline fallback do factory
    inject (giữ module này 0-LLM, test không cần monkeypatch ask_ai).
    """
    nodes, method = build_skeleton(mm_input)
    if method == "single" and outline_fn is not None:
        try:
            outlined = outline_fn(mm_input)
        except Exception:
            outlined = None
        if outlined:
            nodes, method = outlined, "llm_outline"
    sections = _flatten(nodes)
    if sections:
        return sections, method
    # root-only: 1 section ôm toàn bộ chunk — vẫn tóm tắt được, khai degraded ở caller
    all_refs: list[str] = []
    for c in mm_input.get("chunks") or []:
        for k in c.get("chunk_keys") or []:
            k = str(k)
            if k not in all_refs:
                all_refs.append(k)
    title = (mm_input.get("title") or "Tài liệu").strip()
    return [{"id": "s1", "title": title, "chunk_refs": all_refs, "order": 0}], "single"
