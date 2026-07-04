from __future__ import annotations

import json

from services.mindmap.worker import collect_chunks_for_sources
from services.mindmap.jsonrepair import repair_json_text as _repair_json_text


def _meta(entries):
    m = {str(i): e for i, e in enumerate(entries)}
    m["__meta__"] = {"version": "1.1", "num_chunks": len(entries)}
    return m


def test_space_filename_matches_sanitized_video():
    meta = _meta([{"text": "a", "video": "videos/My_Report_pdf_20260628_120000.mp4"}])
    assert [c["key"] for c in collect_chunks_for_sources(meta, ["my report_pdf"])] == ["0"]
    assert [c["key"] for c in collect_chunks_for_sources(meta, ["My Report.pdf"])] == ["0"]


def test_prefers_source_stem_field():
    meta = _meta([{"text": "a", "video": "videos/unrelated_20260628_120000.mp4", "source_stem": "my_report_pdf"}])
    assert [c["key"] for c in collect_chunks_for_sources(meta, ["My Report.pdf"])] == ["0"]


def test_vietnamese_diacritics():
    meta = _meta([{"text": "a", "video": "videos/Báo_cáo_pdf_20260628_120000.mp4"}])
    assert [c["key"] for c in collect_chunks_for_sources(meta, ["Báo cáo.pdf"])] == ["0"]


def test_wrong_name_excluded():
    meta = _meta([{"text": "a", "video": "videos/My_Report_pdf_20260628_120000.mp4"}])
    assert collect_chunks_for_sources(meta, ["khac.pdf"]) == []


def test_empty_sources_returns_empty():
    meta = _meta([{"text": "a", "video": "videos/My_Report_pdf_20260628_120000.mp4"}])
    assert collect_chunks_for_sources(meta, []) == []


def test_multi_source_selects_correctly():
    meta = _meta([
        {"text": "a", "video": "videos/A_pdf_20260628_120000.mp4"},
        {"text": "b", "video": "videos/B_pdf_20260628_120000.mp4"},
    ])
    assert [c["key"] for c in collect_chunks_for_sources(meta, ["A.pdf"])] == ["0"]
    assert sorted(c["key"] for c in collect_chunks_for_sources(meta, ["A.pdf", "B.pdf"])) == ["0", "1"]


def test_ignores_meta_entry_and_blank_video():
    meta = {
        "0": {"text": "a", "video": "videos/A_pdf_20260628_120000.mp4"},
        "1": {"text": "x", "video": ""},
        "__meta__": {"version": "1.1", "num_chunks": 2},
    }
    assert [c["key"] for c in collect_chunks_for_sources(meta, ["A.pdf"])] == ["0"]


def test_repair_strips_code_fence_and_extracts_object():
    raw = "```json\n{\"a\": 1}\n``` rác phía sau"
    assert json.loads(_repair_json_text(raw)) == {"a": 1}


def test_repair_removes_trailing_commas_outside_strings():
    raw = '{"branches": [1, 2, 3,], "x": {"y": 1,},}'
    assert json.loads(_repair_json_text(raw)) == {"branches": [1, 2, 3], "x": {"y": 1}}


def test_repair_keeps_comma_inside_string():
    raw = '{"a": "x,]", "b": "y,}"}'
    assert json.loads(_repair_json_text(raw)) == {"a": "x,]", "b": "y,}"}
