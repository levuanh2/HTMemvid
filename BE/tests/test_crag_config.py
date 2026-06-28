from __future__ import annotations

import shared.config as cfg


def test_crag_supervisor_hitl_env_flags(monkeypatch):
    monkeypatch.setenv("CRAG_ENABLED", "1")
    monkeypatch.setenv("CRAG_RELEVANCE_THRESHOLD", "0.7")
    monkeypatch.setenv("CRAG_WRONG_FLOOR", "0.2")
    monkeypatch.setenv("CRAG_REWRITE_MAX", "3")
    monkeypatch.setenv("SUPERVISOR_ENABLED", "true")
    monkeypatch.setenv("HITL_ENABLED", "yes")

    cfg.reload()
    settings = cfg.get_settings()

    assert settings.crag_enabled is True
    assert settings.crag_relevance_threshold == 0.7
    assert settings.crag_wrong_floor == 0.2
    assert settings.crag_rewrite_max == 3
    assert settings.supervisor_enabled is True
    assert settings.hitl_enabled is True


def test_nli_env_flags(monkeypatch):
    monkeypatch.setenv("NLI_ENABLED", "1")
    monkeypatch.setenv("NLI_MODEL", "some/model")
    monkeypatch.setenv("NLI_CONTRADICTION_THRESHOLD", "0.8")
    monkeypatch.setenv("NLI_TIMEOUT_SEC", "5")
    monkeypatch.setenv("NLI_MAX_PAIRS", "20")

    cfg.reload()
    settings = cfg.get_settings()

    assert settings.nli_enabled is True
    assert settings.nli_model == "some/model"
    assert settings.nli_contradiction_threshold == 0.8
    assert settings.nli_timeout_sec == 5
    assert settings.nli_max_pairs == 20


def test_nli_defaults(monkeypatch):
    for name in (
        "NLI_ENABLED",
        "NLI_MODEL",
        "NLI_CONTRADICTION_THRESHOLD",
        "NLI_TIMEOUT_SEC",
        "NLI_MAX_PAIRS",
    ):
        monkeypatch.delenv(name, raising=False)

    cfg.reload()
    settings = cfg.get_settings()

    assert settings.nli_enabled is False
    assert settings.nli_model == "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
    assert settings.nli_contradiction_threshold == 0.6
    assert settings.nli_timeout_sec == 10
    assert settings.nli_max_pairs == 10


def test_crag_supervisor_hitl_defaults(monkeypatch):
    for name in (
        "CRAG_ENABLED",
        "CRAG_RELEVANCE_THRESHOLD",
        "CRAG_WRONG_FLOOR",
        "CRAG_REWRITE_MAX",
        "SUPERVISOR_ENABLED",
        "HITL_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)

    cfg.reload()
    settings = cfg.get_settings()

    assert settings.crag_enabled is False
    assert settings.crag_relevance_threshold == 0.25
    assert settings.crag_wrong_floor == 0.1
    assert settings.crag_rewrite_max == 1
    assert settings.supervisor_enabled is False
    assert settings.hitl_enabled is False
