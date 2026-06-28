"""Unit test cho nli module: null passthrough, phát hiện mâu thuẫn, an toàn lỗi."""

from __future__ import annotations

import shared.config as cfg
import app.domains.retrieval.nli as nli


def _reload(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    cfg.reload()
    nli.reset_cache()


class _FakeNli:
    """contradiction cao khi một bên chứa 'OLD' và bên kia 'NEW' (mô phỏng số cũ/mới)."""

    def predict(self, pairs):
        out = []
        for p, h in pairs:
            if ("OLD" in p and "NEW" in h) or ("NEW" in p and "OLD" in h):
                out.append({"entailment": 0.05, "neutral": 0.05, "contradiction": 0.9})
            else:
                out.append({"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1})
        return out


def test_null_when_disabled(monkeypatch):
    _reload(monkeypatch, NLI_ENABLED="0")
    assert isinstance(nli.get_nli(), nli.NullNli)
    assert nli.detect_conflicts(["a", "b"]) == []  # null → không xung đột


def test_null_when_skip_model_load(monkeypatch):
    _reload(monkeypatch, NLI_ENABLED="1", SKIP_MODEL_LOAD="1")
    assert isinstance(nli.get_nli(), nli.NullNli)


def test_detect_conflicts_flags_contradiction(monkeypatch):
    monkeypatch.setattr(nli, "get_nli", lambda: _FakeNli())
    chunks = ["nghỉ phép OLD 12 ngày", "thông tin nền chung", "nghỉ phép NEW 20 ngày"]
    conflicts = nli.detect_conflicts(chunks, max_pairs=10, threshold=0.6)
    assert conflicts == [{"i": 0, "j": 2, "score": 0.9}]


def test_detect_conflicts_none_below_threshold(monkeypatch):
    monkeypatch.setattr(nli, "get_nli", lambda: _FakeNli())
    # threshold cao hơn 0.9 → không cặp nào tính là xung đột.
    assert nli.detect_conflicts(["x OLD", "y NEW"], threshold=0.95) == []


def test_detect_conflicts_swallows_error(monkeypatch):
    class _Boom:
        def predict(self, pairs):
            raise RuntimeError("model down")

    monkeypatch.setattr(nli, "get_nli", lambda: _Boom())
    assert nli.detect_conflicts(["a", "b", "c"]) == []  # lỗi → passthrough


def test_detect_conflicts_too_few_chunks(monkeypatch):
    monkeypatch.setattr(nli, "get_nli", lambda: _FakeNli())
    assert nli.detect_conflicts(["chỉ một chunk"]) == []


def test_resolve_conflicts_drops_lower_rank():
    keep = nli.resolve_conflicts(3, [{"i": 0, "j": 2, "score": 0.9}])
    assert keep == [0, 1]  # giữ hạng cao (0), loại hạng thấp (2)


def test_classify_neutral_on_error(monkeypatch):
    class _Boom:
        def predict(self, pairs):
            raise RuntimeError("down")

    monkeypatch.setattr(nli, "get_nli", lambda: _Boom())
    scores = nli.classify("a", "b")
    assert scores["neutral"] == 1.0 and scores["contradiction"] == 0.0
