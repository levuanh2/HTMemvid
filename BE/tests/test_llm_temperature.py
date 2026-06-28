"""Per-feature temperature: factual→0, chat→0.3, options override luôn thắng."""

from __future__ import annotations

import app.clients.llm_factory as lf


def test_chat_uses_conversational_default(monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    monkeypatch.delenv("LLM_TEMPERATURE_FACTUAL", raising=False)
    assert lf._resolve_temperature("chat") == 0.3


def test_factual_features_use_zero(monkeypatch):
    monkeypatch.delenv("LLM_TEMPERATURE_FACTUAL", raising=False)
    # 'answer' = đường sinh đáp án RAG (summarize_results dùng feature này).
    for feat in ("answer", "summary", "mindmap", "grade", "classify", "extract"):
        assert lf._resolve_temperature(feat) == 0.0, feat


def test_options_temperature_overrides_feature(monkeypatch):
    # mindmap truyền options temp riêng (0.15) → phải thắng cả factual lẫn chat.
    assert lf._resolve_temperature("answer", {"temperature": 0.15}) == 0.15
    assert lf._resolve_temperature("chat", {"temperature": 0.0}) == 0.0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "0.5")
    monkeypatch.setenv("LLM_TEMPERATURE_FACTUAL", "0.1")
    assert lf._resolve_temperature("chat") == 0.5
    assert lf._resolve_temperature("answer") == 0.1


def test_summarize_results_routes_to_answer_feature(monkeypatch):
    """summarize_results phải gọi ask_ai với feature='answer' (factual temp)."""
    captured = {}

    def _fake_ask_ai(prompt, system_prompt=None, model=None, feature="chat", **kw):
        captured["feature"] = feature
        return "ok"

    monkeypatch.setattr(lf, "ask_ai", _fake_ask_ai)
    lf.summarize_results("câu hỏi?", ["đoạn 1", "đoạn 2"])
    assert captured["feature"] == "answer"
