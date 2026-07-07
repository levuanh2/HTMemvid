"""Phase 2 — global LLM concurrency cap in the gateway.

Tests the process-global semaphore in services/llm_gateway/server.py in isolation:
no real gRPC server, no Ollama. A fake pool with a controllable delay stands in for
the expensive generation call.
"""
from __future__ import annotations

import threading
import time

import pytest

from services.llm_gateway import server as gw
from shared.proto.gen import llm_pb2


def setup_function(_fn):
    # Fresh gate before every test (rebuilds semaphore, resets in-flight counter).
    gw.configure_llm_gate(max_calls=2, wait_timeout=5.0)


class _Aborted(Exception):
    pass


class _FakeContext:
    """Mimics a grpc servicer context: abort() records the code and raises."""

    def __init__(self):
        self.aborted = None

    def abort(self, code, details):
        self.aborted = (code, details)
        raise _Aborted(details)


class _FakePool:
    def __init__(self, text="ok", delay=0.0):
        self.text = text
        self.delay = delay
        self.last_provider_used = "fake"

    def ask(self, prompt, **_kw):
        time.sleep(self.delay)
        return self.text


# --------------------------------------------------------------- config
def test_env_config_respected():
    gw.configure_llm_gate(max_calls=3, wait_timeout=7.5)
    assert gw._LLM_MAX == 3
    assert gw._LLM_WAIT == 7.5
    # semaphore actually allows 3 concurrent acquisitions
    got = [gw._llm_semaphore.acquire(timeout=0.1) for _ in range(3)]
    assert all(got)
    assert gw._llm_semaphore.acquire(timeout=0.1) is False
    for _ in range(3):
        gw._llm_semaphore.release()


def test_bad_env_falls_back_to_default():
    gw.configure_llm_gate()  # reset
    import os
    os.environ["MAX_CONCURRENT_LLM_CALLS"] = "not-a-number"
    try:
        gw.configure_llm_gate()
        assert gw._LLM_MAX == 2  # default, not a crash
    finally:
        os.environ.pop("MAX_CONCURRENT_LLM_CALLS", None)
        gw.configure_llm_gate(max_calls=2, wait_timeout=5.0)


# --------------------------------------------------------------- cap
def test_semaphore_caps_concurrent_generations():
    gw.configure_llm_gate(max_calls=2, wait_timeout=10.0)
    peak = 0
    cur = 0
    lock = threading.Lock()

    def work():
        nonlocal peak, cur
        with gw._llm_slot("t"):
            with lock:
                cur += 1
                peak = max(peak, cur)
            time.sleep(0.15)
            with lock:
                cur -= 1

    threads = [threading.Thread(target=work) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak <= 2
    assert gw._llm_active == 0  # every slot released


# --------------------------------------------------------------- release
def test_slot_released_after_success():
    gw.configure_llm_gate(max_calls=1, wait_timeout=2.0)
    for _ in range(5):
        with gw._llm_slot("t"):
            pass
    assert gw._llm_active == 0
    assert gw._llm_semaphore.acquire(timeout=0.1) is True
    gw._llm_semaphore.release()


def test_slot_released_after_exception():
    gw.configure_llm_gate(max_calls=1, wait_timeout=2.0)
    with pytest.raises(ValueError):
        with gw._llm_slot("t"):
            raise ValueError("boom")
    assert gw._llm_active == 0
    # slot is free again despite the exception
    assert gw._llm_semaphore.acquire(timeout=0.1) is True
    gw._llm_semaphore.release()


# --------------------------------------------------------------- wait timeout
def test_wait_timeout_raises_busy():
    gw.configure_llm_gate(max_calls=1, wait_timeout=0.3)
    gate_taken = threading.Event()
    release = threading.Event()

    def holder():
        with gw._llm_slot("hold"):
            gate_taken.set()
            release.wait(2.0)

    t = threading.Thread(target=holder)
    t.start()
    assert gate_taken.wait(1.0)

    t0 = time.time()
    with pytest.raises(gw.LlmBusyError):
        with gw._llm_slot("second"):
            pass
    waited = time.time() - t0
    assert 0.2 <= waited <= 1.5  # bounded wait, not unlimited

    release.set()
    t.join()
    assert gw._llm_active == 0


# --------------------------------------------------------------- servicer
def test_ask_success_path_still_works():
    gw.configure_llm_gate(max_calls=2, wait_timeout=5.0)
    svc = gw.LlmGatewayService(pool=_FakePool(text="hello"))
    resp = svc.Ask(llm_pb2.AskRequest(prompt="hi"), _FakeContext())
    assert resp.text == "hello"
    assert resp.provider_used == "fake"
    assert gw._llm_active == 0


def test_ask_busy_returns_controlled_error():
    gw.configure_llm_gate(max_calls=1, wait_timeout=0.3)
    svc = gw.LlmGatewayService(pool=_FakePool(text="slow", delay=1.0))

    # Occupy the single slot with a slow in-flight Ask.
    holder = threading.Thread(
        target=lambda: svc.Ask(llm_pb2.AskRequest(prompt="a"), _FakeContext())
    )
    holder.start()
    time.sleep(0.1)  # let the holder acquire the slot

    ctx = _FakeContext()
    with pytest.raises(_Aborted):
        svc.Ask(llm_pb2.AskRequest(prompt="b"), ctx)
    assert ctx.aborted is not None
    assert ctx.aborted[0] == gw.grpc.StatusCode.RESOURCE_EXHAUSTED
    assert "busy" in ctx.aborted[1].lower()

    holder.join()
    assert gw._llm_active == 0  # released after both success and busy paths
