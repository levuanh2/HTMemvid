"""CRAG — viết lại câu hỏi để tăng khả năng truy hồi (nhánh ambiguous/wrong)."""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

_SYS = (
    "Bạn là trợ lý viết lại câu hỏi để tăng khả năng truy hồi tài liệu. "
    "Viết lại câu hỏi của người dùng rõ ràng hơn, bổ sung từ khóa/đồng nghĩa quan trọng, "
    "giữ nguyên ý định. Chỉ trả về câu hỏi đã viết lại, không giải thích."
)


def rewrite_query(question: str, *, feature: str = "chat") -> str:
    """Viết lại câu hỏi bằng LLM. Trả về câu gốc nếu kết quả rỗng/không đổi.

    Lazy-import get_llm để test có thể monkeypatch app.clients.llm_factory.get_llm.
    """
    q = (question or "").strip()
    if not q:
        return question

    from app.clients.llm_factory import get_llm, lc_ai_message_text

    llm = get_llm(feature=feature)
    out = llm.invoke([SystemMessage(content=_SYS), HumanMessage(content=q)], stream=False)
    rewritten = (lc_ai_message_text(out) or "").strip()
    if not rewritten or rewritten.lower() == q.lower():
        return question
    return rewritten
