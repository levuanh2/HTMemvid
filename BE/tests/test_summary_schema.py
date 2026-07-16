# BE/tests/test_summary_schema.py
from services.summary.pipeline import schema as sm


def test_content_hash_changes_with_every_input():
    base = sm.content_hash(["a"], ["t1"], ["h1"], "medium")
    assert sm.content_hash(["a"], ["t1"], ["h1"], "medium") == base
    assert sm.content_hash(["b"], ["t1"], ["h1"], "medium") != base
    assert sm.content_hash(["a"], ["t2"], ["h1"], "medium") != base
    assert sm.content_hash(["a"], ["t1"], ["h2"], "medium") != base
    assert sm.content_hash(["a"], ["t1"], ["h1"], "short") != base


def test_content_hash_includes_pipeline_version(monkeypatch):
    h1 = sm.content_hash(["a"], ["t"], [], "medium")
    monkeypatch.setattr(sm, "PIPELINE_VERSION", "summary_sections_v999")
    assert sm.content_hash(["a"], ["t"], [], "medium") != h1


def test_sanitize_sections_filters_bogus_refs_and_dedupes():
    out = sm.sanitize_sections([
        {"id": "s1", "title": "Mục 1", "summary": " ok ", "chunk_refs": ["0", "999", 0],
         "key_points": ["a", "", "b"]},
        {"id": "s1", "title": "Trùng id"},
        {"id": "s3", "title": "  "},  # title rỗng → bỏ
    ], valid_chunk_ids={"0", "1"})
    assert len(out) == 1
    assert out[0]["chunk_refs"] == ["0"]
    assert out[0]["key_points"] == ["a", "b"]
    assert out[0]["summary"] == "ok"


def test_build_record_shape_and_degraded():
    rec = sm.build_record(title="T", sources=["a"], length_mode="short", overview="ov",
                          sections=[{"id": "s1", "title": "M", "summary": "s",
                                     "key_points": [], "chunk_refs": [], "order": 0}],
                          entities=["E", " ", "E2"], content_hash_value="h" * 64,
                          model="m", elapsed_sec=1.23, degraded_missing=["synthesize"],
                          skeleton_method="headings")
    assert rec["schema_version"] == 2
    assert rec["length_mode"] == "short"
    assert rec["entities"] == ["E", "E2"]
    assert rec["generator"]["degraded"] is True
    assert rec["generator"]["missing"] == ["synthesize"]
    assert rec["generator"]["skeleton_method"] == "headings"
    assert rec["created_at"].endswith("Z")


def test_build_record_invalid_mode_falls_back_medium():
    rec = sm.build_record(title="T", sources=[], length_mode="bogus", overview="",
                          sections=[], entities=[], content_hash_value="h",
                          model="m", elapsed_sec=0, degraded_missing=[])
    assert rec["length_mode"] == "medium"


# --- Summary v3 facts ledger ---

def test_pipeline_version_bumped_to_v4():
    assert sm.PIPELINE_VERSION == "summary_sections_v4"


def test_sanitize_facts_coerces_strings_and_drops_empty_and_unknown():
    facts = sm.sanitize_facts({
        "key_points": ["a", "", "  b  ", 3],   # coerce str + strip + drop empty
        "definitions": [],                     # rỗng → bỏ key
        "formulas": "not-a-list",              # không phải list → bỏ
        "junk_key": ["x"],                     # ngoài FACTS_KEYS → bỏ
    })
    assert facts["key_points"] == ["a", "b", "3"]
    assert "definitions" not in facts
    assert "formulas" not in facts
    assert "junk_key" not in facts


def test_sanitize_facts_non_dict_returns_empty():
    assert sm.sanitize_facts(None) == {}
    assert sm.sanitize_facts(["a"]) == {}
    assert sm.sanitize_facts("x") == {}


def test_sanitize_facts_caps_items():
    facts = sm.sanitize_facts({"key_points": [str(i) for i in range(50)]})
    assert len(facts["key_points"]) == sm.MAX_FACT_ITEMS


def test_sanitize_sections_preserves_valid_facts():
    out = sm.sanitize_sections([
        {"id": "s1", "title": "M", "summary": "s", "chunk_refs": ["0"],
         "facts": {"definitions": ["D1"], "formulas": [], "important_terms": ["T1", ""]}},
    ], valid_chunk_ids={"0"})
    assert out[0]["facts"] == {"definitions": ["D1"], "important_terms": ["T1"]}


def test_sanitize_sections_without_facts_omits_key_backcompat():
    # section v2 (không facts) đi qua sanitize vẫn hợp lệ, không sinh key rỗng
    out = sm.sanitize_sections([
        {"id": "s1", "title": "M", "summary": "s", "chunk_refs": []},
    ], valid_chunk_ids=set())
    assert "facts" not in out[0]


def test_build_record_includes_section_facts_when_present():
    rec = sm.build_record(title="T", sources=["a"], length_mode="medium", overview="ov",
                          sections=[{"id": "s1", "title": "M", "summary": "s", "key_points": [],
                                     "chunk_refs": [], "order": 0,
                                     "facts": {"key_points": ["kp"]}}],
                          entities=[], content_hash_value="h", model="m",
                          elapsed_sec=0, degraded_missing=[])
    assert rec["sections"][0]["facts"] == {"key_points": ["kp"]}


# --- Summary v3 Phase 2: source pointers ---

def test_sanitize_pointers_keeps_known_keys_dedupes_and_drops_no_id():
    out = sm.sanitize_pointers([
        {"chunk_id": "0", "source_id": "doc-a", "source_stem": "doc_a", "page": 3,
         "heading_path": ["Ch 1", "1. Intro"], "chunk_index": 12, "junk": "x"},
        {"chunk_id": "0", "page": 9},   # trùng chunk_id → bỏ
        {"page": 5},                    # thiếu chunk_id → bỏ
    ])
    assert len(out) == 1
    p = out[0]
    assert set(p.keys()) == set(sm.POINTER_KEYS)   # field "junk" bị bỏ
    assert p["section_title"] == "1. Intro"        # mục cuối heading_path
    assert p["heading_path"] == ["Ch 1", "1. Intro"]
    assert p["page"] == 3 and p["chunk_index"] == 12
    assert p["source_id"] == "doc-a" and p["source_stem"] == "doc_a"


def test_sanitize_pointers_missing_metadata_is_none_safe():
    out = sm.sanitize_pointers([{"chunk_id": "5"}])
    assert out == [{"chunk_id": "5", "source_id": None, "source_stem": None, "page": None,
                    "section_title": None, "heading_path": [], "chunk_index": None}]


def test_sanitize_pointers_non_list_returns_empty():
    assert sm.sanitize_pointers(None) == []
    assert sm.sanitize_pointers({"chunk_id": "0"}) == []


def test_sanitize_sections_preserves_pointers_and_keeps_chunk_refs():
    out = sm.sanitize_sections([
        {"id": "s1", "title": "M", "summary": "s", "chunk_refs": ["0", "999"],
         "pointers": [{"chunk_id": "0", "page": 2, "heading_path": ["A", "B"]}]},
    ], valid_chunk_ids={"0"})
    assert out[0]["chunk_refs"] == ["0"]           # chunk_refs vẫn lọc id thật (không đổi)
    assert out[0]["pointers"][0]["chunk_id"] == "0"
    assert out[0]["pointers"][0]["page"] == 2
    assert out[0]["pointers"][0]["section_title"] == "B"


def test_sanitize_sections_without_pointers_omits_key_backcompat():
    out = sm.sanitize_sections([
        {"id": "s1", "title": "M", "summary": "s", "chunk_refs": []},
    ], valid_chunk_ids=set())
    assert "pointers" not in out[0]


# --- Summary v3 Phase 3: mode axis (standard | study) ---

def test_content_hash_includes_mode():
    base = sm.content_hash(["a"], ["t"], ["h"], "medium")            # default standard
    assert sm.content_hash(["a"], ["t"], ["h"], "medium", "standard") == base
    assert sm.content_hash(["a"], ["t"], ["h"], "medium", "study") != base


def test_build_record_defaults_mode_standard_and_no_study_block():
    rec = sm.build_record(title="T", sources=[], length_mode="medium", overview="",
                          sections=[], entities=[], content_hash_value="h",
                          model="m", elapsed_sec=0, degraded_missing=[])
    assert rec["mode"] == "standard"      # thiếu mode → standard
    assert "study" not in rec             # standard → không có block study


def test_build_record_invalid_mode_falls_back_standard():
    rec = sm.build_record(title="T", sources=[], length_mode="medium", overview="",
                          sections=[], entities=[], content_hash_value="h",
                          model="m", elapsed_sec=0, degraded_missing=[], mode="bogus")
    assert rec["mode"] == "standard"


def test_build_record_study_mode_includes_study_block():
    rec = sm.build_record(title="T", sources=[], length_mode="medium", overview="",
                          sections=[], entities=[], content_hash_value="h",
                          model="m", elapsed_sec=0, degraded_missing=[],
                          mode="study", study={"key_concepts": ["A"], "self_check": []})
    assert rec["mode"] == "study"
    assert rec["study"] == {"key_concepts": ["A"], "self_check": []}


def test_build_record_study_none_omits_block_even_in_study_mode():
    rec = sm.build_record(title="T", sources=[], length_mode="medium", overview="",
                          sections=[], entities=[], content_hash_value="h",
                          model="m", elapsed_sec=0, degraded_missing=[],
                          mode="study", study=None)
    assert rec["mode"] == "study"
    assert "study" not in rec
