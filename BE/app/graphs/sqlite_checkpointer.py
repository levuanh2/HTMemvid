"""SqliteSaver.from_conn_string là context manager — không dùng làm checkpointer trực tiếp cho compile()."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


def sqlite_saver_from_path(db_path: Path) -> SqliteSaver:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    return SqliteSaver(conn)
