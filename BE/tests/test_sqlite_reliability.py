"""PR#2 SQLite reliability — mọi store SQLite dùng chung (cross-process/-container)
phải set PRAGMA busy_timeout để writer đồng thời không ném 'database is locked' ngay.
"""

from __future__ import annotations


def _busy_timeout(conn) -> int:
    return int(conn.execute("PRAGMA busy_timeout").fetchone()[0])


def test_jobs_store_conn_has_busy_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))
    from app.domains.jobs import jobs_store as js
    conn = js.get_conn()
    try:
        assert _busy_timeout(conn) == 5000
    finally:
        conn.close()


def test_mindmap_store_conn_has_busy_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mindmaps.sqlite"))
    from app.domains.mindmap import store as mm_store
    conn = mm_store.get_conn()
    try:
        assert _busy_timeout(conn) == 5000
    finally:
        conn.close()


def test_summary_store_conn_has_busy_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("SUMMARIES_DB_PATH", str(tmp_path / "summaries.sqlite"))
    from app.domains.summary import store as sum_store
    conn = sum_store.get_conn()
    try:
        assert _busy_timeout(conn) == 5000
    finally:
        conn.close()


def test_sqlite_checkpointer_conn_has_busy_timeout(tmp_path):
    from app.graphs.sqlite_checkpointer import sqlite_saver_from_path
    saver = sqlite_saver_from_path(tmp_path / "checkpoints.sqlite")
    conn = getattr(saver, "conn", None)
    assert conn is not None, "SqliteSaver không expose .conn — cập nhật test theo API mới"
    assert _busy_timeout(conn) == 5000
