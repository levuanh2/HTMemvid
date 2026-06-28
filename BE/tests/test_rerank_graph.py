"""Rerank wiring trong query graph: build thật + Stage 2 lọc/đảo thứ tự chunk."""

from __future__ import annotations

import time

import app.domains.retrieval.rerank as rk
from tests._qg_build import StubChunk, base_env, build, init_state, run


class _SlowLoadReranker:
    """Reranker giả: tải model chậm (lazy trong _ensure_model), predict nhanh.

    Mô phỏng đúng cấu trúc CrossEncoderReranker để test warmup tách load ra ngoài timeout.
    """

    def __init__(self, order, load_sec: float):
        self._order = order  # thứ tự index muốn trả về
        self._load_sec = load_sec
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            time.sleep(self._load_sec)  # giả lập thời gian tải model
            self._model = object()
        return self._model

    def rerank(self, query, texts, *, top_n=None):
        self._ensure_model()
        ranked = [(i, float(len(texts) - k)) for k, i in enumerate(self._order) if 0 <= i < len(texts)]
        return ranked[:top_n] if top_n else ranked


def test_rerank_off_no_node(monkeypatch):
    base_env(monkeypatch, RERANK_ENABLED="0")
    called = {"n": 0}
    monkeypatch.setattr(rk, "rerank_texts", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    g, _ = build()
    out = run(g, init_state("hello"))
    assert called["n"] == 0  # node rerank không tồn tại khi tắt
    assert out["payload"]["answer"] == "generated answer"


def test_rerank_on_reorders_and_truncates(monkeypatch):
    base_env(monkeypatch, RERANK_ENABLED="1", RERANK_TOP_N="2", RERANK_CANDIDATE_K="10")
    chunks = [
        StubChunk("c0", stem="s0", cid=0),
        StubChunk("c1", stem="s1", cid=1),
        StubChunk("c2", stem="s2", cid=2),
        StubChunk("c3", stem="s3", cid=3),
    ]
    # Reranker chọn chunk index 3 rồi 1 (đảo thứ tự, cắt còn 2).
    monkeypatch.setattr(rk, "rerank_texts", lambda q, texts, top_n=None: [(3, 9.0), (1, 8.0)])
    g, _ = build(retriever_chunks=chunks)
    out = run(g, init_state("q"))
    assert out["retrieved_chunks"] == ["c3", "c1"]
    assert out["retrieved_sources"] == ["s3", "s1"]
    assert out["payload"]["answer"] == "generated answer"


def test_rerank_error_keeps_pipeline_alive(monkeypatch):
    base_env(monkeypatch, RERANK_ENABLED="1", RERANK_TOP_N="2")
    chunks = [StubChunk(f"c{i}", stem=f"s{i}", cid=i) for i in range(4)]

    def _boom(*a, **k):
        raise RuntimeError("rerank down")

    monkeypatch.setattr(rk, "rerank_texts", _boom)
    g, _ = build(retriever_chunks=chunks)
    out = run(g, init_state("q"))
    # Lỗi rerank → giữ ứng viên đầu, cắt top_n; vẫn trả lời được.
    assert out["retrieved_chunks"] == ["c0", "c1"]
    assert out["payload"]["answer"] == "generated answer"


def test_rerank_warmup_loads_model_outside_timeout(monkeypatch):
    """Regression: lazy-load phải xảy ra TRONG warmup (ngoài vùng timeout), không
    trong block rerank có timeout — nếu không, lần đầu (cache nguội) sẽ timeout và
    âm thầm fallback identity (mất tác dụng rerank).

    RERANK_TIMEOUT_SEC=1 nhưng model "tải" mất 2s: nếu load nằm trong timeout →
    fallback identity (giữ thứ tự gốc); nếu warmup nạp trước → predict kịp, đảo thứ tự.
    """
    base_env(monkeypatch, RERANK_ENABLED="1", RERANK_TOP_N="2", RERANK_TIMEOUT_SEC="1")
    monkeypatch.setenv("SKIP_MODEL_LOAD", "0")  # cho warmup chạy thật
    # Engine giả chọn index 2 rồi 0 (đảo thứ tự) — phân biệt rõ với fallback identity.
    engine = _SlowLoadReranker(order=[2, 0], load_sec=2.0)
    monkeypatch.setattr(rk, "get_reranker", lambda: engine)

    chunks = [StubChunk(f"c{i}", stem=f"s{i}", cid=i) for i in range(4)]
    g, _ = build(retriever_chunks=chunks)
    out = run(g, init_state("q"))

    # Load 2s đã nằm trong warmup (timeout rộng) → rerank kịp trong 1s → đảo thứ tự.
    assert out["retrieved_chunks"] == ["c2", "c0"]
    assert out["retrieved_sources"] == ["s2", "s0"]
    assert out["payload"]["answer"] == "generated answer"
