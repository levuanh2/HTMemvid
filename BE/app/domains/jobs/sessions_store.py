from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional


_LOCK = threading.Lock()


def _db_path() -> Path:
    from shared.paths import BE_ROOT
    data_root = Path(os.environ.get("DATA_DIR", str(BE_ROOT)))
    return data_root / "sessions.sqlite"


def init_db() -> None:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        con = sqlite3.connect(str(path), check_same_thread=False)
        try:
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA synchronous=NORMAL;")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    updated_at REAL NOT NULL,
                    history_json TEXT NOT NULL
                )
                """
            )
            con.commit()
        finally:
            con.close()


def _cleanup_expired(con: sqlite3.Connection) -> None:
    ttl_h = int(os.environ.get("SESSION_TTL_HOURS", "24"))
    if ttl_h <= 0:
        return
    cutoff = time.time() - ttl_h * 3600
    con.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))


def get_history(session_id: str, *, limit_messages: int = 8) -> list[dict[str, str]]:
    if not session_id:
        return []
    init_db()
    path = _db_path()
    with _LOCK:
        con = sqlite3.connect(str(path), check_same_thread=False)
        try:
            _cleanup_expired(con)
            row = con.execute(
                "SELECT history_json FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return []
            raw = row[0] or "[]"
            try:
                hist = json.loads(raw)
            except Exception:
                return []
            if not isinstance(hist, list):
                return []
            # Keep only last N messages (role/content dicts)
            out: list[dict[str, str]] = []
            for item in hist[-limit_messages:]:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip()
                content = str(item.get("content") or "").strip()
                if role and content:
                    out.append({"role": role, "content": content})
            return out
        finally:
            con.commit()
            con.close()


def append_messages(session_id: str, messages: list[dict[str, str]]) -> None:
    if not session_id:
        return
    if not messages:
        return
    init_db()
    path = _db_path()
    now = time.time()

    with _LOCK:
        con = sqlite3.connect(str(path), check_same_thread=False)
        try:
            _cleanup_expired(con)
            row = con.execute(
                "SELECT history_json FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                try:
                    hist = json.loads(row[0] or "[]")
                except Exception:
                    hist = []
            else:
                hist = []

            if not isinstance(hist, list):
                hist = []

            for m in messages:
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role") or "").strip()
                content = str(m.get("content") or "").strip()
                if role and content:
                    hist.append({"role": role, "content": content})

            # cap history to avoid unlimited growth
            cap = int(os.environ.get("SESSION_MAX_MESSAGES", "80"))
            if cap > 0 and len(hist) > cap:
                hist = hist[-cap:]

            con.execute(
                """
                INSERT INTO sessions(session_id, updated_at, history_json)
                VALUES(?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    history_json=excluded.history_json
                """,
                (session_id, now, json.dumps(hist, ensure_ascii=False)),
            )
            con.commit()
        finally:
            con.close()

