# BE/tests/test_summary_summarize.py — per-section LLM, monkeypatch ask_ai (không model thật)
import json

import pytest

from services.summary.pipeline import summarize as sz


_MM = {"title": "Doc", "sources": ["a_docx"],
       "chunks": [{"key": "0", "text": "nội dung 0", "heading_path": "1. A", "chunk_keys": ["0"]},
                  {"key": "1", "text": "nội dung 1", "heading_path": "2. B", "chunk_keys": ["1"]}]}
_SECTIONS = [{"id": "s1", "title": "A", "chunk_refs": ["0"], "order": 0},
             {"id": "s2", "title": "B", "chunk_refs": ["1"], "order": 1}]


@pytest.fixture(autouse=True)
def _no_skip(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)


def _ok_payload(refs):
    return json.dumps({"summary": "tóm tắt", "key_points": ["ý 1"], "chunk_keys": refs})


def test_happy_path_all_sections_summarized(monkeypatch):
    monkeypatch.setattr(sz, "ask_ai", lambda *a, **k: _ok_payload(["0"]))
    out, missing = sz.summarize_sections(_MM, _SECTIONS, timeout_sec=5, max_workers=1)
    assert missing == []
    assert all(s["summary"] == "tóm tắt" for s in out)
    assert all(s["key_points"] == ["ý 1"] for s in out)


def test_hallucinated_chunk_keys_filtered_keeps_skeleton_refs(monkeypatch):
    # LLM trả id không nằm trong allowed → lọc hết → giữ refs skeleton
    monkeypatch.setattr(sz, "ask_ai", lambda *a, **k: _ok_payload(["999"]))
    out, missing = sz.summarize_sections(_MM, _SECTIONS, timeout_sec=5, max_workers=1)
    assert missing == []
    assert out[0]["chunk_refs"] == ["0"]
    assert out[1]["chunk_refs"] == ["1"]


def test_malformed_then_valid_json_retries_once(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return "{broken" if calls["n"] == 1 else _ok_payload([])

    monkeypatch.setattr(sz, "ask_ai", flaky)
    out, missing = sz.summarize_sections(_MM, _SECTIONS[:1], timeout_sec=5, max_workers=1)
    assert missing == []
    assert out[0]["summary"] == "tóm tắt"
    assert calls["n"] == 2


def test_persistent_failure_keeps_skeleton_and_marks_missing(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(sz, "ask_ai", boom)
    out, missing = sz.summarize_sections(_MM, _SECTIONS, timeout_sec=5, max_workers=1)
    assert set(missing) == {"section:A", "section:B"}
    assert all(s["summary"] == "" for s in out)  # degraded trung thực, không bịa


def test_cancel_before_start_skips_all_llm_calls(monkeypatch):
    called = {"n": 0}

    def count(*a, **k):
        called["n"] += 1
        return _ok_payload([])

    monkeypatch.setattr(sz, "ask_ai", count)
    out, missing = sz.summarize_sections(_MM, _SECTIONS, timeout_sec=5,
                                         cancel_cb=lambda: True)
    assert called["n"] == 0
    assert all(s["summary"] == "" for s in out)


def test_skip_model_load_returns_all_missing(monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    out, missing = sz.summarize_sections(_MM, _SECTIONS)
    assert set(missing) == {"section:A", "section:B"}
    assert all(s["summary"] == "" for s in out)


def test_length_mode_changes_prompt_rule(monkeypatch):
    seen = {}

    def capture(prompt, system_prompt=None, **k):
        seen["system"] = system_prompt
        return _ok_payload([])

    monkeypatch.setattr(sz, "ask_ai", capture)
    sz.summarize_sections(_MM, _SECTIONS[:1], length_mode="short", timeout_sec=5)
    assert "2-3 câu" in seen["system"]
    sz.summarize_sections(_MM, _SECTIONS[:1], length_mode="detailed", timeout_sec=5)
    assert "2-3 đoạn" in seen["system"]
