"""
Chuỗi Q&A từ ngữ cảnh đã retrieve — Phase 4 (thay thế dần summarize_results trong graph).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.clients.llm_factory import get_llm, lc_ai_message_text, stream_chat_tokens

def history_to_lc_messages(history: Optional[List[Dict[str, Any]]]) -> list:
    out = []
    for m in history or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").strip().lower()
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        if role in ("user", "human"):
            out.append(HumanMessage(content=content))
        elif role in ("assistant", "ai"):
            out.append(AIMessage(content=content))
    return out


def _qa_messages(question: str, context_text: str, *, history: Optional[List[Dict[str, Any]]]) -> list:
    # Tier 1 (prompt/prefix cache): thứ tự message CỐ ĐỊNH static-first —
    # System(instruction + RAG context) → history → câu hỏi hiện tại.
    # Phần đầu ổn định giữa các request cùng context giúp provider tái dùng
    # KV/prefix cache (Ollama KV reuse, Gemini implicit caching) → giảm TTFT.
    # ĐỔI system prompt này → bump llm_cache.PROMPT_VERSION
    # (app/domains/cache/llm_cache.py) để vô hiệu semantic cache cũ.
    sys_txt = (
        "Bạn là trợ lý nghiên cứu. Chỉ dùng thông tin trong phần Context; "
        "nếu không đủ, nói rõ — không bịa.\n\n"
        f"Context:\n{context_text}"
    )
    return [SystemMessage(content=sys_txt), *history_to_lc_messages(history), HumanMessage(content=question)]


def answer_with_document_context(
    question: str,
    context_text: str,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    feature: str = "chat",
) -> str:
    """
    Trả lời dựa trên một khối context (đã gồm citation nếu có).
    """
    llm = get_llm(feature=feature)
    msgs = _qa_messages(question, context_text, history=history)
    out = llm.invoke(msgs, stream=False)
    return lc_ai_message_text(out).strip()


def answer_with_document_context_stream(
    question: str,
    context_text: str,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    feature: str = "chat",
):
    """Yield từng chunk text từ LLM.stream (SSE)."""
    llm = get_llm(feature=feature)
    msgs = _qa_messages(question, context_text, history=history)
    yield from stream_chat_tokens(llm, msgs)
