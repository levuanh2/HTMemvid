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


# Separator nối các section khi dựng lại doc_text (hệ toạ độ char cho late chunking).
_SECTION_SEP = "\n\n"


def chunk_markdown_spans(
    md: str,
    *,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> "tuple[str, List[dict]]":
    """Markdown -> (doc_text, pieces) cho LATE CHUNKING.

    Trả về:
      - doc_text: văn bản dựng lại từ các section (nối bằng `_SECTION_SEP`) — đây là
        HỆ TOẠ ĐỘ CHAR nhất quán để embed toàn văn rồi mean-pool theo span. (KHÔNG
        dùng `md` gốc vì MarkdownHeaderTextSplitter viết lại khoảng trắng → offset lệch.)
      - pieces: list[{'text','heading_path','start','end'}] với doc_text[start:end] == text.
        Piece không định vị được (hiếm, do splitter chuẩn hoá) → start=end=-1 (caller
        fallback encode riêng cho piece đó).
    """
    if not md or not md.strip():
        return "", []

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
    doc_parts: List[str] = []
    doc_cursor = 0  # độ dài doc_text đã dựng (== offset SEP.join)
    for sec in sections:
        hp = _heading_path(getattr(sec, "metadata", {}) or {})
        sec_text = (getattr(sec, "page_content", "") or "").strip()
        if not sec_text:
            continue
        if doc_parts:
            doc_cursor += len(_SECTION_SEP)
        sec_start = doc_cursor
        doc_parts.append(sec_text)
        doc_cursor += len(sec_text)

        # Định vị mỗi piece trong sec_text bằng con trỏ chạy tiến (xử lý overlap).
        search_from = 0
        for piece in size_splitter.split_text(sec_text):
            piece = (piece or "").strip()
            if not piece:
                continue
            rel = sec_text.find(piece, search_from)
            if rel < 0:  # overlap có thể lùi lại, hoặc splitter chuẩn hoá → thử từ đầu
                rel = sec_text.find(piece)
            if rel < 0:
                start = end = -1
            else:
                start = sec_start + rel
                end = start + len(piece)
                search_from = rel + 1
            out.append({"text": piece, "heading_path": hp, "start": start, "end": end})

    doc_text = _SECTION_SEP.join(doc_parts)
    return doc_text, out


def chunk_markdown(
    md: str,
    *,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> List[dict]:
    """Markdown -> list[{'text','heading_path','start','end'}], cắt theo heading + cap size+overlap.

    Signature cũ (chỉ trả pieces). Late chunking dùng `chunk_markdown_spans` để lấy cả
    doc_text (hệ toạ độ của start/end). start/end ở đây trỏ vào doc_text đó.
    """
    _doc_text, pieces = chunk_markdown_spans(
        md, chunk_size=chunk_size, chunk_overlap=chunk_overlap
    )
    return pieces
