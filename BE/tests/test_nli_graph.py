"""NLI wiring trong query graph: build thật + VerifyContext loại chunk mâu thuẫn."""

from __future__ import annotations

import app.domains.retrieval.nli as nli
from tests._qg_build import StubChunk, base_env, build, init_state, run


def test_nli_off_no_node(monkeypatch):
    base_env(monkeypatch, NLI_ENABLED="0")
    called = {"n": 0}
    monkeypatch.setattr(nli, "detect_conflicts", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or [])
    g, _ = build()
    out = run(g, init_state("hello"))
    assert called["n"] == 0  # node VerifyContext không tồn tại khi tắt
    assert out["payload"]["answer"] == "generated answer"


def test_nli_on_drops_conflicting_chunk(monkeypatch):
    base_env(monkeypatch, NLI_ENABLED="1")
    chunks = [
        StubChunk("c0", stem="s0", cid=0),
        StubChunk("c1", stem="s1", cid=1),
        StubChunk("c2", stem="s2", cid=2),
    ]
    # NLI báo chunk 0 và 2 mâu thuẫn → giữ 0 (hạng cao), loại 2.
    monkeypatch.setattr(nli, "detect_conflicts", lambda *a, **k: [{"i": 0, "j": 2, "score": 0.9}])
    g, _ = build(retriever_chunks=chunks)
    out = run(g, init_state("q"))
    assert out["retrieved_chunks"] == ["c0", "c1"]
    assert out["retrieved_sources"] == ["s0", "s1"]
    assert out["context_conflicts"] == [{"i": 0, "j": 2, "score": 0.9}]
    assert out["payload"]["answer"] == "generated answer"


def test_nli_no_conflict_keeps_all(monkeypatch):
    base_env(monkeypatch, NLI_ENABLED="1")
    chunks = [StubChunk(f"c{i}", stem=f"s{i}", cid=i) for i in range(3)]
    monkeypatch.setattr(nli, "detect_conflicts", lambda *a, **k: [])
    g, _ = build(retriever_chunks=chunks)
    out = run(g, init_state("q"))
    assert out["retrieved_chunks"] == ["c0", "c1", "c2"]
    assert out["context_conflicts"] == []


def test_nli_error_keeps_pipeline_alive(monkeypatch):
    base_env(monkeypatch, NLI_ENABLED="1")
    chunks = [StubChunk(f"c{i}", stem=f"s{i}", cid=i) for i in range(3)]

    def _boom(*a, **k):
        raise RuntimeError("nli down")

    monkeypatch.setattr(nli, "detect_conflicts", _boom)
    g, _ = build(retriever_chunks=chunks)
    out = run(g, init_state("q"))
    # Lỗi NLI → giữ nguyên toàn bộ chunk; vẫn trả lời được.
    assert out["retrieved_chunks"] == ["c0", "c1", "c2"]
    assert out["payload"]["answer"] == "generated answer"
