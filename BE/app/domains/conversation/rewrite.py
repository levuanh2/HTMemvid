"""Conversational follow-up rewrite (Conversation Context Layer, Phase C).

Turns a follow-up question that leans on the conversation ("nó là gì", "phần đó
nói kỹ hơn", "so sánh cái này với cái kia") into a standalone question for
retrieval, using the recent source-scoped context. JSON-only prompt, tolerant
parse, fail-open: any error keeps the original question with low confidence.

The rewritten question is used for RETRIEVAL only; the original question is kept
for answer generation, and the scoped conversation history resolves the reference
in the answer prompt (so the answer system prompt is unchanged — no PROMPT_VERSION
bump in this phase).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

_SYS = (
    "Bạn là bộ viết lại câu hỏi cho hệ thống hỏi-đáp tài liệu. Nhiệm vụ: biến câu hỏi "
    "follow-up (có thể chứa \"nó\", \"phần đó\", \"ý trên\", \"cái này\", \"cái kia\", "
    "\"cái đầu\", \"cái thứ hai\") thành MỘT câu hỏi độc lập, đầy đủ ngữ cảnh, dựa trên "
    "các lượt trò chuyện gần đây.\n\n"
    "Quy tắc:\n"
    "- Nếu câu đã độc lập, giữ gần như nguyên văn (needs_context=false).\n"
    "- Chỉ dùng ngữ cảnh trò chuyện để giải nghĩa tham chiếu; KHÔNG bịa dữ kiện tài liệu.\n"
    "- Nếu không đủ ngữ cảnh để giải nghĩa, đặt needs_context=false và giữ câu gốc.\n"
    "- confidence là độ tin (0.0–1.0) rằng câu viết lại đúng ý người dùng.\n"
    "- CHỈ trả về JSON, không giải thích ngoài JSON.\n\n"
    "Ví dụ:\n"
    "Ngữ cảnh: [assistant] File nói về Phase 5 RQ worker.\n"
    "Câu hỏi: Nó giải quyết vấn đề gì?\n"
    "JSON: {\"standalone_question\": \"Phase 5 RQ worker trong tài liệu giải quyết vấn đề gì?\", "
    "\"needs_context\": true, \"refers_to_previous_answer\": true, \"confidence\": 0.9, "
    "\"reason\": \"'nó' trỏ tới Phase 5 RQ worker ở câu trả lời trước.\"}\n\n"
    "Ngữ cảnh: [assistant] Có 3 queue chính: ingest, summary, mindmap.\n"
    "Câu hỏi: so sánh cái đầu với cái thứ hai\n"
    "JSON: {\"standalone_question\": \"So sánh queue ingest với queue summary trong kiến trúc "
    "MemVid.\", \"needs_context\": true, \"refers_to_previous_answer\": true, \"confidence\": 0.85, "
    "\"reason\": \"'cái đầu' = ingest, 'cái thứ hai' = summary theo câu trước.\"}"
)


def _default(question: str, reason: str = "no_context", confidence: float = 0.0) -> dict:
    return {
        "standalone_question": question,
        "needs_context": False,
        "refers_to_previous_answer": False,
        "confidence": confidence,
        "reason": reason,
    }


def _confidence_threshold() -> float:
    try:
        return float(os.getenv("CONVERSATION_REWRITE_MIN_CONFIDENCE", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def _render_context(recent_context: Any, max_turns: int = 6) -> str:
    turns = []
    if recent_context is None:
        return ""
    if hasattr(recent_context, "turns"):
        turns = recent_context.turns or []
    elif isinstance(recent_context, dict):
        turns = recent_context.get("turns") or []
    lines = []
    for t in turns[-max_turns:]:
        role = t.get("role") or "user"
        content = (t.get("content") or "").strip().replace("\n", " ")
        if content:
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def _parse_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from an LLM response, tolerating prose/fences."""
    if not text:
        return None
    s = text.strip()
    # Strip common markdown code fences.
    s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.MULTILINE).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except Exception:
            return None
    return None


def rewrite_followup_question(
    current_question: str,
    recent_context: Any = None,
    selected_sources: Optional[list] = None,
    *,
    feature: str = "extract",
) -> dict:
    """Return a machine-readable rewrite decision. Fail-open to the original question.

    Lazy-import get_llm so tests can monkeypatch app.clients.llm_factory.get_llm.
    """
    q = (current_question or "").strip()
    if not q:
        return _default(current_question)

    ctx_text = _render_context(recent_context)
    if not ctx_text:
        # No usable context → nothing to resolve; treat as standalone.
        return _default(q, reason="empty_context")

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from app.clients.llm_factory import get_llm, lc_ai_message_text

        human = f"Ngữ cảnh gần đây:\n{ctx_text}\n\nCâu hỏi hiện tại:\n{q}\n\nTrả về JSON:"
        llm = get_llm(feature=feature)
        out = llm.invoke([SystemMessage(content=_SYS), HumanMessage(content=human)], stream=False)
        raw = (lc_ai_message_text(out) or "").strip()
    except Exception:
        return _default(q, reason="llm_error")

    data = _parse_json(raw)
    if not isinstance(data, dict):
        return _default(q, reason="parse_error")

    standalone = str(data.get("standalone_question") or "").strip() or q
    needs_context = bool(data.get("needs_context"))
    refers = bool(data.get("refers_to_previous_answer"))
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(data.get("reason") or "")

    # Low confidence or no context needed → keep the original question for retrieval.
    if not needs_context or confidence < _confidence_threshold():
        standalone = q

    return {
        "standalone_question": standalone,
        "needs_context": needs_context,
        "refers_to_previous_answer": refers,
        "confidence": confidence,
        "reason": reason,
    }


def decide_context_mode(decision: dict) -> str:
    """Map a rewrite decision to a cache-safety context_mode."""
    if not isinstance(decision, dict) or not decision.get("needs_context"):
        return "standalone"
    try:
        conf = float(decision.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return "contextual" if conf >= _confidence_threshold() else "low_confidence_contextual"
