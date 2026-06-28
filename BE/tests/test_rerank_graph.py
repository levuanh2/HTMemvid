"""Rerank wiring trong query graph: build thật + Stage 2 lọc/đảo thứ tự chunk."""

from __future__ import annotations

import app.domains.retrieval.rerank as rk
from tests._qg_build import StubChunk, base_env, build, init_state, run


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
