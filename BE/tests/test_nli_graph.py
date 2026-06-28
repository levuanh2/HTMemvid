"""NLI wiring trong query graph: build thật + VerifyContext loại chunk mâu thuẫn."""

from __future__ import annotations

import time

import app.domains.retrieval.nli as nli
from tests._qg_build import StubChunk, base_env, build, init_state, run


class _SlowLoadNli:
    """NLI giả: tải model chậm (lazy trong _ensure_model), nhưng predict nhanh.

    Mô phỏng đúng cấu trúc MDebertaNli để test warmup tách load ra ngoài timeout.
    """

    def __init__(self, load_sec: float):
        self._load_sec = load_sec
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            time.sleep(self._load_sec)  # giả lập thời gian tải model
            self._model = object()
        return self._model

    def predict(self, pairs):
        self._ensure_model()
        # Mọi cặp đều mâu thuẫn → detect_conflicts sẽ bắt được nếu kịp chạy.
        return [{"entailment": 0.0, "neutral": 0.0, "contradiction": 1.0} for _ in pairs]


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


def test_nli_warmup_loads_model_outside_timeout(monkeypatch):
    """Regression: lazy-load phải xảy ra TRONG warmup (ngoài vùng timeout), không
    trong block detect_conflicts có timeout — nếu không, lần đầu (cache nguội) sẽ
    timeout và âm thầm bỏ qua khử mâu thuẫn.

    NLI_TIMEOUT_SEC=1 nhưng model "tải" mất 2s: nếu load nằm trong timeout → []
    (giữ cả 2 chunk); nếu warmup nạp trước → predict kịp, bắt mâu thuẫn → loại c1.
    """
    base_env(monkeypatch, NLI_ENABLED="1", NLI_TIMEOUT_SEC="1")
    monkeypatch.setenv("SKIP_MODEL_LOAD", "0")  # cho warmup chạy thật
    engine = _SlowLoadNli(load_sec=2.0)
    monkeypatch.setattr(nli, "get_nli", lambda: engine)

    chunks = [StubChunk("c0", stem="s0", cid=0), StubChunk("c1", stem="s1", cid=1)]
    g, _ = build(retriever_chunks=chunks)
    out = run(g, init_state("q"))

    # Load 2s đã nằm trong warmup (timeout rộng) → detect_conflicts kịp trong 1s.
    assert out["context_conflicts"] == [{"i": 0, "j": 1, "score": 1.0}]
    assert out["retrieved_chunks"] == ["c0"]  # c1 (hạng thấp, mâu thuẫn) bị loại
    assert out["payload"]["answer"] == "generated answer"
