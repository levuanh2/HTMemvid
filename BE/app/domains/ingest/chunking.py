"""
Cắt chunk theo CẤU TRÚC tài liệu (tầng Structured của tháp chất lượng).

chunk_markdown: tách theo heading (#/##/###) bằng MarkdownHeaderTextSplitter để
giữ ngữ cảnh section, rồi giới hạn kích thước bằng RecursiveCharacterTextSplitter
(có overlap) cho các section quá dài. Mỗi chunk mang theo heading_path để vừa lọc
vừa (tùy chọn) chèn làm ngữ cảnh.
"""

from __future__ import annotations

from typing import List, Optional

from shared.config import get_settings

_HEADERS = [("#", "h1"), ("##", "h2"), ("###", "h3")]


def _heading_path(meta: dict) -> str:
    parts = [meta.get(k) for _, k in _HEADERS if meta.get(k)]
    return " > ".join(p for p in parts if p)


def chunk_markdown(
    md: str,
    *,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> List[dict]:
    """Markdown -> list[{'text', 'heading_path'}], cắt theo heading + cap size+overlap."""
    if not md or not md.strip():
        return []

    s = get_settings()
    size = int(chunk_size if chunk_size is not None else s.chunk_size)
    overlap = int(chunk_overlap if chunk_overlap is not None else s.chunk_overlap)

    from langchain_text_splitters import (
        MarkdownHeaderTextSplitter,
        RecursiveCharacterTextSplitter,
    )

    # Bước 1: tách theo heading (giữ heading trong nội dung để không mất ngữ cảnh).
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=_HEADERS, strip_headers=False
    )
    try:
        sections = header_splitter.split_text(md)
    except Exception:
        sections = []

    # Không có heading nào -> coi toàn bộ là 1 section heading rỗng.
    if not sections:
        from langchain_core.documents import Document

        sections = [Document(page_content=md, metadata={})]

    # Bước 2: cap size từng section, giữ heading_path cho mọi mảnh con.
    size_splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""],
        length_function=len,
    )

    out: List[dict] = []
    for sec in sections:
        hp = _heading_path(getattr(sec, "metadata", {}) or {})
        text = (getattr(sec, "page_content", "") or "").strip()
        if not text:
            continue
        for piece in size_splitter.split_text(text):
            piece = (piece or "").strip()
            if piece:
                out.append({"text": piece, "heading_path": hp})
    return out
