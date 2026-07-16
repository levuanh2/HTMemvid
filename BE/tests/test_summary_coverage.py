# BE/tests/test_summary_coverage.py — coverage judge (Phase 5): pure logic + fake LLM
from services.summary.pipeline import coverage as cov


# --- sanitize_coverage ---

def test_sanitize_coverage_accepts_valid_diagnostics():
    out = cov.sanitize_coverage({
        "covered": ["A", "B"], "missing": ["C"], "unsupported": [],
        "vague": True, "notes": ["n1"],
    })
    assert out == {"covered": ["A", "B"], "missing": ["C"], "unsupported": [],
                   "vague": True, "notes": ["n1"]}


def test_sanitize_coverage_coerces_list_items_to_strings():
    out = cov.sanitize_coverage({"covered": [1, 2.5, "x", ""]})
    assert out["covered"] == ["1", "2.5", "x"]   # str + drop empty/whitespace


def test_sanitize_coverage_str_none_is_kept_as_text_but_empty_dropped():
    # str(None) = "None" (non-empty) → kept; only whitespace/empty dropped.
    out = cov.sanitize_coverage({"covered": [None, "", "  "]})
    assert out["covered"] == ["None"]


def test_sanitize_coverage_caps_long_lists():
    out = cov.sanitize_coverage({"covered": [str(i) for i in range(100)]})
    assert len(out["covered"]) == cov._MAX_LIST


def test_sanitize_coverage_vague_coerces_to_bool():
    assert cov.sanitize_coverage({"vague": "yes"})["vague"] is True
    assert cov.sanitize_coverage({"vague": 0})["vague"] is False
    assert cov.sanitize_coverage({})["vague"] is False       # missing → False


def test_sanitize_coverage_drops_unknown_keys_and_bad_lists():
    out = cov.sanitize_coverage({"covered": "not-a-list", "junk": ["x"]})
    assert set(out.keys()) == set(cov.COVERAGE_KEYS)
    assert out["covered"] == []                  # non-list → []
    assert "junk" not in out


def test_sanitize_coverage_non_dict_returns_none():
    assert cov.sanitize_coverage(None) is None
    assert cov.sanitize_coverage(["a"]) is None
    assert cov.sanitize_coverage("x") is None


def test_sanitize_coverage_caps_item_length():
    long = "x" * 1000
    out = cov.sanitize_coverage({"notes": [long]})
    assert len(out["notes"][0]) == cov._MAX_ITEM_CHARS


# --- build_coverage_payload ---

def test_build_coverage_payload_extracts_source_backed_artifacts():
    rec = {
        "overview": "ov",
        "sections": [{"title": "A", "summary": "s", "key_points": ["k1", ""],
                      "facts": {"definitions": ["d1"], "formulas": []},
                      "chunk_refs": ["0"], "pointers": [{"chunk_id": "0", "page": 3}]}],
        "study": {"key_concepts": ["A"], "recommended_review": [{"chunk_id": "0"}]},
    }
    p = cov.build_coverage_payload(rec)
    assert p["overview"] == "ov"
    s = p["sections"][0]
    assert s["title"] == "A" and s["summary"] == "s"
    assert s["key_points"] == ["k1"]
    assert s["facts"] == {"definitions": ["d1"]}   # empty formulas dropped
    # source pointers / chunk_refs NOT sent to the model (no page/id leakage into judge)
    assert "pointers" not in s and "chunk_refs" not in s
    assert p["study"] == {"key_concepts": ["A"]}


def test_build_coverage_payload_non_dict_safe():
    assert cov.build_coverage_payload(None) == {"overview": "", "sections": []}


# --- build_coverage_prompt ---

def test_build_coverage_prompt_has_json_keys_and_no_rewrite_rule():
    prompt = cov.build_coverage_prompt({"overview": "x", "sections": []})
    for key in cov.COVERAGE_KEYS:
        assert key in prompt
    assert "Do not rewrite" in prompt
    assert "Return JSON only" in prompt
    assert '"overview": "x"' in prompt     # payload embedded


# --- judge_coverage with fake LLM ---

def _rec():
    return {"overview": "ov", "sections": [{"title": "A", "summary": "s", "key_points": ["k"]}]}


def test_judge_disabled_returns_none_and_never_calls_llm():
    called = []
    out = cov.judge_coverage(_rec(), ask_fn=lambda p: called.append(p) or "{}", enabled=False)
    assert out is None
    assert called == []


def test_judge_valid_json_parsed():
    raw = '{"covered": ["A"], "missing": [], "unsupported": [], "vague": false, "notes": []}'
    out = cov.judge_coverage(_rec(), ask_fn=lambda p: raw, enabled=True)
    assert out == {"covered": ["A"], "missing": [], "unsupported": [],
                   "vague": False, "notes": []}


def test_judge_malformed_json_degrades_to_none():
    out = cov.judge_coverage(_rec(), ask_fn=lambda p: "not json at all {", enabled=True)
    assert out is None


def test_judge_llm_raises_does_not_propagate():
    def boom(_p):
        raise RuntimeError("model down")
    out = cov.judge_coverage(_rec(), ask_fn=boom, enabled=True)
    assert out is None      # judge failure never fails the summary


def test_judge_does_not_mutate_record():
    rec = _rec()
    before = dict(rec)
    cov.judge_coverage(rec, ask_fn=lambda p: '{"covered": ["A"]}', enabled=True)
    assert rec == before    # no auto-repair / no summary mutation
