"""HITL endpoint: /query → interrupted, /query-resume → done; guard 404/409."""

from __future__ import annotations

import time

import pytest

from langgraph.types import Command


class _Interrupt:
    def __init__(self, value):
        self.value = value


class _Task:
    def __init__(self, interrupts):
        self.interrupts = interrupts


class _StateSnap:
    def __init__(self, nxt, tasks):
        self.next = nxt
        self.tasks = tasks


class _HITLMockGraph:
    """Mock: invoke đầu (state) → pause; invoke sau (Command) → trả lời cuối."""

    def __init__(self):
        self.paused = {}

    def invoke(self, payload, config=None, **_k):
        tid = config["configurable"]["thread_id"]
        if isinstance(payload, Command):
            self.paused[tid] = False
            return {"payload": {"answer": "approved final"}, "status_code": 200, "answer": "approved final"}
        self.paused[tid] = True
        return dict(payload or {})

    def get_state(self, config):
        tid = config["configurable"]["thread_id"]
        if self.paused.get(tid):
            return _StateSnap(("ReviewGate",), [_Task([_Interrupt({"type": "review", "answer": "draft"})])])
        return _StateSnap((), [])


def _poll(client, jid, want, tries=50):
    for _ in range(tries):
        s = client.get(f"/query-status/{jid}")
        data = s.get_json()
        if data.get("status") == want:
            return data
        if data.get("status") == "error":
            raise AssertionError(f"job error: {data.get('error')}")
        time.sleep(0.05)
    raise AssertionError(f"job {jid} không đạt trạng thái {want}")


@pytest.fixture
def hitl_graph(client):
    import app.main as be_main
    prev = be_main.QUERY_GRAPH
    be_main.QUERY_GRAPH = _HITLMockGraph()
    try:
        yield client
    finally:
        be_main.QUERY_GRAPH = prev


def test_query_interrupts_then_resumes(hitl_graph):
    client = hitl_graph
    r = client.post("/query", json={"q": "review me", "sources": [], "use_memory_tree": False})
    assert r.status_code == 202
    jid = r.get_json()["job_id"]

    data = _poll(client, jid, "interrupted")
    assert data["result"]["payload"]["review"]["answer"] == "draft"

    rr = client.post(f"/query-resume/{jid}", json={"action": "approve"})
    assert rr.status_code == 202

    done = _poll(client, jid, "done")
    assert done["result"]["payload"]["answer"] == "approved final"


def test_resume_unknown_job_404(hitl_graph):
    rr = hitl_graph.post("/query-resume/does-not-exist", json={"action": "approve"})
    assert rr.status_code == 404


def test_resume_non_interrupted_409(hitl_graph):
    client = hitl_graph
    r = client.post("/query", json={"q": "review me", "sources": [], "use_memory_tree": False})
    jid = r.get_json()["job_id"]
    _poll(client, jid, "interrupted")
    # resume lần 1 → chuyển sang running/done
    client.post(f"/query-resume/{jid}", json={"action": "approve"})
    _poll(client, jid, "done")
    # resume lại job đã done → 409
    rr = client.post(f"/query-resume/{jid}", json={"action": "approve"})
    assert rr.status_code == 409
