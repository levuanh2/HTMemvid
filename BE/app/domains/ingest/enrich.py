"""
Làm giàu chunk (tầng Enriched của tháp chất lượng).

- attach_metadata: gán source/category/date/language/heading_path/page (rẻ, không LLM).
- contextualize: chèn 1 câu định vị đầu chunk (Anthropic contextual retrieval) — gate CONTEXTUAL_EMBEDDINGS.
- hypothetical_qa: sinh câu hỏi giả định đính kèm trước khi vector hóa — gate HYPO_QA.
- enrich_chunk: gộp các bước, trả (final_text, metadata).

LLM được inject qua tham số `ask` (mặc định llm_factory.ask_ai, đã route qua llm-gateway
khi LLM_GATEWAY_ADDR set) để dễ test offline.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Tuple

from shared.config import get_settings


def _default_ask(prompt: str, system_prompt: Optional[str] = None, **kw: Any) -> str:
    from app.clients.llm_factory import ask_ai

    return ask_ai(prompt, system_prompt=system_prompt, feature="summary")


def _detect_language(text: str) -> str:
    try:
        from langdetect import detect

        return detect(text)
    except Exception:
        return "vi"


def attach_metadata(
    chunk_text: str,
    *,
    source: str,
    heading_path: str = "",
    page: Any = None,
    file_path: Optional[str] = None,
    doc_category: Optional[str] = None,
) -> dict:
    """Metadata tối thiểu cho 1 chunk 'sạch' (cho phép lọc theo miền/ngôn ngữ/ngày)."""
    if file_path and os.path.exists(file_path):
        date = datetime.fromtimestamp(os.path.getmtime(file_path), timezone.utc).isoformat()
    else:
        date = datetime.now(timezone.utc).isoformat()
    return {
        "source": source,
        "category": doc_category or get_settings().doc_category,
        "date": date,
        "language": _detect_language(chunk_text or ""),
        "heading_path": heading_path or "",
        "page": page,
    }


def contextualize(
    chunk_text: str,
    doc_context: str = "",
    *,
    ask: Optional[Callable[..., str]] = None,
) -> str:
    """Chèn 1 câu định vị đầu chunk nếu bật CONTEXTUAL_EMBEDDINGS; nếu không, trả nguyên."""
    if not get_settings().contextual_embeddings:
        return chunk_text
    ask = ask or _default_ask
    prompt = (
        "Cho ngữ cảnh tài liệu và một đoạn trích. Viết DUY NHẤT một câu ngắn (tiếng Việt) "
        "định vị đoạn trích trong tài liệu để khi đứng một mình vẫn rõ nghĩa. "
        "Chỉ trả về câu đó, không thêm gì.\n\n"
        f"<tài liệu>\n{(doc_context or '')[:2000]}\n</tài liệu>\n\n"
        f"<đoạn trích>\n{chunk_text[:1500]}\n</đoạn trích>"
    )
    try:
        sentence = (ask(prompt) or "").strip()
    except Exception:
        sentence = ""
    if not sentence:
        return chunk_text
    return f"{sentence}\n\n{chunk_text}"


def hypothetical_qa(
    chunk_text: str,
    *,
    ask: Optional[Callable[..., str]] = None,
) -> str:
    """Sinh 2-3 câu hỏi giả định mà đoạn trả lời được (thu hẹp vocab gap). '' nếu tắt."""
    if not get_settings().hypo_qa:
        return ""
    ask = ask or _default_ask
    prompt = (
        "Dựa trên đoạn văn sau, liệt kê 2-3 câu hỏi (tiếng Việt) mà đoạn này trả lời được. "
        "Mỗi câu hỏi một dòng, bắt đầu bằng '- '. Chỉ trả về danh sách câu hỏi.\n\n"
        f"{chunk_text[:1800]}"
    )
    try:
        qa = (ask(prompt) or "").strip()
    except Exception:
        qa = ""
    if not qa:
        return ""
    return "Câu hỏi liên quan:\n" + qa


def enrich_chunk(
    chunk_text: str,
    *,
    source: str,
    heading_path: str = "",
    file_path: Optional[str] = None,
    doc_context: str = "",
    doc_category: Optional[str] = None,
    ask: Optional[Callable[..., str]] = None,
) -> Tuple[str, dict]:
    """Trả (text đã làm giàu để embed/lưu, metadata)."""
    text2 = contextualize(chunk_text, doc_context, ask=ask)
    qa = hypothetical_qa(chunk_text, ask=ask)
    final = text2 + (("\n\n" + qa) if qa else "")
    meta = attach_metadata(
        final or chunk_text,
        source=source,
        heading_path=heading_path,
        file_path=file_path,
        doc_category=doc_category,
    )
    return final, meta
