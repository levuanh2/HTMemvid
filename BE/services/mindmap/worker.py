"""Minimal helpers kept after the skeleton-first mindmap pipeline migration."""
from __future__ import annotations

import contextvars
from typing import Callable, Optional

from services.mindmap.jsonrepair import repair_json_text as _repair_json_text
from shared.source_id import canonical_source_stem

_mindmap_job_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mindmap_job_id", default=None
)


def collect_chunks_for_sources(meta: dict, source_names: list) -> list:
    """Collect chunks for the selected sources using canonical source stems."""
    wanted = {canonical_source_stem(s) for s in (source_names or []) if (s or "").strip()}
    wanted.discard("")
    out: list = []
    for key, entry in (meta or {}).items():
        if not isinstance(entry, dict):
            continue
        stem = canonical_source_stem(entry.get("source_stem") or entry.get("video") or "")
        if not stem or stem not in wanted:
            continue
        from app.domains.vectorstore import chunk_text_store

        text = (chunk_text_store.get_text(int(key)) or "").strip() or (entry.get("text") or "").strip()
        out.append(
            {
                "text": text,
                "parent_id": entry.get("parent_id"),
                "sub_order": entry.get("sub_order"),
                "total_parts": entry.get("total_parts"),
                "is_subchunk": entry.get("is_subchunk", False),
                "key": key,
                "embedding": entry.get("embedding"),
            }
        )
    return out


def attach_mindmap_job_context(job_id: Optional[str]) -> None:
    _mindmap_job_id_ctx.set(job_id)


def _notify_progress(
    progress_cb: Optional[Callable[[int], None]],
    p: int,
    msg_vi: str,
) -> None:
    if progress_cb is not None:
        progress_cb(int(p))
    jid = _mindmap_job_id_ctx.get(None)
    if jid:
        try:
            from app.domains.jobs.jobs_store import update_job

            update_job(jid, progress=int(p), current_node=msg_vi)
        except Exception:
            pass
