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


# --- Summary v3 facts ledger (with_facts=True) ---

def _facts_payload(refs, facts=None):
    return json.dumps({
        "facts": facts if facts is not None else {
            "key_points": ["kp"], "definitions": ["def A"], "formulas": ["E=mc^2"],
            "examples": ["vd 1"], "important_terms": ["T"], "common_mistakes": ["nhầm X"],
            "open_questions": ["còn gì?"]},
        "summary": "tóm tắt", "key_points": ["ý 1"], "chunk_keys": refs})


def test_with_facts_parses_all_seven_keys(monkeypatch):
    monkeypatch.setattr(sz, "ask_ai", lambda *a, **k: _facts_payload(["0"]))
    out, missing = sz.summarize_sections(_MM, _SECTIONS[:1], with_facts=True,
                                         timeout_sec=5, max_workers=1)
    assert missing == []
    assert set(out[0]["facts"].keys()) == set(sz._FACTS_KEYS)
    assert out[0]["facts"]["formulas"] == ["E=mc^2"]


def test_with_facts_false_omits_facts_and_keeps_old_shape(monkeypatch):
    monkeypatch.setattr(sz, "ask_ai", lambda *a, **k: _facts_payload(["0"]))
    out, _ = sz.summarize_sections(_MM, _SECTIONS[:1], with_facts=False,
                                   timeout_sec=5, max_workers=1)
    assert "facts" not in out[0]
    assert out[0]["summary"] == "tóm tắt"
    assert out[0]["key_points"] == ["ý 1"]


def test_with_facts_coerces_and_drops_empty(monkeypatch):
    payload = _facts_payload(["0"], facts={"key_points": ["a", "", 7], "definitions": [],
                                           "junk": ["z"]})
    monkeypatch.setattr(sz, "ask_ai", lambda *a, **k: payload)
    out, _ = sz.summarize_sections(_MM, _SECTIONS[:1], with_facts=True,
                                   timeout_sec=5, max_workers=1)
    assert out[0]["facts"] == {"key_points": ["a", "7"]}


def test_with_facts_hallucinated_chunk_keys_filtered(monkeypatch):
    monkeypatch.setattr(sz, "ask_ai", lambda *a, **k: _facts_payload(["999"]))
    out, _ = sz.summarize_sections(_MM, _SECTIONS[:1], with_facts=True,
                                   timeout_sec=5, max_workers=1)
    assert out[0]["chunk_refs"] == ["0"]   # bogus filtered → skeleton ref giữ nguyên


def test_with_facts_malformed_then_valid_retries_once(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return "{broken" if calls["n"] == 1 else _facts_payload(["0"])

    monkeypatch.setattr(sz, "ask_ai", flaky)
    out, missing = sz.summarize_sections(_MM, _SECTIONS[:1], with_facts=True,
                                         timeout_sec=5, max_workers=1)
    assert missing == []
    assert calls["n"] == 2
    assert out[0]["facts"]["key_points"] == ["kp"]


def test_with_facts_persistent_failure_degrades_without_fabricating(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(sz, "ask_ai", boom)
    out, missing = sz.summarize_sections(_MM, _SECTIONS[:1], with_facts=True,
                                         timeout_sec=5, max_workers=1)
    assert missing == ["section:A"]
    assert out[0]["summary"] == ""
    assert "facts" not in out[0]   # lỗi → không bịa facts


def test_two_pass_seam_raises_not_implemented(monkeypatch):
    monkeypatch.setattr(sz, "ask_ai", lambda *a, **k: _facts_payload(["0"]))
    with pytest.raises(NotImplementedError):
        sz._summarize_one(_MM, _SECTIONS[0], None, 5, "medium",
                          with_facts=True, two_pass=True)


def test_facts_prompt_contains_seven_keys_and_length_rule(monkeypatch):
    seen = {}

    def capture(prompt, system_prompt=None, **k):
        seen["system"] = system_prompt
        return _facts_payload(["0"])

    monkeypatch.setattr(sz, "ask_ai", capture)
    sz.summarize_sections(_MM, _SECTIONS[:1], with_facts=True, length_mode="short",
                          timeout_sec=5, max_workers=1)
    for key in sz._FACTS_KEYS:
        assert key in seen["system"]
    assert "2-3 câu" in seen["system"]
