"""PR#4 — in-process LLM concurrency cap (monolith không có gateway).

Cap tại chokepoint ask_ai: khi LLM_GATEWAY_ADDR unset, generation đồng thời bị
chặn bởi BoundedSemaphore process-wide (MAX_CONCURRENT_LLM_CALLS +
LLM_QUEUE_WAIT_TIMEOUT_SECONDS). Fake provider — không Ollama thật.
"""
from __future__ import annotations

import threading
import time

import pytest

import app.clients.llm_factory as lf


@pytest.fixture()
def local_llm(monkeypatch):
    """ask_ai đi nhánh local với fake provider block-được."""
    monkeypatch.delenv("LLM_GATEWAY_ADDR", raising=False)
    monkeypatch.setattr(lf, "PROVIDERS", ["ollama"])
    state = {"active": 0, "peak": 0, "release": threading.Event(), "lock": threading.Lock()}

    def fake_invoke(llm, prompt, system_prompt, timeout=None):
        with state["lock"]:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            state["release"].wait(2.0)
            return "ok"
        finally:
            with state["lock"]:
                state["active"] -= 1

    monkeypatch.setattr(lf, "_ollama_chat_llm", lambda *a, **k: object())
    monkeypatch.setattr(lf, "_invoke_chat", fake_invoke)
    yield state
    lf.configure_inproc_gate(max_calls=2, wait_timeout=30.0)


def test_concurrent_calls_capped(local_llm):
    lf.configure_inproc_gate(max_calls=1, wait_timeout=0.3)
    taken = threading.Event()

    def holder():
        local_llm["release"].clear()
        taken.set()
        lf.ask_ai("q1")

    t = threading.Thread(target=holder)
    t.start()
    assert taken.wait(1.0)
    time.sleep(0.1)  # holder chiếm slot

    t0 = time.time()
    with pytest.raises(RuntimeError) as ei:
        lf.ask_ai("q2")
    assert "busy" in str(ei.value).lower()
    assert time.time() - t0 <= 1.5  # bounded wait

    local_llm["release"].set()
    t.join()
    assert local_llm["peak"] == 1  # không bao giờ 2 generation đồng thời


def test_slot_released_after_success_and_error(local_llm, monkeypatch):
    lf.configure_inproc_gate(max_calls=1, wait_timeout=0.3)
    local_llm["release"].set()
    for _ in range(3):
        assert lf.ask_ai("q") == "ok"  # slot tái dùng được liên tiếp

    def boom(*a, **k):
        raise ValueError("provider down")
    monkeypatch.setattr(lf, "_invoke_chat", boom)
    with pytest.raises(RuntimeError):  # "All AI providers failed"
        lf.ask_ai("q")
    # slot đã nhả dù exception — acquire được ngay
    assert lf._inproc_semaphore.acquire(timeout=0.1) is True
    lf._inproc_semaphore.release()


def test_disabled_flag_removes_cap(local_llm, monkeypatch):
    monkeypatch.setenv("LLM_INPROCESS_CAP_ENABLED", "0")
    lf.configure_inproc_gate(max_calls=1, wait_timeout=0.3)
    local_llm["release"].clear()
    results = []

    def call():
        results.append(lf.ask_ai("q"))

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    assert local_llm["peak"] == 2  # không cap khi tắt
    local_llm["release"].set()
    for t in threads:
        t.join()
    assert results == ["ok", "ok"]


def test_gateway_branch_bypasses_inproc_gate(monkeypatch):
    # LLM_GATEWAY_ADDR set → ask_ai đi gRPC, KHÔNG chạm semaphore in-process
    # (gateway tự cap phía server).
    monkeypatch.setenv("LLM_GATEWAY_ADDR", "fake:50051")
    lf.configure_inproc_gate(max_calls=1, wait_timeout=0.3)

    class FakeGrpc:
        def ask(self, prompt, **kw):
            return "from-gateway"

    monkeypatch.setattr(lf, "_grpc_llm_provider", lambda addr: FakeGrpc())
    # Chiếm sạch semaphore in-process — gateway path vẫn phải chạy được.
    assert lf._inproc_semaphore.acquire(timeout=0.1)
    try:
        assert lf.ask_ai("q") == "from-gateway"
    finally:
        lf._inproc_semaphore.release()
        monkeypatch.delenv("LLM_GATEWAY_ADDR", raising=False)
        lf.configure_inproc_gate(max_calls=2, wait_timeout=30.0)
