# BE/tests/test_summary_dedup.py — dedup/polish thuần (0 LLM, deterministic)
from services.summary.pipeline import dedup as dd


def test_normalize_text_lowercases_strips_collapses():
    assert dd.normalize_text("  Đệ   Quy .  ") == "đệ quy"
    assert dd.normalize_text("A, B; C!") == "a b c"
    assert dd.normalize_text(None) == ""


def test_normalize_keeps_diacritics_distinct():
    # KHÔNG bỏ dấu → "bàn" ≠ "bán" (không gộp nhầm homograph)
    assert dd.normalize_text("bàn") != dd.normalize_text("bán")


def test_dedupe_strings_removes_exact_normalized_dupes():
    # biến thể chỉ khác hoa-thường (cùng độ dài) → gộp 1, giữ bản gặp ĐẦU
    out = dd.dedupe_strings(["Đệ quy", "đệ quy", "ĐỆ QUY", "Ngăn xếp"])
    assert out == ["Đệ quy", "Ngăn xếp"]


def test_dedupe_strings_keeps_most_complete_variant():
    # CÙNG bản chuẩn hoá (chỉ khác dấu câu/hoa-thường) → giữ bản DÀI hơn, vị trí lần đầu.
    # (Biến thể thêm TỪ mới sẽ chuẩn hoá KHÁC nhau → giữ cả hai — xem test conservative.)
    out = dd.dedupe_strings(["Đệ quy", "Đệ quy!!!"])
    assert out == ["Đệ quy!!!"]


def test_dedupe_strings_extra_words_are_kept_not_merged():
    # "đệ quy" vs "đệ quy hàm gọi chính nó" chuẩn hoá KHÁC → cả hai giữ (không mất fact)
    out = dd.dedupe_strings(["Đệ quy", "Đệ quy: hàm gọi chính nó"])
    assert out == ["Đệ quy", "Đệ quy: hàm gọi chính nó"]


def test_dedupe_strings_preserves_order_for_non_dupes():
    assert dd.dedupe_strings(["c", "a", "b"]) == ["c", "a", "b"]


def test_conservative_does_not_merge_unrelated_similar():
    # na ná nhưng KHÁC nghĩa → cả hai GIỮ (chỉ khớp chính xác mới gộp)
    out = dd.dedupe_strings(["Đệ quy tuyến tính", "Đệ quy cây"])
    assert out == ["Đệ quy tuyến tính", "Đệ quy cây"]


def test_dedupe_strings_cap():
    assert dd.dedupe_strings([str(i) for i in range(10)], max_items=3) == ["0", "1", "2"]


def test_dedupe_facts_per_list_and_drops_empty_keys():
    facts = {"key_points": ["A", "a", "B"], "definitions": [], "formulas": ["x", "x"]}
    out = dd.dedupe_facts(facts)
    assert out["key_points"] == ["A", "B"]
    assert out["formulas"] == ["x"]
    assert "definitions" not in out     # rỗng → bỏ key


def test_dedupe_sections_dedupes_keypoints_and_facts_keeps_refs_pointers():
    secs = [{
        "id": "s1", "title": "M", "summary": "giữ nguyên",
        "key_points": ["ý chính", "ý chính", "khác"],
        "chunk_refs": ["0", "1"],
        "pointers": [{"chunk_id": "0"}, {"chunk_id": "1"}],
        "facts": {"definitions": ["D", "d"]},
    }]
    out = dd.dedupe_sections(secs)
    assert out[0]["key_points"] == ["ý chính", "khác"]
    assert out[0]["facts"]["definitions"] == ["D"]
    assert out[0]["summary"] == "giữ nguyên"          # text KHÔNG bị viết lại
    assert out[0]["chunk_refs"] == ["0", "1"]         # refs không đụng
    assert out[0]["pointers"] == [{"chunk_id": "0"}, {"chunk_id": "1"}]  # pointers giữ


def test_dedupe_study_lists_selfcheck_and_review():
    study = {
        "key_concepts": ["A", "a", "B"],
        "definitions": ["D", "D"],
        "formulas": [], "examples": [], "common_mistakes": [],
        "self_check": [{"q": "Câu 1?", "a_hint": "h"}, {"q": "câu 1 ?", "a_hint": ""},
                       {"q": "Câu 2?", "a_hint": ""}],
        "recommended_review": [
            {"title": "Ôn: M", "section_title": "M", "chunk_id": "0", "page": 2, "reason": "R"},
            {"title": "Ôn: M", "section_title": "M", "chunk_id": "0", "page": 2, "reason": "R"},
            {"title": "Ôn: N", "section_title": "N", "chunk_id": "5", "page": None, "reason": "R"},
        ],
    }
    out = dd.dedupe_study(study)
    assert out["key_concepts"] == ["A", "B"]
    assert out["definitions"] == ["D"]
    assert [q["q"] for q in out["self_check"]] == ["Câu 1?", "Câu 2?"]   # dedupe theo câu
    assert len(out["recommended_review"]) == 2                          # dedupe theo key
    assert out["recommended_review"][0]["page"] == 2                    # pointer field giữ


def test_dedupe_record_end_to_end_and_null_safe():
    rec = {
        "sections": [{"id": "s1", "title": "M", "key_points": ["a", "a"]}],
        "study": {"key_concepts": ["X", "x"]},
        "overview": "tổng quan giữ nguyên",
    }
    out = dd.dedupe_record(rec)
    assert out["sections"][0]["key_points"] == ["a"]
    assert out["study"]["key_concepts"] == ["X"]
    assert out["overview"] == "tổng quan giữ nguyên"
    # record không có study / rỗng → an toàn
    assert dd.dedupe_record({"sections": []}) == {"sections": []}
    assert dd.dedupe_record(None) is None
