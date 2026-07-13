"""Auth Hardening Phase C — query + query-job ownership, retrieval filter, cache bypass."""

from __future__ import annotations

import time

import pytest


def _protect(main, monkeypatch, uid, on=True):
    monkeypatch.setattr(main, "_auth_protect_enabled", lambda: on)
    monkeypatch.setattr(main, "_current_user_id", lambda: uid)


@pytest.fixture()
def be(client):
    import app.main as main
    return main


# ---- 401 when protected, no token ------------------------------------------

def test_query_routes_401_without_token(be, client, monkeypatch):
    _protect(be, monkeypatch, None, on=True)
    assert client.post("/query", json={"q": "hi"}).status_code == 401
    assert client.get("/query-status/j1").status_code == 401
    assert client.get("/query-stream/j1").status_code == 401
    assert client.post("/query-resume/j1", json={"action": "approve"}).status_code == 401


# ---- query source ownership + in-process invariant -------------------------

def test_query_foreign_source_403(be, client, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "idA": {"source_stem": "doc_a", "user_id": "userA"},
        "idB": {"source_stem": "doc_b", "user_id": "userB"},
    })
    _protect(be, monkeypatch, "userA", on=True)
    r = client.post("/query", json={"q": "test", "sources": ["doc_b"], "use_memory_tree": False})
    assert r.status_code == 403


def test_query_empty_sources_ok_and_in_process(be, client, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "idA": {"source_stem": "doc_a", "user_id": "userA"},
    })
    _protect(be, monkeypatch, "userA", on=True)
    import app.jobs.queue as queue
    calls = []
    monkeypatch.setattr(queue, "enqueue_job", lambda *a, **k: calls.append(a))
    r = client.post("/query", json={"q": "test", "sources": [], "use_memory_tree": False})
    assert r.status_code == 202  # empty resolves to owned; query accepted
    time.sleep(0.2)
    assert calls == []  # /query never enqueues to RQ (in-process)


# ---- query job ownership ---------------------------------------------------

def test_query_job_status_owner_isolation(be, client, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {"idA": {"source_stem": "doc_a", "user_id": "userA"}})
    _protect(be, monkeypatch, "userA", on=True)
    jid = client.post("/query", json={"q": "test", "sources": [], "use_memory_tree": False}).get_json()["job_id"]
    # A can read own job status
    assert client.get(f"/query-status/{jid}").status_code == 200
    # B cannot (404, no oracle) — status, stream, resume
    _protect(be, monkeypatch, "userB", on=True)
    assert client.get(f"/query-status/{jid}").status_code == 404
    assert client.get(f"/query-stream/{jid}").status_code == 404
    assert client.post(f"/query-resume/{jid}", json={"action": "approve"}).status_code == 404


def test_query_job_owner_helper(be, monkeypatch):
    # in-memory query_jobs carries user_id; helper checks it
    with be.query_jobs_lock:
        be.query_jobs["jX"] = {"status": "done", "user_id": "userA"}
    try:
        assert be._query_job_owner_ok("jX", "userA") is True
        assert be._query_job_owner_ok("jX", "userB") is False
        assert be._query_job_owner_ok("unknown-job", "userA") is None
    finally:
        with be.query_jobs_lock:
            be.query_jobs.pop("jX", None)


# ---- retrieval filter: non-matching/sentinel stem returns [] ---------------

def test_filter_by_sources_excludes_non_owned(monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    from app.domains.retrieval.hybrid import HybridRetriever
    from tests._qg_build import StubChunk
    from pathlib import Path
    r = HybridRetriever(index_path=Path("nope.faiss"), meta_path=Path("nope.json"))
    r._chunks = [StubChunk("owned text", stem="doc_a", cid=1)]
    assert r._filter_by_sources(["doc_a"]) == [0]            # owned → matched
    assert r._filter_by_sources(["doc_b"]) == []             # foreign → excluded
    assert r._filter_by_sources(["\x00__no_owned_sources__"]) == []  # sentinel → excluded
    assert r._filter_by_sources([]) == [0]                   # open (None/empty) → all


# ---- cache scope under auth protection: see test_cache_ownership.py (Phase E) ----------


class _RecordingRetriever:
    """Retriever that actually honors selected_sources (returns only matching stems)."""
    def __init__(self, chunks):
        self._chunks = chunks
        self.seen_sources = []

    def retrieve(self, q, *, selected_sources=None, top_k=6, category=None, language=None):
        self.seen_sources.append(list(selected_sources or []))
        if not selected_sources:
            return list(self._chunks)  # open behavior
        return [c for c in self._chunks if c.video_stem in set(selected_sources)]


def test_sentinel_selected_source_yields_zero_context(monkeypatch):
    """flag-on user with zero owned sources → sentinel → retrieval returns 0 chunks
    (never the global corpus)."""
    from tests import _qg_build as qb
    qb.base_env(monkeypatch)
    rec = _RecordingRetriever([qb.StubChunk("global secret", stem="doc_other", cid=1)])
    g, _cache = qb.build(retriever=rec)
    state = qb.init_state("nội dung là gì", selected_sources=["\x00__no_owned_sources__"])
    out = qb.run(g, state, thread_id="tsentinel")
    # retriever saw the sentinel (plumbing) and matched nothing → no context leak
    assert rec.seen_sources[0] == ["\x00__no_owned_sources__"]
    assert not (out.get("retrieved_chunks") or [])


def test_user_a_and_b_empty_sources_scoped(monkeypatch):
    """selected_sources=[] resolves to the caller's own stems only."""
    import app.main as be
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "idA": {"source_stem": "doc_a", "user_id": "A"},
        "idB": {"source_stem": "doc_b", "user_id": "B"},
    })
    monkeypatch.setattr(be, "_auth_protect_enabled", lambda: True)
    with be.app.app_context():
        ra, _ = be._resolve_owned_query_sources([], "A")
        rb, _ = be._resolve_owned_query_sources([], "B")
    assert ra == ["doc_a"] and rb == ["doc_b"]  # disjoint, no cross-user, no global
