"""PR#2 reliability + retention — jobs_store/logger maintenance.

Cover: retention prune (chỉ terminal cũ), checkpoint prune theo thread_id,
token_buffer clear khi terminal (giữ khi running/interrupted), stuck-job sweep
(running không heartbeat → interrupted; pending/terminal không đụng), log retention.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.domains.jobs import jobs_store as js
from app.graphs import logger as lg


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))
    monkeypatch.setenv("LOG_DB_PATH", str(tmp_path / "logs.sqlite"))
    return tmp_path


def _backdate(job_id: str, **delta) -> None:
    """Đẩy updated_at về quá khứ (format iso UTC y hệt _now())."""
    ts = (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()
    conn = sqlite3.connect(str(js.db_path()))
    try:
        conn.execute("UPDATE jobs SET updated_at=? WHERE job_id=?", (ts, job_id))
        conn.commit()
    finally:
        conn.close()


# --- Retention -------------------------------------------------------------

def test_old_terminal_jobs_pruned_recent_kept(iso):
    for st in ("done", "error", "timeout", "cancelled", "interrupted"):
        js.create_job(f"old_{st}", "summary", status=st)
        _backdate(f"old_{st}", days=10)
    js.create_job("recent_done", "summary", status="done")  # trong hạn → giữ

    assert js.cleanup_terminal_jobs(retention_days=7) == 5
    for st in ("done", "error", "timeout", "cancelled", "interrupted"):
        assert js.get_job(f"old_{st}") is None
    assert js.get_job("recent_done") is not None


def test_active_jobs_never_pruned_even_if_old(iso):
    for st in ("pending", "running", "processing"):
        js.create_job(f"act_{st}", "query", status=st)
        _backdate(f"act_{st}", days=30)
    assert js.cleanup_terminal_jobs(retention_days=7) == 0
    for st in ("pending", "running", "processing"):
        assert js.get_job(f"act_{st}") is not None


def test_cleanup_idempotent_and_disabled_by_zero(iso):
    js.create_job("j1", "summary", status="done")
    _backdate("j1", days=10)
    assert js.cleanup_terminal_jobs(retention_days=0) == 0  # tắt → không đụng
    assert js.get_job("j1") is not None
    assert js.cleanup_terminal_jobs(retention_days=7) == 1
    assert js.cleanup_terminal_jobs(retention_days=7) == 0  # idempotent


def test_cleanup_prunes_checkpoints_of_pruned_jobs(iso, tmp_path):
    ck = tmp_path / "checkpoints.sqlite"
    conn = sqlite3.connect(str(ck))
    conn.execute("CREATE TABLE checkpoints (thread_id TEXT, checkpoint_id TEXT)")
    conn.execute("CREATE TABLE writes (thread_id TEXT, task_id TEXT)")
    conn.execute("INSERT INTO checkpoints VALUES ('j_old','c1')")
    conn.execute("INSERT INTO writes VALUES ('j_old','t1')")
    conn.execute("INSERT INTO checkpoints VALUES ('j_keep','c2')")
    conn.commit()
    conn.close()

    js.create_job("j_old", "summary", status="done")
    _backdate("j_old", days=10)
    js.create_job("j_keep", "summary", status="done")  # trong hạn

    assert js.cleanup_terminal_jobs(retention_days=7) == 1
    conn = sqlite3.connect(str(ck))
    try:
        assert conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='j_old'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM writes WHERE thread_id='j_old'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM checkpoints WHERE thread_id='j_keep'").fetchone()[0] == 1
    finally:
        conn.close()


def test_cleanup_survives_missing_checkpoint_db(iso):
    js.create_job("j2", "summary", status="done")
    _backdate("j2", days=10)
    # không có checkpoints.sqlite trong DATA_DIR → vẫn prune job, không raise
    assert js.cleanup_terminal_jobs(retention_days=7) == 1


# --- token_buffer ----------------------------------------------------------

def test_running_job_keeps_token_buffer(iso):
    js.create_job("q1", "query", status="running")
    js.append_token("q1", "xin chào")
    js.update_job("q1", progress=50)
    assert js.get_job("q1")["token_buffer"] == "xin chào"


def test_done_clears_token_buffer_but_keeps_result(iso):
    js.create_job("q2", "query", status="running")
    js.append_token("q2", "một phần answer")
    js.update_job("q2", status="done", progress=100,
                  result={"payload": {"answer": "full answer"}, "status": 200})
    j = js.get_job("q2")
    assert j["status"] == "done"
    assert j["token_buffer"] == ""
    assert j["result"]["payload"]["answer"] == "full answer"


def test_error_and_cancelled_clear_token_buffer(iso):
    for jid, st in (("q3", "error"), ("q4", "cancelled"), ("q5", "timeout")):
        js.create_job(jid, "query", status="running")
        js.append_token(jid, "partial")
        js.update_job(jid, status=st, error_text="boom" if st == "error" else None)
        assert js.get_job(jid)["token_buffer"] == "", st


def test_interrupted_keeps_token_buffer_for_resume(iso):
    # HITL: interrupted có thể resume → buffer phải sống để SSE stream tiếp
    js.create_job("q6", "query", status="running")
    js.append_token("q6", "partial")
    js.update_job("q6", status="interrupted")
    assert js.get_job("q6")["token_buffer"] == "partial"


def test_request_cancel_on_pending_clears_buffer(iso):
    js.create_job("q7", "query", status="pending")
    js.append_token("q7", "stale")
    js.request_cancel("q7")
    j = js.get_job("q7")
    assert j["status"] == "cancelled"
    assert j["token_buffer"] == ""


def test_request_cancel_on_running_keeps_buffer_cooperative(iso):
    js.create_job("q8", "query", status="running")
    js.append_token("q8", "live")
    js.request_cancel("q8")  # cooperative: executor còn sống, stream tiếp tục
    j = js.get_job("q8")
    assert j["status"] == "running"
    assert j["token_buffer"] == "live"


# --- Stuck-job sweep -------------------------------------------------------

def test_stale_running_job_swept_to_interrupted(iso):
    js.create_job("s1", "summary", status="running")
    _backdate("s1", seconds=1200)
    assert js.sweep_stuck_jobs(stuck_after_seconds=900) == 1
    j = js.get_job("s1")
    assert j["status"] == "interrupted"
    assert j["current_node"] == "StuckSweep"


def test_fresh_running_and_old_pending_untouched(iso):
    js.create_job("s2", "summary", status="running")  # heartbeat mới
    js.create_job("s3", "summary", status="pending")
    _backdate("s3", seconds=5000)  # pending chờ lâu hợp lệ (queue backlog)
    assert js.sweep_stuck_jobs(stuck_after_seconds=900) == 0
    assert js.get_job("s2")["status"] == "running"
    assert js.get_job("s3")["status"] == "pending"


def test_terminal_jobs_untouched_and_sweep_idempotent(iso):
    js.create_job("s4", "summary", status="done")
    _backdate("s4", seconds=5000)
    js.create_job("s5", "summary", status="processing")
    _backdate("s5", seconds=5000)
    assert js.sweep_stuck_jobs(stuck_after_seconds=900) == 1  # chỉ s5
    assert js.get_job("s4")["status"] == "done"
    assert js.get_job("s5")["status"] == "interrupted"
    assert js.sweep_stuck_jobs(stuck_after_seconds=900) == 0  # idempotent


# --- Log retention ---------------------------------------------------------

def test_old_node_logs_pruned_recent_kept(iso):
    lg.log_node_event("lj1", "OldNode", "ok", 1.0)
    lg.log_node_event("lj2", "NewNode", "ok", 1.0)
    conn = sqlite3.connect(str(lg.log_db_path()))
    try:
        conn.execute("UPDATE node_logs SET ts=datetime('now','-10 days') WHERE job_id='lj1'")
        conn.commit()
    finally:
        conn.close()

    assert lg.cleanup_old_node_logs(retention_days=7) == 1
    assert lg.read_job_events("lj1") == []
    assert len(lg.read_job_events("lj2")) == 1
    assert lg.cleanup_old_node_logs(retention_days=7) == 0  # idempotent


def test_log_cleanup_missing_db_returns_zero(iso):
    assert lg.cleanup_old_node_logs(retention_days=7) == 0  # chưa có logs.sqlite
