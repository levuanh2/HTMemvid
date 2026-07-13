"""SQLite store for conversation turns (Conversation Context Layer).

Mirrors the jobs_store template: WAL + synchronous=NORMAL + busy_timeout, lazy
idempotent init_db(), CONVERSATIONS_DB_PATH env override → else DATA_DIR (shared
`/app/memory` volume in Docker so web + RQ worker agree). No migration framework —
schema evolves via CREATE TABLE IF NOT EXISTS.

Two tables:
- conversations         : one row per conversation_id (== the query session_id)
- conversation_messages : one row per turn, with source-scope + debug metadata

Every store call is best-effort at the caller; here we keep the API simple and let
callers wrap in try/except so a DB failure never breaks /query (fail-open).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()


def _data_dir() -> Path:
    base = (os.environ.get("DATA_DIR") or "").strip()
    if base:
        return Path(base)
    from shared.paths import BE_ROOT
    return BE_ROOT


def db_path() -> Path:
    override = (os.environ.get("CONVERSATIONS_DB_PATH") or "").strip()
    if override:
        return Path(override)
    return _data_dir() / "conversations.sqlite"


def get_conn() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id     TEXT PRIMARY KEY,
                    created_at          REAL NOT NULL,
                    updated_at          REAL NOT NULL,
                    context_reset_at    REAL,
                    deleted_at          REAL,
                    title               TEXT,
                    active_source_scope TEXT,
                    user_id             TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id          TEXT PRIMARY KEY,
                    conversation_id     TEXT NOT NULL,
                    role                TEXT NOT NULL,
                    content             TEXT NOT NULL,
                    created_at          REAL NOT NULL,
                    selected_source_ids TEXT,
                    source_context_hash TEXT,
                    cited_chunk_ids     TEXT,
                    rewritten_query     TEXT,
                    answer_summary      TEXT,
                    metadata_json       TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cmsg_conv_created "
                "ON conversation_messages(conversation_id, created_at)"
            )
            _ensure_columns(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, conversation_id)"
            )
            conn.commit()
        finally:
            conn.close()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    # Additive migration (Auth Hardening Phase A): owner column, nullable.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT")


# ---- config helpers (mirror sessions_store env knobs) -----------------------

def _ttl_hours() -> int:
    try:
        return int(os.environ.get("CONVERSATION_TTL_HOURS", os.environ.get("SESSION_TTL_HOURS", "24")))
    except (TypeError, ValueError):
        return 24


def _max_messages() -> int:
    try:
        return int(os.environ.get("CONVERSATION_MAX_MESSAGES", os.environ.get("SESSION_MAX_MESSAGES", "80")))
    except (TypeError, ValueError):
        return 80


def _dumps(v: Any) -> Optional[str]:
    if v is None:
        return None
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return None


def _loads(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


# ---- conversations ----------------------------------------------------------

def ensure_conversation(conversation_id: str, *, active_source_scope: Any = None,
                        user_id: Optional[str] = None, enforce_owner: bool = False) -> bool:
    """Create the conversation row if absent (idempotent). Bumps updated_at.

    First writer establishes ownership: user_id is set on INSERT and preserved
    (COALESCE) on conflict — an existing owner is never overwritten. When
    enforce_owner is True and the row already belongs to a different (or NULL)
    owner, nothing is mutated and False is returned (denied). Returns True when the
    row is created/confirmed for this caller (always True when not enforcing)."""
    if not conversation_id:
        return False
    init_db()
    now = time.time()
    scope = _dumps(active_source_scope)
    with _lock:
        conn = get_conn()
        try:
            if _owner_blocks(conn, conversation_id, user_id, enforce_owner):
                return False
            conn.execute(
                """
                INSERT INTO conversations(conversation_id, created_at, updated_at, active_source_scope, user_id)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    active_source_scope=COALESCE(excluded.active_source_scope, conversations.active_source_scope),
                    user_id=COALESCE(conversations.user_id, excluded.user_id)
                """,
                (conversation_id, now, now, scope, user_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def get_conversation(conversation_id: str) -> Optional[dict]:
    if not conversation_id:
        return None
    init_db()
    with _lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT conversation_id, created_at, updated_at, context_reset_at, deleted_at, "
                "title, active_source_scope, user_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return {
        "conversation_id": row[0],
        "created_at": row[1],
        "updated_at": row[2],
        "context_reset_at": row[3],
        "deleted_at": row[4],
        "title": row[5],
        "active_source_scope": _loads(row[6], None),
        "user_id": row[7] if len(row) > 7 else None,
    }


def _owner_blocks(conn: sqlite3.Connection, conversation_id: str, user_id: Optional[str], enforce: bool) -> bool:
    """True when an owner-enforced op must be denied: the conversation exists and
    its user_id differs from the caller (a legacy NULL owner is also denied under
    enforcement — fail-closed). Absent row → not blocked (create-type ops may
    establish ownership). No-op when enforce is False."""
    if not enforce:
        return False
    row = conn.execute(
        "SELECT user_id FROM conversations WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    if row is None:
        return False
    return row[0] != user_id


def owner_check(conversation_id: str, user_id: Optional[str]) -> Optional[bool]:
    """None if the conversation is absent; True if owned by user_id; False if it
    exists with a different (or NULL) owner. Used by routes to map owner-mismatch
    → 404 without an existence oracle."""
    if not conversation_id:
        return None
    init_db()
    with _lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT user_id FROM conversations WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    return row[0] == user_id


def set_context_reset(conversation_id: str, ts: Optional[float] = None,
                      *, user_id: Optional[str] = None, enforce_owner: bool = False) -> Optional[float]:
    """Set context_reset_at = ts (default now). Returns the timestamp used, or None
    when owner-enforced and the caller is not the owner (denied, no mutation)."""
    if not conversation_id:
        return 0.0
    init_db()
    now = time.time()
    reset_at = float(ts) if ts is not None else now
    with _lock:
        conn = get_conn()
        try:
            if _owner_blocks(conn, conversation_id, user_id, enforce_owner):
                return None
            conn.execute(
                """
                INSERT INTO conversations(conversation_id, created_at, updated_at, context_reset_at, user_id)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    context_reset_at=excluded.context_reset_at,
                    user_id=COALESCE(conversations.user_id, excluded.user_id)
                """,
                (conversation_id, now, now, reset_at, user_id),
            )
            conn.commit()
        finally:
            conn.close()
    return reset_at


def soft_delete(conversation_id: str, *, user_id: Optional[str] = None, enforce_owner: bool = False) -> None:
    if not conversation_id:
        return
    init_db()
    now = time.time()
    with _lock:
        conn = get_conn()
        try:
            if _owner_blocks(conn, conversation_id, user_id, enforce_owner):
                return
            conn.execute(
                "UPDATE conversations SET deleted_at = ?, updated_at = ? WHERE conversation_id = ?",
                (now, now, conversation_id),
            )
            conn.commit()
        finally:
            conn.close()


# ---- messages ---------------------------------------------------------------

def append_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    selected_source_ids: Any = None,
    source_context_hash: Optional[str] = None,
    cited_chunk_ids: Any = None,
    rewritten_query: Optional[str] = None,
    answer_summary: Optional[str] = None,
    metadata: Any = None,
    user_id: Optional[str] = None,
    enforce_owner: bool = False,
) -> Optional[str]:
    """Append one turn. Returns message_id, or None if skipped (empty role/content),
    or None when owner-enforced and the conversation belongs to a different owner."""
    if not conversation_id:
        return None
    role = (role or "").strip()
    content = (content or "").strip()
    if not role or not content:
        return None
    init_db()
    # Establish/confirm ownership before writing; denied → do not append.
    if not ensure_conversation(conversation_id, user_id=user_id, enforce_owner=enforce_owner):
        return None
    now = time.time()
    message_id = uuid.uuid4().hex
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT INTO conversation_messages(
                    message_id, conversation_id, role, content, created_at,
                    selected_source_ids, source_context_hash, cited_chunk_ids,
                    rewritten_query, answer_summary, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id, conversation_id, role, content, now,
                    _dumps(selected_source_ids), source_context_hash, _dumps(cited_chunk_ids),
                    rewritten_query, answer_summary, _dumps(metadata),
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )
            # Cap growth: drop oldest rows beyond the per-conversation cap.
            cap = _max_messages()
            if cap > 0:
                conn.execute(
                    """
                    DELETE FROM conversation_messages
                    WHERE conversation_id = ? AND message_id NOT IN (
                        SELECT message_id FROM conversation_messages
                        WHERE conversation_id = ?
                        ORDER BY created_at DESC, rowid DESC
                        LIMIT ?
                    )
                    """,
                    (conversation_id, conversation_id, cap),
                )
            conn.commit()
        finally:
            conn.close()
    return message_id


def get_messages(
    conversation_id: str,
    *,
    after_ts: Optional[float] = None,
    limit: Optional[int] = None,
    user_id: Optional[str] = None,
    enforce_owner: bool = False,
) -> list[dict]:
    """Return messages for a conversation, oldest first. after_ts filters created_at
    > after_ts. Owner-enforced + not the owner → empty (never another user's data)."""
    if not conversation_id:
        return []
    if enforce_owner and owner_check(conversation_id, user_id) is not True:
        return []  # foreign or absent → no data
    init_db()
    sql = (
        "SELECT message_id, conversation_id, role, content, created_at, selected_source_ids, "
        "source_context_hash, cited_chunk_ids, rewritten_query, answer_summary, metadata_json "
        "FROM conversation_messages WHERE conversation_id = ?"
    )
    params: list[Any] = [conversation_id]
    if after_ts is not None:
        sql += " AND created_at > ?"
        params.append(float(after_ts))
    sql += " ORDER BY created_at ASC, rowid ASC"
    if limit is not None and limit > 0:
        # newest N, then re-sorted ascending below
        sql = sql.replace("ASC, rowid ASC", "DESC, rowid DESC") + " LIMIT ?"
        params.append(int(limit))
    with _lock:
        conn = get_conn()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    out = [
        {
            "message_id": r[0],
            "conversation_id": r[1],
            "role": r[2],
            "content": r[3],
            "created_at": r[4],
            "selected_source_ids": _loads(r[5], None),
            "source_context_hash": r[6],
            "cited_chunk_ids": _loads(r[7], None),
            "rewritten_query": r[8],
            "answer_summary": r[9],
            "metadata": _loads(r[10], None),
        }
        for r in rows
    ]
    if limit is not None and limit > 0:
        out.reverse()  # DESC fetch → back to ascending
    return out


def delete_messages(conversation_id: str, *, user_id: Optional[str] = None, enforce_owner: bool = False) -> int:
    """Hard-delete all messages for a conversation. Returns rows removed.
    Owner-enforced + a different owner → 0 (nothing deleted)."""
    if not conversation_id:
        return 0
    init_db()
    with _lock:
        conn = get_conn()
        try:
            if _owner_blocks(conn, conversation_id, user_id, enforce_owner):
                return 0
            cur = conn.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()


def cleanup_expired() -> None:
    """Delete conversations (and their messages) untouched past the TTL."""
    ttl_h = _ttl_hours()
    if ttl_h <= 0:
        return
    init_db()
    cutoff = time.time() - ttl_h * 3600
    with _lock:
        conn = get_conn()
        try:
            stale = [
                r[0]
                for r in conn.execute(
                    "SELECT conversation_id FROM conversations WHERE updated_at < ?", (cutoff,)
                ).fetchall()
            ]
            for cid in stale:
                conn.execute("DELETE FROM conversation_messages WHERE conversation_id = ?", (cid,))
            conn.execute("DELETE FROM conversations WHERE updated_at < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()
