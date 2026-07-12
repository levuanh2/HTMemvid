"""SQLite user store for the auth MVP.

Mirrors the jobs_store template: WAL + synchronous=NORMAL + busy_timeout, lazy
idempotent init_db(), USERS_DB_PATH env override → else DATA_DIR (shared
`/app/memory/users.sqlite` in Docker). Passwords hashed with Werkzeug — no
plaintext is ever stored. Store dicts carry password_hash + token_version for
internal use; the API layer sanitises via auth.service.public_user().
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

_lock = threading.Lock()


class EmailExistsError(Exception):
    """Raised by create_user when the email is already registered."""


def _data_dir() -> Path:
    base = (os.environ.get("DATA_DIR") or "").strip()
    if base:
        return Path(base)
    from shared.paths import BE_ROOT
    return BE_ROOT


def db_path() -> Path:
    override = (os.environ.get("USERS_DB_PATH") or "").strip()
    if override:
        return Path(override)
    return _data_dir() / "users.sqlite"


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
                CREATE TABLE IF NOT EXISTS users (
                    user_id        TEXT PRIMARY KEY,
                    email          TEXT UNIQUE NOT NULL COLLATE NOCASE,
                    password_hash  TEXT NOT NULL,
                    display_name   TEXT,
                    token_version  INTEGER NOT NULL DEFAULT 1,
                    created_at     REAL NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def _norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _row_to_user(row) -> Optional[dict]:
    if not row:
        return None
    return {
        "user_id": row[0],
        "email": row[1],
        "password_hash": row[2],
        "display_name": row[3],
        "token_version": row[4],
        "created_at": row[5],
    }


_COLS = "user_id, email, password_hash, display_name, token_version, created_at"


def create_user(email: str, password: str, display_name: Optional[str] = None) -> dict:
    """Create a user (password hashed). Raises EmailExistsError on duplicate."""
    init_db()
    em = _norm_email(email)
    user_id = uuid.uuid4().hex
    pw_hash = generate_password_hash(password)
    dn = (display_name or "").strip() or None
    now = time.time()
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO users(user_id, email, password_hash, display_name, token_version, created_at) "
                "VALUES(?, ?, ?, ?, 1, ?)",
                (user_id, em, pw_hash, dn, now),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise EmailExistsError(em) from exc
        finally:
            conn.close()
    return {
        "user_id": user_id, "email": em, "password_hash": pw_hash,
        "display_name": dn, "token_version": 1, "created_at": now,
    }


def get_by_email(email: str) -> Optional[dict]:
    init_db()
    em = _norm_email(email)
    with _lock:
        conn = get_conn()
        try:
            row = conn.execute(
                f"SELECT {_COLS} FROM users WHERE email = ? COLLATE NOCASE", (em,)
            ).fetchone()
        finally:
            conn.close()
    return _row_to_user(row)


def get_by_id(user_id: str) -> Optional[dict]:
    if not user_id:
        return None
    init_db()
    with _lock:
        conn = get_conn()
        try:
            row = conn.execute(
                f"SELECT {_COLS} FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        finally:
            conn.close()
    return _row_to_user(row)


def verify_password(email: str, password: str) -> Optional[dict]:
    """Return the user dict on a correct password, else None (no user enumeration)."""
    user = get_by_email(email)
    if not user:
        return None
    if not check_password_hash(user["password_hash"], password or ""):
        return None
    return user


def bump_token_version(user_id: str) -> None:
    """Invalidate all existing tokens for a user (logout-all / password change)."""
    if not user_id:
        return
    init_db()
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE users SET token_version = token_version + 1 WHERE user_id = ?",
                (user_id,),
            )
            conn.commit()
        finally:
            conn.close()
