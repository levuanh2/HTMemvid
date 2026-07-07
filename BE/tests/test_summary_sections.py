# BE/tests/test_summary_sections.py — build_sections trên skeleton thật (0 LLM)
from services.summary.pipeline.sections import build_sections


def _mm(chunks):
    return {"title": "Doc", "sources": ["a_docx"], "chunks": chunks, "tree_sections": []}


def test_headings_produce_sections_with_descendant_refs():
    mm = _mm([
        {"key": "0", "text": "t0", "heading_path": "1. Mở đầu", "chunk_keys": ["0"]},
        {"key": "1", "text": "t1", "heading_path": "1. Mở đầu > 1.1 Bối cảnh", "chunk_keys": ["1"]},
        {"key": "2", "text": "t2", "heading_path": "2. Phương pháp", "chunk_keys": ["2"]},
    ])
    sections, method = build_sections(mm)
    assert method == "headings"
    assert [s["title"] for s in sections] == ["1. Mở đầu", "2. Phương pháp"]
    # section 1 phải gom cả chunk của heading con (descendant refs)
    assert set(sections[0]["chunk_refs"]) == {"0", "1"}
    assert sections[1]["chunk_refs"] == ["2"]


def test_no_structure_falls_back_single_section_with_all_refs():
    mm = _mm([
        {"key": "0", "text": "t0", "heading_path": "", "chunk_keys": ["0"]},
        {"key": "1", "text": "t1", "heading_path": "", "chunk_keys": ["1"]},
    ])
    # heading_path rỗng → _from_headings vẫn tạo "Nội dung khác"; ép single bằng
    # outline_fn=None và chunks không heading + <4 chunk (cluster bó tay)
    sections, method = build_sections({**mm, "chunks": []}, outline_fn=None)
    assert method == "single"
    assert len(sections) == 1
    assert sections[0]["chunk_refs"] == []


def test_outline_fn_used_when_skeleton_single():
    mm = _mm([])  # không chunk → skeleton "single"

    def outline_fn(mi):
        return [
            {"id": "n0", "parent": None, "kind": "root", "title": "Doc", "note": "", "chunk_refs": [], "order": 0},
            {"id": "n1", "parent": "n0", "kind": "section", "title": "A", "note": "", "chunk_refs": ["0"], "order": 0},
            {"id": "n2", "parent": "n0", "kind": "section", "title": "B", "note": "", "chunk_refs": ["1"], "order": 1},
        ]

    sections, method = build_sections(mm, outline_fn=outline_fn)
    assert method == "llm_outline"
    assert [s["title"] for s in sections] == ["A", "B"]


def test_outline_fn_exception_keeps_single():
    def outline_fn(mi):
        raise RuntimeError("llm down")

    sections, method = build_sections(_mm([]), outline_fn=outline_fn)
    assert method == "single"
    assert len(sections) == 1
