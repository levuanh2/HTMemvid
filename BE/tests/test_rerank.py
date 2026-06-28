"""Unit test cho rerank module: identity fallback, cross-encoder reorder, an toàn lỗi."""

from __future__ import annotations

import shared.config as cfg
import app.domains.retrieval.rerank as rk


def _reload(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    cfg.reload()
    rk.reset_cache()


def test_identity_when_disabled(monkeypatch):
    _reload(monkeypatch, RERANK_ENABLED="0")
    r = rk.get_reranker()
    assert isinstance(r, rk.IdentityReranker)
    out = rk.rerank_texts("q", ["a", "b", "c"], top_n=2)
    assert out == [(0, 0.0), (1, 0.0)]  # giữ nguyên thứ tự, cắt top_n


def test_identity_when_skip_model_load(monkeypatch):
    _reload(monkeypatch, RERANK_ENABLED="1", SKIP_MODEL_LOAD="1")
    assert isinstance(rk.get_reranker(), rk.IdentityReranker)


def test_cross_encoder_reorders_by_score(monkeypatch):
    # Fake model: điểm = vị trí ký tự 'x' (đoạn nhiều 'x' hơn = liên quan hơn).
    class _FakeModel:
        def predict(self, pairs, batch_size=16):
            return [float(t.count("x")) for _q, t in pairs]

    ce = rk.CrossEncoderReranker("fake", batch_size=4)
    monkeypatch.setattr(ce, "_ensure_model", lambda: _FakeModel())
    ranked = ce.rerank("query", ["", "xx", "x", "xxx"], top_n=2)
    assert [i for i, _ in ranked] == [3, 1]  # 'xxx' rồi 'xx'


def test_rerank_texts_swallows_predict_error(monkeypatch):
    _reload(monkeypatch, RERANK_ENABLED="1")

    class _Boom:
        def rerank(self, *a, **k):
            raise RuntimeError("model down")

    monkeypatch.setattr(rk, "get_reranker", lambda: _Boom())
    out = rk.rerank_texts("q", ["a", "b", "c"], top_n=2)
    assert out == [(0, 0.0), (1, 0.0)]  # fallback identity, không ném lỗi


def test_invalid_backend_falls_back_to_identity(monkeypatch):
    _reload(monkeypatch, RERANK_ENABLED="1", RERANK_BACKEND="nonsense")
    assert isinstance(rk.get_reranker(), rk.IdentityReranker)
