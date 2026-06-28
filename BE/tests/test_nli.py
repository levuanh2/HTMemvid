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


class _CountingNli:
    """Đếm số lần load + forward để kiểm warmup chỉ warm MỘT lần."""

    def __init__(self):
        self.loads = 0
        self.predicts = 0
        self._model = None
        self._warmed = False

    def _ensure_model(self):
        if self._model is None:
            self.loads += 1
            self._model = object()
        return self._model

    def predict(self, pairs):
        self._ensure_model()
        self.predicts += 1
        return [{"entailment": 0.0, "neutral": 1.0, "contradiction": 0.0} for _ in pairs]


def test_warmup_runs_once_not_every_call(monkeypatch):
    """Regression: warmup KHÔNG được forward mồi lại mỗi lần gọi (sẽ tốn ~giây/query)."""
    eng = _CountingNli()
    monkeypatch.setattr(nli, "get_nli", lambda: eng)
    monkeypatch.setenv("SKIP_MODEL_LOAD", "0")

    nli.warmup()
    nli.warmup()
    nli.warmup()

    assert eng.loads == 1      # load model đúng 1 lần
    assert eng.predicts == 1   # forward mồi đúng 1 lần (cờ _warmed chặn các lần sau)
    assert eng._warmed is True


def test_warmup_noop_when_skip_model_load(monkeypatch):
    eng = _CountingNli()
    monkeypatch.setattr(nli, "get_nli", lambda: eng)
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    nli.warmup()
    assert eng.loads == 0 and eng.predicts == 0  # SKIP → không chạm model


def test_classify_neutral_on_error(monkeypatch):
    class _Boom:
        def predict(self, pairs):
            raise RuntimeError("down")

    monkeypatch.setattr(nli, "get_nli", lambda: _Boom())
    scores = nli.classify("a", "b")
    assert scores["neutral"] == 1.0 and scores["contradiction"] == 0.0
