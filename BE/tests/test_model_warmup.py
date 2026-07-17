"""PR#5 — warmup dùng model ĐÃ CẤU HÌNH (một nguồn sự thật _model_map),
hết stale hardcode; fail-open; tắt được. Không Ollama thật — thread bị chặn
bằng monkeypatch, chỉ quan sát list model được lên lịch.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def be(client):
    import app.main as main
    return main


@pytest.fixture()
def no_thread(be, monkeypatch):
    """Chặn thread warmup thật (không bắn HTTP trong test)."""
    started = []

    class DummyThread:
        def __init__(self, target=None, daemon=None, **kw):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(be.threading, "Thread", DummyThread)
    return started


def test_warmup_models_follow_env_config(be, monkeypatch):
    monkeypatch.setenv("SLM_MODEL_CHAT", "chat-model:1b")
    monkeypatch.setenv("SLM_MODEL_SUMMARY", "summary-model:2b")
    monkeypatch.setenv("MINDMAP_MODEL", "mindmap-model:3b")
    assert be._warmup_model_names() == [
        "chat-model:1b", "summary-model:2b", "mindmap-model:3b",
    ]


def test_warmup_models_dedupe_same_tag(be, monkeypatch):
    # Compose mặc định: cả 3 feature cùng qwen2.5:7b-instruct → warm MỘT lần.
    for var in ("SLM_MODEL_CHAT", "SLM_MODEL", "SLM_MODEL_SUMMARY", "MINDMAP_MODEL"):
        monkeypatch.setenv(var, "qwen2.5:7b-instruct")
    assert be._warmup_model_names() == ["qwen2.5:7b-instruct"]


def test_warmup_models_no_stale_hardcode(be, monkeypatch):
    # Env cấu hình model khác → list KHÔNG được chứa default cũ qwen3.5:9b.
    monkeypatch.setenv("SLM_MODEL_CHAT", "phi4:latest")
    monkeypatch.setenv("SLM_MODEL_SUMMARY", "phi4:latest")
    monkeypatch.setenv("MINDMAP_MODEL", "phi4:latest")
    assert "qwen3.5:9b" not in be._warmup_model_names()


def test_warmup_disabled_schedules_nothing(be, monkeypatch, no_thread):
    monkeypatch.setenv("OLLAMA_WARMUP", "0")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    assert be._warmup_ollama_background() == []
    assert no_thread == []  # không thread nào được tạo


def test_warmup_no_host_schedules_nothing(be, monkeypatch, no_thread):
    monkeypatch.delenv("OLLAMA_WARMUP", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "")
    assert be._warmup_ollama_background() == []
    assert no_thread == []


def test_warmup_enabled_schedules_configured_models(be, monkeypatch, no_thread):
    monkeypatch.delenv("OLLAMA_WARMUP", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    monkeypatch.setenv("SLM_MODEL_CHAT", "m-chat")
    monkeypatch.setenv("SLM_MODEL_SUMMARY", "m-sum")
    monkeypatch.setenv("MINDMAP_MODEL", "m-mm")
    scheduled = be._warmup_ollama_background()
    assert scheduled == ["m-chat", "m-sum", "m-mm"]
    assert len(no_thread) == 1  # đúng một background thread


def test_warmup_run_fail_open(be, monkeypatch):
    # Thread thật chạy với host không tồn tại → nuốt lỗi, không raise.
    monkeypatch.delenv("OLLAMA_WARMUP", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")  # cổng chết, fail nhanh
    monkeypatch.setenv("MODEL_WARMUP_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("SLM_MODEL_CHAT", "m")
    monkeypatch.setenv("SLM_MODEL_SUMMARY", "m")
    monkeypatch.setenv("MINDMAP_MODEL", "m")
    captured = {}

    class InlineThread:
        def __init__(self, target=None, daemon=None, **kw):
            captured["target"] = target

        def start(self):
            captured["target"]()  # chạy đồng bộ trong test — exception sẽ nổi lên nếu có

    monkeypatch.setattr(be.threading, "Thread", InlineThread)
    assert be._warmup_ollama_background() == ["m"]  # không raise = fail-open OK
