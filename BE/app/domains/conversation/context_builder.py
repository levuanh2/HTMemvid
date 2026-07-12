"""Recent conversation context builder (Conversation Context Layer, Phase B).

Loads recent turns for a conversation, scoped to the current document selection so
context never leaks across unrelated sources, and respecting context_reset_at
("Clear context"). Returns a structured, trimmed context — not a raw blob — that
the rewrite step (Phase C) and the answer prompt consume.

Fail-open: any error returns an empty context; the caller treats that as a
standalone question. A read failure must never break /query.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ConversationContext:
    conversation_id: str
    context_reset_at: Optional[float] = None
    source_scope_match: bool = False
    turns: list[dict] = field(default_factory=list)
    context_signature: str = ""
    is_empty: bool = True

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "context_reset_at": self.context_reset_at,
            "source_scope_match": self.source_scope_match,
            "turns": self.turns,
            "context_signature": self.context_signature,
            "is_empty": self.is_empty,
        }


def _empty(conversation_id: str, reset_at: Optional[float] = None) -> ConversationContext:
    return ConversationContext(conversation_id=conversation_id, context_reset_at=reset_at)


def _norm_sources(ids: Any) -> tuple:
    if not ids:
        return tuple()
    try:
        return tuple(sorted(str(s) for s in ids if s is not None))
    except Exception:
        return tuple()


def _scope_matches(msg: dict, cur_hash: Optional[str], cur_sources: tuple) -> bool:
    """A turn belongs to the current document scope if its cache bucket matches, or
    (when hashes are unavailable) its selected source set matches. If we can't
    establish a match, treat as NON-matching — never leak across documents."""
    mh = msg.get("source_context_hash")
    if cur_hash and mh:
        return mh == cur_hash
    ms = _norm_sources(msg.get("selected_source_ids"))
    if cur_sources or ms:
        return ms == cur_sources
    # No scope signal on either side → cannot prove same document → exclude.
    return False


def build_recent_conversation_context(
    conversation_id: str,
    selected_sources: Optional[list] = None,
    source_context_hash: Optional[str] = None,
    max_turns: int = 6,
    max_chars: int = 8000,
    *,
    user_id: Optional[str] = None,
    enforce_owner: bool = False,
) -> ConversationContext:
    if not conversation_id:
        return _empty(conversation_id)
    try:
        from app.domains.conversation import store as _conv
        # Owner-enforced: a non-owner sees no context (get_messages returns []),
        # so this never reads another user's turns.
        conv = _conv.get_conversation(conversation_id)
        reset_at = conv.get("context_reset_at") if conv else None
        # Only turns created after the last Clear-context.
        msgs = _conv.get_messages(
            conversation_id, after_ts=reset_at, user_id=user_id, enforce_owner=enforce_owner
        )
    except Exception:
        return _empty(conversation_id)

    if not msgs:
        return _empty(conversation_id, reset_at)

    cur_sources = _norm_sources(selected_sources)
    # Keep only same-scope turns (leak prevention).
    scoped = [m for m in msgs if _scope_matches(m, source_context_hash, cur_sources)]
    source_scope_match = bool(scoped)
    if not scoped:
        return ConversationContext(
            conversation_id=conversation_id, context_reset_at=reset_at,
            source_scope_match=False, turns=[], is_empty=True,
        )

    # Newest max_turns, trimmed to max_chars total (assistant answers use answer_summary
    # when present to keep long generated text / citations out of the context window).
    recent = scoped[-max_turns:] if max_turns > 0 else scoped
    turns: list[dict] = []
    total = 0
    for m in reversed(recent):  # newest first while budgeting, re-reversed below
        role = m.get("role") or ""
        if role == "assistant":
            content = m.get("answer_summary") or m.get("content") or ""
        else:
            content = m.get("content") or ""
        content = str(content).strip()
        if not content:
            continue
        if max_chars > 0 and total + len(content) > max_chars:
            content = content[: max(0, max_chars - total)]
        turns.append({
            "role": role,
            "content": content,
            "created_at": m.get("created_at"),
            "selected_source_ids": m.get("selected_source_ids"),
        })
        total += len(content)
        if max_chars > 0 and total >= max_chars:
            break
    turns.reverse()

    if not turns:
        return ConversationContext(
            conversation_id=conversation_id, context_reset_at=reset_at,
            source_scope_match=source_scope_match, turns=[], is_empty=True,
        )

    sig_parts = [str(reset_at or ""), str(source_context_hash or ""), "|".join(cur_sources)]
    for t in turns:
        sig_parts.append(f"{t['role']}:{t['content']}")
    context_signature = hashlib.sha256("\x1f".join(sig_parts).encode("utf-8")).hexdigest()[:16]

    return ConversationContext(
        conversation_id=conversation_id,
        context_reset_at=reset_at,
        source_scope_match=source_scope_match,
        turns=turns,
        context_signature=context_signature,
        is_empty=False,
    )
