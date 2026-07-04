from services.mindmap.pipeline.modelcfg import resolve_mindmap_model


def test_mindmap_model_env_wins(monkeypatch):
    monkeypatch.setenv("MINDMAP_MODEL", "custom-model")
    monkeypatch.setenv("SLM_MODEL", "other-model")
    assert resolve_mindmap_model() == "custom-model"


def test_mindmap_model_falls_back_to_slm_model(monkeypatch):
    monkeypatch.delenv("MINDMAP_MODEL", raising=False)
    monkeypatch.setenv("SLM_MODEL", "qwen3.5:9b")
    assert resolve_mindmap_model() == "qwen3.5:9b"


def test_mindmap_model_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("MINDMAP_MODEL", raising=False)
    monkeypatch.delenv("SLM_MODEL", raising=False)
    assert resolve_mindmap_model() == "qwen2.5:14b"
