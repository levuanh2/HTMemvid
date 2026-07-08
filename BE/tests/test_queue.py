"""Phase 5 Step 1 — RQ queue abstraction, shared DB paths, orphan reconciliation.

Unit-level: the RQ boundary is mocked (get_queue / _live_job_ids), so no real Redis
or worker is needed. Flag-off path is the real daemon-thread behaviour.
"""
from __future__ import annotations

import threading
import uuid

import pytest

from app.jobs import queue as q
from app.domains.jobs import jobs_store


@pytest.fixture
def tmp_jobs_db(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))
    yield


# --------------------------------------------------------------- shared DB paths
def test_jobs_db_path_respects_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "j.sqlite"))
    assert jobs_store.db_path() == tmp_path / "j.sqlite"


def test_jobs_db_path_default_when_unset(monkeypatch):
    monkeypatch.delenv("JOBS_DB_PATH", raising=False)
    assert jobs_store.db_path().name == "jobs.sqlite"


def test_log_db_path_respects_env(tmp_path, monkeypatch):
    from app.graphs import logger
    monkeypatch.setenv("LOG_DB_PATH", str(tmp_path / "l.sqlite"))
    assert logger.log_db_path() == tmp_path / "l.sqlite"


# --------------------------------------------------------------- enqueue switch
def test_thread_path_when_disabled(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    ev = threading.Event()
    res = q.enqueue_job(lambda: ev.set())
    assert res["mode"] == "thread"
    assert ev.wait(2.0)


def test_rq_path_when_enabled(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    seen = {}

    class FakeQ:
        def enqueue(self, func, *args, **kw):
            seen["func"], seen["args"], seen["kw"] = func, args, kw

    monkeypatch.setattr(q, "get_queue", lambda name="ingest": FakeQ())

    def fn(a, b):
        pass

    res = q.enqueue_job(fn, args=(1, 2), job_id="jx")
    assert res["mode"] == "rq"
    assert seen["func"] is fn and seen["args"] == (1, 2)
    assert seen["kw"].get("job_id") == "jx"


def test_rq_enqueue_failure_falls_back_to_thread(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "true")

    def boom(name="ingest"):
        raise RuntimeError("redis down")

    monkeypatch.setattr(q, "get_queue", boom)
    ev = threading.Event()
    res = q.enqueue_job(lambda: ev.set())
    assert res["mode"] == "thread_fallback"
    assert ev.wait(2.0)  # work still ran despite the broken queue


# --------------------------------------------------------------- ingest lifecycle
def test_ingest_lifecycle_pending_running_done(tmp_jobs_db, monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    jid = str(uuid.uuid4())
    jobs_store.create_job(jid, job_type="ingest", status="pending")
    assert jobs_store.get_job(jid)["status"] == "pending"
    done = threading.Event()

    def work():
        jobs_store.update_job(jid, status="running")
        jobs_store.update_job(jid, status="done", result={"ok": True})
        done.set()

    q.enqueue_job(work)
    assert done.wait(3.0)
    j = jobs_store.get_job(jid)
    assert j["status"] == "done" and j["result"] == {"ok": True}


def test_ingest_lifecycle_failed(tmp_jobs_db, monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    jid = str(uuid.uuid4())
    jobs_store.create_job(jid, job_type="ingest", status="pending")
    done = threading.Event()

    def work():
        jobs_store.update_job(jid, status="error", error_text="boom")
        done.set()

    q.enqueue_job(work)
    assert done.wait(3.0)
    assert jobs_store.get_job(jid)["status"] == "error"


# --------------------------------------------------------------- reconciliation
def test_reconcile_disabled_marks_all_active(tmp_jobs_db, monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    jid = str(uuid.uuid4())
    jobs_store.create_job(jid, "ingest", status="running")
    q.reconcile_interrupted()
    assert jobs_store.get_job(jid)["status"] == "interrupted"


def test_reconcile_registry_keeps_live_marks_stale(tmp_jobs_db, monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    live_id, stale_id = str(uuid.uuid4()), str(uuid.uuid4())
    jobs_store.create_job(live_id, "ingest", status="running")
    jobs_store.create_job(stale_id, "ingest", status="running")
    monkeypatch.setattr(q, "_live_job_ids", lambda: {live_id})
    res = q.reconcile_interrupted()
    assert res["mode"] == "registry"
    assert jobs_store.get_job(live_id)["status"] == "running"        # live worker job preserved
    assert jobs_store.get_job(stale_id)["status"] == "interrupted"   # orphan marked


def test_reconcile_rq_unreachable_touches_nothing(tmp_jobs_db, monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    jid = str(uuid.uuid4())
    jobs_store.create_job(jid, "ingest", status="running")
    monkeypatch.setattr(q, "_live_job_ids", lambda: None)  # RQ/Redis down
    res = q.reconcile_interrupted()
    assert res["mode"] == "skipped"
    assert jobs_store.get_job(jid)["status"] == "running"  # untouched (fail-safe)


# --------------------------------------------------------------- stats
def test_queue_stats_disabled_structured(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    s = q.queue_stats()
    for k in ("enabled", "queued_count", "started_count", "failed_count", "worker_count"):
        assert k in s
    assert s["enabled"] is False


# --------------------------------------------------------------- main routes
import app.main as main  # noqa: E402
from app.main import app as flask_app  # noqa: E402
from app.clients import redis_client  # noqa: E402


def test_stats_has_queue_block():
    r = flask_app.test_client().get("/stats")
    assert r.status_code == 200
    assert "queue" in r.get_json()


def test_ready_503_when_queue_full(monkeypatch):
    monkeypatch.setattr(main, "QUERY_GRAPH", object())
    monkeypatch.setattr(main, "_queue_stats_safe", lambda: {"enabled": True, "queued_count": 999})
    monkeypatch.setenv("QUEUE_DEPTH_MAX", "20")

    class FakeR:
        def ping(self):
            return True

    redis_client.reset_for_tests(FakeR())
    try:
        r = flask_app.test_client().get("/ready")
        assert r.status_code == 503
        assert "queue_full" in r.get_json()["reason"]
    finally:
        redis_client.reset_for_tests(None)


def test_query_path_still_returns_job_id_not_queued(tmp_path, monkeypatch):
    # /query is interactive and must NOT be moved to RQ — it still spawns a thread + 202.
    monkeypatch.setenv("QUEUE_ENABLED", "true")  # even with the queue on
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))
    r = flask_app.test_client().post("/query", json={"q": "nội dung là gì"})
    assert r.status_code == 202
    assert r.get_json().get("job_id")


# --------------------------------------------------------------- Step 2: summary
def test_summary_enqueues_to_summary_queue(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    seen = {}

    class FakeQ:
        def enqueue(self, func, *a, **k):
            seen["func"], seen["kw"] = func, k

    def fake_get_queue(name="ingest"):
        seen["queue"] = name
        return FakeQ()

    monkeypatch.setattr(q, "get_queue", fake_get_queue)
    res = q.enqueue_job(main.run_summary_job, args=("j", [], {}, "h", "medium"),
                        queue="summary", job_id="j")
    assert res["mode"] == "rq"
    assert seen["queue"] == "summary"          # correct queue name
    assert seen["func"] is main.run_summary_job
    assert seen["kw"].get("job_id") == "j"


def test_summary_thread_path_when_disabled(monkeypatch, tmp_path):
    # QUEUE_ENABLED=false -> summary still dispatched via a thread (existing behaviour).
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))
    calls = {}
    ev = threading.Event()

    def fake_run(jid, *a):
        calls["jid"] = jid
        ev.set()

    monkeypatch.setattr(main, "run_summary_job", fake_run)
    jid = main._start_summary_job(["s"], {"chunks": []}, "h", "medium")
    assert ev.wait(2.0) and calls["jid"] == jid   # ran in-thread, not queued


def test_run_summary_job_marks_error_without_flask(tmp_jobs_db, monkeypatch):
    # RQ worker executes run_summary_job with NO Flask request context; failure -> job error.
    jid = "sum-fail"
    jobs_store.create_job(jid, "summary", status="pending")
    monkeypatch.setattr(main, "SUMMARY_GRAPH", None)  # force a controlled failure
    main.run_summary_job(jid, [], {}, "h", "medium")
    j = jobs_store.get_job(jid)
    assert j["status"] == "error" and (j["error"] or "").strip()


def test_queue_stats_has_per_queue_breakdown(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    s = q.queue_stats()
    for name in ("ingest", "summary", "mindmap", "rebuild", "memory"):
        assert name in s
    assert set(s["summary"].keys()) == {"queued", "started", "failed"}


# --------------------------------------------------------------- Step 4: rebuild
def test_rebuild_enqueues_to_rebuild_queue(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    seen = {}

    class FakeQ:
        def enqueue(self, func, *a, **k):
            seen["func"], seen["kw"] = func, k

    def fake_get_queue(name="ingest"):
        seen["queue"] = name
        return FakeQ()

    monkeypatch.setattr(q, "get_queue", fake_get_queue)
    res = q.enqueue_job(main.run_rebuild_index_job, args=("j",), queue="rebuild", job_id="j")
    assert res["mode"] == "rq"
    assert seen["queue"] == "rebuild"          # correct queue name
    assert seen["func"] is main.run_rebuild_index_job
    assert seen["kw"].get("job_id") == "j"


def test_rebuild_thread_path_when_disabled(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    ev = threading.Event()
    res = q.enqueue_job(lambda jid: ev.set(), args=("j",), queue="rebuild")
    assert res["mode"] == "thread"
    assert ev.wait(2.0)


def test_run_rebuild_index_job_success_without_flask(tmp_jobs_db, monkeypatch):
    # RQ worker executes run_rebuild_index_job with NO Flask request context.
    jid = "rb-ok"
    jobs_store.create_job(jid, "rebuild", status="pending")
    monkeypatch.setattr("app.scripts.rebuild_index_from_video.rebuild_faiss_index_from_videos",
                        lambda progress_cb=None: {"num_videos": 3, "num_chunks": 42})
    main.run_rebuild_index_job(jid)
    j = jobs_store.get_job(jid)
    assert j["status"] == "done" and j["progress"] == 100
    assert j["result"]["num_chunks"] == 42 and j["result"]["num_videos"] == 3


def test_run_rebuild_index_job_marks_error_without_flask(tmp_jobs_db, monkeypatch):
    jid = "rb-fail"
    jobs_store.create_job(jid, "rebuild", status="pending")

    def boom(progress_cb=None):
        raise RuntimeError("rebuild boom")

    monkeypatch.setattr("app.scripts.rebuild_index_from_video.rebuild_faiss_index_from_videos", boom)
    main.run_rebuild_index_job(jid)
    j = jobs_store.get_job(jid)
    assert j["status"] == "error" and (j["error"] or "").strip()


# --------------------------------------------------------------- Step 4: memory-tree
def test_memory_tree_enqueues_to_memory_queue(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    seen = {}

    class FakeQ:
        def enqueue(self, func, *a, **k):
            seen["func"], seen["kw"] = func, k

    def fake_get_queue(name="ingest"):
        seen["queue"] = name
        return FakeQ()

    monkeypatch.setattr(q, "get_queue", fake_get_queue)
    res = q.enqueue_job(main.run_memory_tree_job, args=(["s"],), queue="memory")
    assert res["mode"] == "rq"
    assert seen["queue"] == "memory"           # correct queue name
    assert seen["func"] is main.run_memory_tree_job


def test_memory_tree_thread_path_when_disabled(monkeypatch):
    # QUEUE_ENABLED=false -> memory-tree still dispatched via a thread (existing behaviour).
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    ev = threading.Event()
    seen = {}

    def fake_build(stems):
        seen["stems"] = stems
        ev.set()

    monkeypatch.setattr(main, "build_memory_tree_for_sources", fake_build)
    main._trigger_memory_tree_build(["srcA"])
    assert ev.wait(2.0) and seen["stems"] == ["srcA"]   # ran in-thread, not queued


# --------------------------------------------------------------- Step 3: mindmap
def test_mindmap_enqueues_to_mindmap_queue(monkeypatch):
    monkeypatch.setenv("QUEUE_ENABLED", "true")
    seen = {}

    class FakeQ:
        def enqueue(self, func, *a, **k):
            seen["func"], seen["kw"] = func, k

    def fake_get_queue(name="ingest"):
        seen["queue"] = name
        return FakeQ()

    monkeypatch.setattr(q, "get_queue", fake_get_queue)
    res = q.enqueue_job(main.run_mindmap_job, args=("j", [], {}, "h"),
                        queue="mindmap", job_id="j")
    assert res["mode"] == "rq"
    assert seen["queue"] == "mindmap"          # correct queue name
    assert seen["func"] is main.run_mindmap_job
    assert seen["kw"].get("job_id") == "j"


def test_mindmap_thread_path_when_disabled(monkeypatch, tmp_path):
    # QUEUE_ENABLED=false -> mindmap still dispatched via a thread (existing behaviour).
    monkeypatch.setenv("QUEUE_ENABLED", "false")
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))
    calls = {}
    ev = threading.Event()

    def fake_run(jid, *a):
        calls["jid"] = jid
        ev.set()

    monkeypatch.setattr(main, "run_mindmap_job", fake_run)
    jid = main._start_mindmap_job(["s"], {"chunks": []}, "h")
    assert ev.wait(2.0) and calls["jid"] == jid   # ran in-thread, not queued


def test_run_mindmap_job_marks_error_without_flask(tmp_jobs_db, monkeypatch):
    # RQ worker executes run_mindmap_job with NO Flask request context; failure -> job error.
    jid = "mm-fail"
    jobs_store.create_job(jid, "mindmap", status="pending")
    monkeypatch.setattr(main, "MINDMAP_GRAPH", None)  # force a controlled failure
    main.run_mindmap_job(jid, [], {}, "h")
    j = jobs_store.get_job(jid)
    assert j["status"] == "error" and (j["error"] or "").strip()
