from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_lock = threading.Lock()


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


class _Timer:
    def __init__(self) -> None:
        self.t0 = time.time()

    def ms(self) -> float:
        return (time.time() - self.t0) * 1000.0

