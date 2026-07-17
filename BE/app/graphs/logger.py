from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Phase 0 observability — LLM call counter.
# Per-job: counter dict mutable gắn vào contextvar ở job thread (run_*_job /
# process_query_job); pipeline pool PHẢI submit qua ctx_submit để propagate
# (ThreadPoolExecutor không copy context). Global: total per-process + mirror
# Redis INCR (fail-open tuyệt đối — đếm không bao giờ làm hỏng LLM call).
# ---------------------------------------------------------------------------
_LLM_COUNT: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "llm_call_counter", default=None)
_llm_total_lock = threading.Lock()
_LLM_TOTAL = {"calls": 0}


def _get_redis_safe():
    """Seam cho test + tránh import cycle. None khi Redis vắng."""
    try:
        from app.clients.redis_client import get_redis
        return get_redis()
    except Exception:
        return None


def begin_llm_count() -> dict:
    """Gắn counter mới vào context của thread hiện tại (đầu job). Trả về dict
    mutable để flush_llm_count dùng ở cuối job (kể cả khi job lỗi)."""
    c = {"calls": 0}
    _LLM_COUNT.set(c)
    return c


def reset_llm_count() -> None:
    _LLM_COUNT.set(None)


def note_llm_call() -> None:
    """+1 cho MỘT lần gọi model/gateway thật (cache hit không đi qua đây)."""
    with _llm_total_lock:
        _LLM_TOTAL["calls"] += 1
        c = _LLM_COUNT.get()
        if c is not None:
            c["calls"] = c.get("calls", 0) + 1
    try:
        r = _get_redis_safe()
        if r is not None:
            r.incrby("metrics:llm:calls", 1)
    except Exception:
        pass  # fail-open: lỗi Redis nuốt tại chỗ


def llm_calls_total() -> int:
    with _llm_total_lock:
        return int(_LLM_TOTAL["calls"])


def ctx_submit(ex, fn, *args, **kwargs):
    """ex.submit có propagate contextvars — dùng cho pool gọi LLM để counter
    per-job đếm được từ pool thread. Hành vi fn không đổi."""
    return ex.submit(contextvars.copy_context().run, fn, *args, **kwargs)


def flush_llm_count(job_id: str, counter: Optional[dict]) -> None:
    """Ghi tổng LLM call của job thành node event 'LLMCalls' (tái dùng
    logs.sqlite — timeline endpoint đọc lại). Best-effort, không raise."""
    if not counter:
        return
    try:
        log_node_event(job_id, "LLMCalls", "ok", 0.0,
                       {"llm_calls": int(counter.get("calls", 0))})
    except Exception:
        pass


def read_job_events(job_id: str) -> list[dict]:
    """Node events của MỘT job theo thứ tự ghi. DB vắng/hỏng → [] (read-only,
    không bao giờ raise vào route)."""
    try:
        p = log_db_path()
        if not p.is_file():
            return []
        conn = sqlite3.connect(str(p), timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            cur = conn.execute(
                "SELECT node, status, duration_ms, ts, metadata FROM node_logs "
                "WHERE job_id=? ORDER BY id",
                (job_id,),
            )
            out: list[dict] = []
            for node, status, dur, ts, md in cur.fetchall():
                try:
                    mdd = json.loads(md) if md else {}
                except Exception:
                    mdd = {}
                out.append({"node": node, "status": status,
                            "duration_ms": dur, "ts": ts, "metadata": mdd})
            return out
        finally:
            conn.close()
    except Exception:
        return []


def _data_dir() -> Path:
    base = (os.environ.get("DATA_DIR") or "").strip()
    if base:
        return Path(base)
    return Path(__file__).resolve().parent.parent


def log_db_path() -> Path:
    # Phase 5: LOG_DB_PATH lets web + RQ worker share node_logs on a mounted volume.
    override = (os.environ.get("LOG_DB_PATH") or "").strip()
    if override:
        return Path(override)
    return _data_dir() / "logs.sqlite"


def log_node_event(
    job_id: str,
    node_name: str,
    status: str,
    duration_ms: float = 0.0,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """
    Local observability thay LangSmith.
    status: ok | error | timeout
    """
    md = metadata or {}
    with _lock:
        p = log_db_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS node_logs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id      TEXT,
                    node        TEXT,
                    status      TEXT,
                    duration_ms REAL,
                    ts          TEXT,
                    metadata    TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO node_logs VALUES (NULL,?,?,?,?,datetime('now'),?)
                """,
                (job_id, node_name, status, float(duration_ms), json.dumps(md, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()


def cleanup_old_node_logs(retention_days: Optional[int] = None) -> int:
    """Prune node_logs cũ hơn retention (default env LOG_RETENTION_DAYS=7).
    Idempotent, fail-open (DB/table vắng hoặc lỗi → 0). <= 0 → tắt."""
    if retention_days is None:
        try:
            retention_days = int((os.environ.get("LOG_RETENTION_DAYS") or "").strip() or 7)
        except ValueError:
            retention_days = 7
    if retention_days <= 0:
        return 0
    try:
        p = log_db_path()
        if not p.is_file():
            return 0
        with _lock:
            conn = sqlite3.connect(str(p), timeout=5.0)
            conn.execute("PRAGMA busy_timeout=5000")
            try:
                cur = conn.execute(
                    "DELETE FROM node_logs WHERE ts < datetime('now', ?)",
                    (f"-{int(retention_days)} days",),
                )
                conn.commit()
                return max(cur.rowcount, 0)
            finally:
                conn.close()
    except Exception:
        return 0


class _Timer:
    def __init__(self) -> None:
        self.t0 = time.time()

    def ms(self) -> float:
        return (time.time() - self.t0) * 1000.0

