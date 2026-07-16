# BE/tests/test_summary_pointers.py — build_pointers thuần (0 LLM, deterministic)
from services.summary.pipeline.pointers import attach_pointers, build_pointers


def _mm():
    return {"title": "Doc", "sources": ["doc_a"], "chunks": [
        {"key": "0", "text": "t0", "heading_path": "Chương 1 > 1. Mở đầu",
         "chunk_keys": ["0"], "source_id": "document-a", "source_stem": "doc_a",
         "page": 3, "chunk_index": 12},
        {"key": "1", "text": "t1", "heading_path": "2. Phương pháp",
         "chunk_keys": ["1", "1b"], "source_stem": "doc_a"},   # sub-key 1b; no page/source_id
    ]}


def test_maps_metadata_and_heading_to_pointer():
    p = build_pointers(_mm(), ["0"])
    assert p == [{"chunk_id": "0", "source_id": "document-a", "source_stem": "doc_a",
                  "page": 3, "section_title": "1. Mở đầu",
                  "heading_path": ["Chương 1", "1. Mở đầu"], "chunk_index": 12}]


def test_section_title_is_last_heading_item():
    p = build_pointers(_mm(), ["1"])
    assert p[0]["section_title"] == "2. Phương pháp"
    assert p[0]["heading_path"] == ["2. Phương pháp"]


def test_sub_key_resolves_to_parent_metadata():
    p = build_pointers(_mm(), ["1b"])
    assert p[0]["chunk_id"] == "1b"
    assert p[0]["source_stem"] == "doc_a"
    assert p[0]["section_title"] == "2. Phương pháp"


def test_missing_metadata_is_none():
    p = build_pointers(_mm(), ["1"])
    assert p[0]["page"] is None
    assert p[0]["source_id"] is None


def test_unknown_ids_ignored_no_hallucination():
    assert build_pointers(_mm(), ["999"]) == []


def test_dedupe_preserves_first_and_input_order():
    p = build_pointers(_mm(), ["1", "0", "1"])
    assert [x["chunk_id"] for x in p] == ["1", "0"]


def test_no_heading_gives_none_section_title():
    mm = {"chunks": [{"key": "5", "chunk_keys": ["5"], "heading_path": ""}]}
    p = build_pointers(mm, ["5"])
    assert p[0]["section_title"] is None
    assert p[0]["heading_path"] == []


def test_empty_inputs_return_empty():
    assert build_pointers({"chunks": []}, ["0"]) == []
    assert build_pointers(_mm(), []) == []


def test_attach_pointers_sets_per_section():
    secs = [{"id": "s1", "title": "A", "chunk_refs": ["0"]},
            {"id": "s2", "title": "B", "chunk_refs": ["999"]}]
    attach_pointers(secs, _mm())
    assert secs[0]["pointers"][0]["chunk_id"] == "0"
    assert secs[1]["pointers"] == []   # id không khớp nguồn → rỗng, không bịa
