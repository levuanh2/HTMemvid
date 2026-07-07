# BE/tests/test_summary_synthesize.py
import json

import pytest

from services.summary.pipeline import synthesize as sy


_SECTIONS = [{"id": "s1", "title": "A", "summary": "tóm tắt A"},
             {"id": "s2", "title": "B", "summary": ""}]


@pytest.fixture(autouse=True)
def _no_skip(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)


def test_happy_path(monkeypatch):
    monkeypatch.setattr(sy, "_ask_json", lambda *a, **k: {
        "title": "Doc mới", "overview": "tổng quan", "entities": ["E1", " ", "E2"]})
    meta, degraded = sy.synthesize(_SECTIONS, doc_title="Doc")
    assert degraded is False
    assert meta == {"title": "Doc mới", "overview": "tổng quan", "entities": ["E1", "E2"]}


def test_failure_returns_empty_overview_degraded(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(sy, "_ask_json", boom)
    meta, degraded = sy.synthesize(_SECTIONS, doc_title="Doc")
    assert degraded is True
    assert meta == {"title": "Doc", "overview": "", "entities": []}


def test_empty_overview_counts_as_degraded(monkeypatch):
    monkeypatch.setattr(sy, "_ask_json", lambda *a, **k: {"title": "T", "overview": "  "})
    meta, degraded = sy.synthesize(_SECTIONS, doc_title="Doc")
    assert degraded is True and meta["overview"] == ""


def test_skip_model_load_degraded(monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    meta, degraded = sy.synthesize(_SECTIONS, doc_title="Doc")
    assert degraded is True and meta["title"] == "Doc"
