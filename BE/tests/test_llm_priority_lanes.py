"""PR#4 — query-priority lanes in the LLM gateway.

Lane suy từ AskRequest.feature có sẵn: "answer" → query lane (global slot only);
"summary"/"mindmap"/unknown/"" → batch lane (batch slot + global slot). Bật qua
LLM_PRIORITY_LANES_ENABLED (mặc định OFF = hành vi cũ y hệt). Không gRPC thật.
"""
from __future__ import annotations

import threading
import time

import pytest

from services.llm_gateway import server as gw


@pytest.fixture()
def lanes_on(monkeypatch):
    monkeypatch.setenv("LLM_PRIORITY_LANES_ENABLED", "true")
    monkeypatch.setenv("LLM_RESERVED_QUERY_SLOTS", "1")
    gw.configure_llm_gate(max_calls=2, wait_timeout=0.3)
    yield
    monkeypatch.delenv("LLM_PRIORITY_LANES_ENABLED", raising=False)
    monkeypatch.delenv("LLM_RESERVED_QUERY_SLOTS", raising=False)
    gw.configure_llm_gate(max_calls=2, wait_timeout=5.0)


@pytest.fixture()
def lanes_off(monkeypatch):
    monkeypatch.delenv("LLM_PRIORITY_LANES_ENABLED", raising=False)
    gw.configure_llm_gate(max_calls=2, wait_timeout=0.3)
    yield
    gw.configure_llm_gate(max_calls=2, wait_timeout=5.0)


def _hold_slot(feature: str, taken: threading.Event, release: threading.Event) -> threading.Thread:
    """Giữ một slot ở thread nền tới khi release set."""
    def run():
        with gw._llm_slot("hold", feature=feature):
            taken.set()
            release.wait(5.0)

    t = threading.Thread(target=run)
    t.start()
    assert taken.wait(2.0)
    return t


def test_lanes_disabled_batch_can_fill_all_slots(lanes_off):
    # Hành vi cũ: 2 batch chiếm đủ 2 slot, không giới hạn riêng.
    t1_taken, t1_rel = threading.Event(), threading.Event()
    t2_taken, t2_rel = threading.Event(), threading.Event()
    t1 = _hold_slot("summary", t1_taken, t1_rel)
    t2 = _hold_slot("mindmap", t2_taken, t2_rel)
    t1_rel.set(); t2_rel.set()
    t1.join(); t2.join()
    assert gw._llm_active == 0


def test_batch_limited_to_max_minus_reserved(lanes_on):
    # max=2, reserved=1 → batch lane 1 slot: batch thứ hai busy.
    taken, release = threading.Event(), threading.Event()
    t = _hold_slot("summary", taken, release)
    with pytest.raises(gw.LlmBusyError) as ei:
        with gw._llm_slot("second-batch", feature="mindmap"):
            pass
    assert "batch" in str(ei.value)
    release.set(); t.join()
    assert gw._llm_active == 0


def test_query_runs_while_batch_lane_full(lanes_on):
    taken, release = threading.Event(), threading.Event()
    t = _hold_slot("summary", taken, release)  # batch lane full (1/1)
    ran = []
    with gw._llm_slot("query", feature="answer"):  # query dùng slot reserve
        ran.append(True)
    assert ran == [True]
    release.set(); t.join()
    assert gw._llm_active == 0


def test_total_never_exceeds_max(lanes_on):
    # 1 batch + 1 query = 2/2 global → query thứ hai busy (global hết).
    b_taken, b_rel = threading.Event(), threading.Event()
    q_taken, q_rel = threading.Event(), threading.Event()
    tb = _hold_slot("summary", b_taken, b_rel)
    tq = _hold_slot("answer", q_taken, q_rel)
    with pytest.raises(gw.LlmBusyError):
        with gw._llm_slot("q2", feature="answer"):
            pass
    b_rel.set(); q_rel.set()
    tb.join(); tq.join()
    assert gw._llm_active == 0


def test_unknown_and_empty_feature_follow_batch(lanes_on):
    # feature lạ/rỗng KHÔNG được lane query (mặc định an toàn về batch).
    taken, release = threading.Event(), threading.Event()
    t = _hold_slot("", taken, release)  # chiếm batch slot duy nhất
    for feat in ("chat", "", "whatever"):
        with pytest.raises(gw.LlmBusyError):
            with gw._llm_slot("x", feature=feat):
                pass
    release.set(); t.join()
    assert gw._llm_active == 0


def test_global_timeout_releases_batch_slot(lanes_on):
    # Query giữ hết global? max=2: 2 query giữ 2 global slot; batch lấy được
    # batch-slot nhưng global timeout → batch-slot PHẢI được nhả lại.
    q1_taken, q1_rel = threading.Event(), threading.Event()
    q2_taken, q2_rel = threading.Event(), threading.Event()
    t1 = _hold_slot("answer", q1_taken, q1_rel)
    t2 = _hold_slot("answer", q2_taken, q2_rel)
    with pytest.raises(gw.LlmBusyError):
        with gw._llm_slot("batch", feature="summary"):
            pass
    # batch semaphore phải còn nguyên capacity (không leak)
    assert gw._batch_semaphore.acquire(timeout=0.1) is True
    gw._batch_semaphore.release()
    q1_rel.set(); q2_rel.set()
    t1.join(); t2.join()
    assert gw._llm_active == 0


def test_reserved_clamped_below_max(monkeypatch):
    # reserved >= max_calls → kẹp: batch lane vẫn còn >= 1 slot.
    monkeypatch.setenv("LLM_PRIORITY_LANES_ENABLED", "true")
    monkeypatch.setenv("LLM_RESERVED_QUERY_SLOTS", "99")
    gw.configure_llm_gate(max_calls=2, wait_timeout=0.3)
    try:
        with gw._llm_slot("batch", feature="summary"):
            pass  # batch vẫn chạy được — không bị reserve nuốt hết lane
        assert gw._BATCH_MAX == 1
    finally:
        monkeypatch.delenv("LLM_PRIORITY_LANES_ENABLED", raising=False)
        monkeypatch.delenv("LLM_RESERVED_QUERY_SLOTS", raising=False)
        gw.configure_llm_gate(max_calls=2, wait_timeout=5.0)
