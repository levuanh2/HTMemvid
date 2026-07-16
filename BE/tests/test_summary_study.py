# BE/tests/test_summary_study.py — build_study thuần (0 LLM, deterministic)
from services.summary.pipeline.study import build_study


def _section(sid, title, facts=None, pointers=None, key_points=None):
    s = {"id": sid, "title": title, "chunk_refs": ["0"]}
    if facts is not None:
        s["facts"] = facts
    if pointers is not None:
        s["pointers"] = pointers
    if key_points is not None:
        s["key_points"] = key_points
    return s


def _rich_sections():
    return [
        _section("s1", "1. Mở đầu", facts={
            "important_terms": ["Đệ quy", "Ngăn xếp"],
            "definitions": ["Đệ quy: hàm gọi chính nó"],
            "formulas": ["T(n)=T(n-1)+1"],
            "examples": ["giai thừa"],
            "common_mistakes": ["quên điều kiện dừng"],
            "open_questions": ["Khi nào nên dùng đệ quy?"],
        }, pointers=[{"chunk_id": "0", "source_id": "doc-a", "source_stem": "doc_a",
                      "page": 2, "section_title": "1. Mở đầu"}]),
        _section("s2", "2. Nâng cao", facts={
            "important_terms": ["Đệ quy"],   # trùng s1 → dedupe
            "formulas": ["O(2^n)"],
        }, pointers=[{"chunk_id": "5", "source_id": None, "source_stem": "doc_a",
                      "page": None, "section_title": "2. Nâng cao"}]),
    ]


def test_aggregates_facts_across_sections_deduped():
    st = build_study(_rich_sections())
    assert st["key_concepts"] == ["Đệ quy", "Ngăn xếp"]     # dedupe "Đệ quy"
    assert st["formulas"] == ["T(n)=T(n-1)+1", "O(2^n)"]
    assert st["definitions"] == ["Đệ quy: hàm gọi chính nó"]
    assert st["common_mistakes"] == ["quên điều kiện dừng"]


def test_self_check_uses_open_questions_then_terms():
    st = build_study(_rich_sections())
    qs = st["self_check"]
    assert qs[0] == {"q": "Khi nào nên dùng đệ quy?", "a_hint": ""}   # open_question dùng thẳng
    # important_term → "Giải thích khái niệm: X", hint = định nghĩa khớp
    term_q = next(q for q in qs if q["q"] == "Giải thích khái niệm: Đệ quy")
    assert term_q["a_hint"] == "Đệ quy: hàm gọi chính nó"


def test_self_check_never_invents_beyond_facts():
    # facts vắng → self_check fallback key_points, KHÔNG bịa câu ngoài
    secs = [_section("s1", "M", key_points=["ý chính A"])]
    st = build_study(secs)
    assert st["self_check"] == [{"q": "Trình bày: ý chính A", "a_hint": "ý chính A"}]
    assert st["definitions"] == [] and st["formulas"] == []   # facts vắng → rỗng, không bịa


def test_recommended_review_from_real_pointers_only():
    st = build_study(_rich_sections())
    rev = st["recommended_review"]
    assert len(rev) == 2
    assert rev[0]["chunk_id"] == "0" and rev[0]["page"] == 2
    assert rev[0]["title"] == "Ôn lại: 1. Mở đầu"
    assert "công thức" in rev[0]["reason"] and "định nghĩa" in rev[0]["reason"]
    assert rev[1]["chunk_id"] == "5" and rev[1]["page"] is None


def test_recommended_review_empty_when_no_pointers():
    secs = [_section("s1", "M", facts={"formulas": ["x"]})]   # no pointers key
    st = build_study(secs)
    assert st["recommended_review"] == []


def test_degrades_safely_with_empty_sections():
    st = build_study([])
    assert st == {"key_concepts": [], "definitions": [], "formulas": [], "examples": [],
                  "common_mistakes": [], "self_check": [], "recommended_review": []}


def test_key_concepts_fallback_to_key_points_when_no_terms():
    secs = [_section("s1", "M", key_points=["Khái niệm X", "Khái niệm Y"])]
    st = build_study(secs)
    assert st["key_concepts"] == ["Khái niệm X", "Khái niệm Y"]
