"""Phase 0 observability — per-job LLM call counter + global total.

Counter contract:
- begin_llm_count() gắn counter (dict mutable) vào contextvar của job thread.
- note_llm_call() +1 cả global total lẫn counter đang active (nếu có).
- ctx_submit(ex, fn, ...) propagate contextvar qua ThreadPoolExecutor —
  các pipeline pool (summarize/enrich/coverage) dùng nó thay ex.submit.
- flush_llm_count(job_id, counter) ghi node event "LLMCalls" qua log_node_event
  (tái dùng logs.sqlite, không thêm hệ thống log mới).
- Redis mirror fail-open: lỗi Redis không bao giờ nổi lên caller.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.graphs import logger as glog


def test_note_llm_call_counts_context_and_global(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DB_PATH", str(tmp_path / "logs.sqlite"))
    before = glog.llm_calls_total()
    c = glog.begin_llm_count()
    glog.note_llm_call()
    glog.note_llm_call()
    assert c["calls"] == 2
    assert glog.llm_calls_total() == before + 2


def test_note_llm_call_without_context_only_global():
    glog.reset_llm_count()  # không có counter active
    before = glog.llm_calls_total()
    glog.note_llm_call()
    assert glog.llm_calls_total() == before + 1


def test_counter_propagates_through_ctx_submit():
    c = glog.begin_llm_count()
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        futs = [glog.ctx_submit(ex, glog.note_llm_call) for _ in range(3)]
        for f in futs:
            f.result(timeout=10)
    finally:
        ex.shutdown(wait=True)
    assert c["calls"] == 3


def test_plain_submit_does_not_leak_between_jobs():
    # Thread pool KHÔNG propagate contextvar → note_llm_call trong pool thường
    # chỉ tăng global, không tăng counter job (đây là lý do phải dùng ctx_submit).
    c = glog.begin_llm_count()
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        ex.submit(glog.note_llm_call).result(timeout=10)
    finally:
        ex.shutdown(wait=True)
    assert c["calls"] == 0


def test_flush_llm_count_writes_node_event(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DB_PATH", str(tmp_path / "logs.sqlite"))
    c = {"calls": 5}
    glog.flush_llm_count("job_x", c)
    events = glog.read_job_events("job_x")
    assert len(events) == 1
    assert events[0]["node"] == "LLMCalls"
    assert events[0]["metadata"] == {"llm_calls": 5}


def test_read_job_events_missing_db_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DB_PATH", str(tmp_path / "khong_co.sqlite"))
    assert glog.read_job_events("nope") == []


def test_note_llm_call_redis_error_swallowed(monkeypatch):
    class _Boom:
        def incrby(self, *a, **kw):
            raise RuntimeError("redis down")

    monkeypatch.setattr(glog, "_get_redis_safe", lambda: _Boom())
    glog.begin_llm_count()
    glog.note_llm_call()  # không được raise


def test_ask_ai_increments_counter(monkeypatch):
    from app.clients import llm_factory as lf

    monkeypatch.setattr(lf, "_gateway_addr", lambda: "")
    monkeypatch.setattr(lf, "PROVIDERS", ["ollama"])
    monkeypatch.setattr(lf, "_ollama_chat_llm", lambda *a, **kw: object())
    monkeypatch.setattr(lf, "_invoke_chat", lambda *a, **kw: "ok")
    c = glog.begin_llm_count()
    before = glog.llm_calls_total()
    out = lf.ask_ai("xin chao", feature="chat")
    assert out == "ok"
    assert c["calls"] == 1
    assert glog.llm_calls_total() == before + 1
