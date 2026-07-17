from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()

TERMINAL_STATUSES = ("done", "error", "timeout", "cancelled", "interrupted")
# token_buffer bị xóa khi job vào các status này (kết quả cuối đã nằm trong
# result_json). "interrupted" KHÔNG nằm trong tập: HITL/reconcile có thể resume
# và SSE cần buffer để stream tiếp — retention sẽ dọn record interrupted cũ.
_CLEAR_BUFFER_STATUSES = ("done", "error", "timeout", "cancelled")


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

    # Terminal (trừ interrupted — có thể resume): xóa token_buffer TRONG CÙNG UPDATE
    # với status/result (atomic — buffer không bao giờ mất trước khi result được lưu).
    # SSE an toàn: FE lấy answer cuối từ result của status event, token chỉ là preview.
    if kwargs.get("status") in _CLEAR_BUFFER_STATUSES and "token_buffer" not in kwargs:
        kwargs["token_buffer"] = ""

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
                       token_buffer = CASE WHEN status IN ('pending','interrupted')
                                     THEN '' ELSE token_buffer END,
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


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


def _delete_checkpoints_for_threads(job_ids: list[str]) -> None:
    """Xóa checkpoint langgraph (thread_id = job_id) của các job đã prune.
    Best-effort: DB/table vắng → bỏ qua. Chỉ đụng thread đã terminal quá hạn
    — không có timestamp trong schema checkpoint nên đây là đường xóa an toàn."""
    if not job_ids:
        return
    p = _data_dir() / "checkpoints.sqlite"
    if not p.is_file():
        return
    try:
        conn = sqlite3.connect(str(p), timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            ph = ",".join("?" * len(job_ids))
            for table in ("checkpoints", "writes"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE thread_id IN ({ph})", job_ids)
                except sqlite3.OperationalError:
                    pass  # table vắng (schema langgraph đổi) → bỏ qua
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # cleanup không bao giờ được làm hỏng request path


def cleanup_terminal_jobs(retention_days: Optional[int] = None) -> int:
    """Prune job TERMINAL cũ hơn retention (default env JOB_RETENTION_DAYS=7) +
    checkpoint của chúng. Chỉ đụng terminal — running/pending giữ nguyên dù cũ.
    Idempotent, fail-open (lỗi → 0). retention_days <= 0 → tắt."""
    days = retention_days if retention_days is not None else _env_int("JOB_RETENTION_DAYS", 7)
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        init_db()
        with _lock:
            conn = get_conn()
            try:
                ph = ",".join("?" * len(TERMINAL_STATUSES))
                cur = conn.execute(
                    f"SELECT job_id FROM jobs WHERE status IN ({ph}) AND updated_at < ?",
                    (*TERMINAL_STATUSES, cutoff),
                )
                ids = [row[0] for row in cur.fetchall()]
                if ids:
                    idph = ",".join("?" * len(ids))
                    conn.execute(f"DELETE FROM jobs WHERE job_id IN ({idph})", ids)
                    conn.commit()
            finally:
                conn.close()
        _delete_checkpoints_for_threads(ids)
        return len(ids)
    except Exception:
        return 0


def sweep_stuck_jobs(stuck_after_seconds: Optional[int] = None) -> int:
    """Job running/processing không heartbeat (updated_at — mọi update_job/append_token
    đều chạm) quá ngưỡng (default env JOB_STUCK_AFTER_SECONDS=900) → 'interrupted'.
    Pending KHÔNG bị đụng (job queue có thể chờ lâu hợp lệ — reconcile_interrupted
    lo orphan pending lúc startup). Idempotent; executor còn sống ghi done/error
    sau đó vẫn thắng (update_job ghi đè). <= 0 → tắt."""
    secs = stuck_after_seconds if stuck_after_seconds is not None else _env_int("JOB_STUCK_AFTER_SECONDS", 900)
    if secs <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=secs)).isoformat()
    try:
        init_db()
        with _lock:
            conn = get_conn()
            try:
                cur = conn.execute(
                    """
                    UPDATE jobs SET status='interrupted', current_node='StuckSweep', updated_at=?
                    WHERE status IN ('running','processing') AND updated_at < ?
                    """,
                    (_now(), cutoff),
                )
                conn.commit()
                return max(cur.rowcount, 0)
            finally:
                conn.close()
    except Exception:
        return 0


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

