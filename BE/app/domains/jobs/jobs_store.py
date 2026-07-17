from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
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
    # Phase 5: JOBS_DB_PATH lets web + RQ worker share ONE jobs.sqlite on a mounted
    # volume (e.g. /app/memory/jobs.sqlite). Unset -> legacy DATA_DIR path (dev/tests).
    override = (os.environ.get("JOBS_DB_PATH") or "").strip()
    if override:
        return Path(override)
    return _data_dir() / "jobs.sqlite"


def get_conn() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # busy_timeout: tolerate cross-process/-container writers on the shared DB.
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _ensure_job_columns(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(jobs)")
    cols = {row[1] for row in cur.fetchall()}
    if "token_buffer" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN token_buffer TEXT DEFAULT ''")
    if "cancel_requested" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INT DEFAULT 0")
    # Auth Hardening Phase A: owner column, nullable (unenforced this phase).
    if "user_id" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN user_id TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_user ON jobs(user_id)")


def init_db() -> None:
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id       TEXT PRIMARY KEY,
                    job_type     TEXT,
                    status       TEXT,
                    progress     INT DEFAULT 0,
                    current_node TEXT,
                    created_at   TEXT,
                    updated_at   TEXT,
                    result_json  TEXT,
                    error_text   TEXT
                );
                """
            )
            _ensure_job_columns(conn)
            conn.commit()
        finally:
            conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(job_id: str, job_type: str, status: str = "pending", progress: int = 0, current_node: str = "", user_id: Optional[str] = None) -> None:
    init_db()
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO jobs(job_id, job_type, status, progress, current_node, created_at, updated_at, user_id)
                VALUES(?,?,?,?,?,?,?,?)
                """,
                (job_id, job_type, status, int(progress), current_node, _now(), _now(), user_id),
            )
            conn.commit()
        finally:
            conn.close()


def update_job(job_id: str, **kwargs: Any) -> None:
    """
    Atomic update.
    Allowed keys: job_type, status, progress, current_node, result_json/result(dict), error_text
    """
    if not kwargs:
        return
    init_db()

    if kwargs.get("status") == "error":
        et = kwargs.get("error_text")
        if et is None or (isinstance(et, str) and not et.strip()):
            kwargs["error_text"] = "Lỗi job không có chi tiết (server)."

    fields: list[str] = []
    values: list[Any] = []

    if "result" in kwargs and "result_json" not in kwargs:
        kwargs["result_json"] = json.dumps(kwargs.pop("result"), ensure_ascii=False)

    for k in ("job_type", "status", "progress", "current_node", "result_json", "error_text", "token_buffer"):
        if k in kwargs:
            fields.append(f"{k}=?")
            values.append(kwargs[k])

    fields.append("updated_at=?")
    values.append(_now())

    values.append(job_id)

    with _lock:
        conn = get_conn()
        try:
            conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE job_id=?", values)
            conn.commit()
        finally:
            conn.close()


def get_job(job_id: str) -> Optional[dict]:
    init_db()
    with _lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "SELECT job_id, job_type, status, progress, current_node, created_at, updated_at, result_json, error_text, token_buffer, cancel_requested, user_id FROM jobs WHERE job_id=?",
                (job_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            result_val = None
            if row[7]:
                try:
                    result_val = json.loads(row[7])
                except Exception:
                    result_val = None
            job = {
                "job_id": row[0],
                "job_type": row[1],
                "status": row[2],
                "progress": row[3],
                "current_node": row[4],
                "created_at": row[5],
                "updated_at": row[6],
                "result": result_val,
                "error": row[8],
                "token_buffer": row[9] if len(row) > 9 and row[9] is not None else "",
                "cancel_requested": bool(row[10]) if len(row) > 10 and row[10] is not None else False,
                "user_id": row[11] if len(row) > 11 else None,
            }
            return job
        finally:
            conn.close()


def request_cancel(job_id: str) -> None:
    """Cancel hợp tác: set cờ cho executor đang sống ack giữa các node. Job KHÔNG còn
    executor (pending trong queue, hoặc interrupted sau restart) chuyển THẲNG sang
    'cancelled' — không ai ack cờ, FE sẽ poll vô hạn ("Đang huỷ…" kẹt mãi).
    Trạng thái terminal (done/error/cancelled) giữ nguyên → idempotent, cancel job đã
    xong là no-op an toàn."""
    init_db()
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                UPDATE jobs SET cancel_requested=1,
                       status = CASE WHEN status IN ('pending','interrupted')
                                     THEN 'cancelled' ELSE status END,
                       current_node = CASE WHEN status IN ('pending','interrupted')
                                     THEN 'Cancelled' ELSE current_node END,
                       updated_at=?
                WHERE job_id=?
                """,
                (_now(), job_id),
            )
            conn.commit()
        finally:
            conn.close()


def is_cancel_requested(job_id: str) -> bool:
    init_db()
    with _lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "SELECT cancel_requested FROM jobs WHERE job_id=?",
                (job_id,),
            )
            row = cur.fetchone()
            if not row or row[0] is None:
                return False
            return bool(row[0])
        finally:
            conn.close()


def clear_token_buffer(job_id: str) -> None:
    """Xóa buffer streaming trước khi chạy query job mới."""
    init_db()
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE jobs SET token_buffer='', updated_at=? WHERE job_id=?",
                (_now(), job_id),
            )
            conn.commit()
        finally:
            conn.close()


def append_token(job_id: str, token: str) -> None:
    """Gắn thêm token vào buffer (SSE đọc incremental)."""
    if token is None:
        return
    s = str(token)
    if not s:
        return
    init_db()
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                UPDATE jobs SET token_buffer = COALESCE(token_buffer, '') || ?, updated_at=?
                WHERE job_id=?
                """,
                (s, _now(), job_id),
            )
            conn.commit()
        finally:
            conn.close()


def mark_interrupted_jobs() -> None:
    """
    Khi process bị kill, mark các job đang running/pending sang interrupted.
    (Single-process behaviour. Trong queue mode dùng reconcile_interrupted() ở
    app/jobs/queue.py để KHÔNG mark nhầm job worker còn sống.)
    """
    init_db()
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                UPDATE jobs
                SET status='interrupted', updated_at=?
                WHERE status IN ('pending','running','processing')
                """,
                (_now(),),
            )
            conn.commit()
        finally:
            conn.close()


def list_active_jobs() -> list[tuple[str, str]]:
    """(job_id, job_type) cho các job chưa terminal — dùng cho reconcile queue-aware."""
    init_db()
    with _lock:
        conn = get_conn()
        try:
            cur = conn.execute(
                "SELECT job_id, job_type FROM jobs WHERE status IN ('pending','running','processing')"
            )
            return [(row[0], row[1]) for row in cur.fetchall()]
        finally:
            conn.close()


def migrate_from_dict(jobs_dict: dict, job_type: str = "ingest") -> None:
    """
    Chạy 1 lần khi startup để migrate jobs{} cũ sang SQLite (idempotent).
    jobs_dict shape có thể khác nhau; ta chỉ map các trường chung.
    """
    if not isinstance(jobs_dict, dict) or not jobs_dict:
        init_db()
        return

    init_db()
    for jid, j in list(jobs_dict.items()):
        try:
            status = (j or {}).get("status") or "pending"
            progress = int((j or {}).get("progress") or 0)
            current_node = (j or {}).get("current_node") or ""
            result = (j or {}).get("result")
            err = (j or {}).get("error")
            create_job(jid, job_type=job_type, status=status, progress=progress, current_node=current_node)
            update_job(jid, result=result, error_text=err)
        except Exception:
            # best-effort migrate; không crash app
            continue

