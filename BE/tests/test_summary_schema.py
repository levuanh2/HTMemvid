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
