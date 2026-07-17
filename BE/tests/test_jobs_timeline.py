"""Phase 0 observability — GET /jobs/<job_id>/timeline.

Read-only timeline from logs.sqlite node_logs + totals (total_ms, llm_calls,
queue_wait_ms). Same auth/owner contract as /summary-status: unknown job and
foreign job are both 404 (no existence oracle). Logs DB missing/unreadable
must never 500 — events degrade to [].
"""

from __future__ import annotations

import pytest

from app.domains.jobs import jobs_store as js
from app.graphs.logger import log_node_event


def _protect(main, monkeypatch, uid, on=True):
    monkeypatch.setattr(main, "_auth_protect_enabled", lambda: on)
    monkeypatch.setattr(main, "_current_user_id", lambda: uid)


@pytest.fixture()
def be(client):
    import app.main as main
    return main


def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_DB_PATH", str(tmp_path / "logs.sqlite"))
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))


def test_timeline_unknown_job_404(client, be, monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    _protect(be, monkeypatch, None, on=False)
    r = client.get("/jobs/khong_ton_tai/timeline")
    assert r.status_code == 404


def test_timeline_events_ordered_with_totals(client, be, monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    _protect(be, monkeypatch, None, on=False)
    js.create_job("jt1", job_type="summary", status="done")
    log_node_event("jt1", "CollectInput", "ok", 100.0)
    log_node_event("jt1", "Sections", "ok", 50.5, {"n": 3})
    log_node_event("jt1", "LLMCalls", "ok", 0.0, {"llm_calls": 4})
    log_node_event("khac", "CollectInput", "ok", 999.0)  # foreign job, filtered out

    r = client.get("/jobs/jt1/timeline")
    assert r.status_code == 200
    data = r.get_json()
    assert data["job_id"] == "jt1"
    assert data["status"] == "done"
    nodes = [e["node"] for e in data["events"]]
    assert nodes == ["CollectInput", "Sections", "LLMCalls"]
    assert data["events"][1]["metadata"] == {"n": 3}
    assert data["events"][0]["duration_ms"] == 100.0
    assert data["totals"]["total_ms"] == pytest.approx(150.5)
    assert data["totals"]["llm_calls"] == 4


def test_timeline_job_without_events_is_empty_not_error(client, be, monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    _protect(be, monkeypatch, None, on=False)
    js.create_job("jt2", job_type="mindmap", status="running", progress=30)
    r = client.get("/jobs/jt2/timeline")
    assert r.status_code == 200
    data = r.get_json()
    assert data["events"] == []
    assert data["totals"]["total_ms"] == 0
    assert data["totals"]["llm_calls"] is None
    assert data["status"] == "running"


def test_timeline_owner_scoped_no_oracle(client, be, monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    js.create_job("jt3", job_type="summary", status="done", user_id="A")
    _protect(be, monkeypatch, "B", on=True)
    assert client.get("/jobs/jt3/timeline").status_code == 404  # foreign → 404
    _protect(be, monkeypatch, "A", on=True)
    assert client.get("/jobs/jt3/timeline").status_code == 200  # owner → 200


def test_timeline_logs_db_unreadable_degrades_to_empty(client, be, monkeypatch, tmp_path):
    _iso(tmp_path, monkeypatch)
    _protect(be, monkeypatch, None, on=False)
    js.create_job("jt4", job_type="summary", status="done")
    # LOG_DB_PATH trỏ vào THƯ MỤC → sqlite connect/select fail → events [] chứ không 500
    monkeypatch.setenv("LOG_DB_PATH", str(tmp_path))
    r = client.get("/jobs/jt4/timeline")
    assert r.status_code == 200
    assert r.get_json()["events"] == []
