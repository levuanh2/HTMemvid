"""D5: lọc retrieval theo category/language từ metadata index.json."""
import json

from app.domains.retrieval.hybrid import HybridRetriever, _meta_match


def _write_meta(tmp_path):
    meta = {
        "0": {"text": "Nội dung y tế tiếng Việt.", "video": "doc1.mp4", "category": "yte", "language": "vi"},
        "1": {"text": "English finance content.", "video": "doc2.mp4", "category": "finance", "language": "en"},
        "__meta__": {"version": "1.1", "num_chunks": 2},
    }
    p = tmp_path / "index.json"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


def test_loads_category_language(tmp_path):
    meta_path = _write_meta(tmp_path)
    r = HybridRetriever(index_path=tmp_path / "index.faiss", meta_path=meta_path)
    r._ensure_loaded()
    by_id = {c.chunk_id: c for c in r._chunks}
    assert by_id[0].category == "yte" and by_id[0].language == "vi"
    assert by_id[1].category == "finance" and by_id[1].language == "en"


def test_meta_match_and_filter(tmp_path):
    meta_path = _write_meta(tmp_path)
    r = HybridRetriever(index_path=tmp_path / "index.faiss", meta_path=meta_path)
    r._ensure_loaded()
    allowed = r._filter_by_sources(None)
    yte = [i for i in allowed if _meta_match(r._chunks[i], "yte", None)]
    assert [r._chunks[i].chunk_id for i in yte] == [0]
    en = [i for i in allowed if _meta_match(r._chunks[i], None, "en")]
    assert [r._chunks[i].chunk_id for i in en] == [1]
    # không filter -> giữ tất cả
    assert [i for i in allowed if _meta_match(r._chunks[i], None, None)] == allowed


# ── Khớp source theo canonical stem (regression bug query-theo-file) ──────────

def _write_source_meta(tmp_path, entries):
    meta = {str(i): e for i, e in enumerate(entries)}
    meta["__meta__"] = {"version": "1.1", "num_chunks": len(entries)}
    p = tmp_path / "index.json"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


def _retriever(tmp_path, entries):
    p = _write_source_meta(tmp_path, entries)
    r = HybridRetriever(index_path=tmp_path / "index.faiss", meta_path=p)
    r._ensure_loaded()
    return r


def test_filter_space_filename_matches_sanitized_video(tmp_path):
    # BUG CHÍNH: chunk có video_path đã sanitize (space→'_'), người dùng chọn bằng
    # stem giữ khoảng trắng (dạng /upload-file trả về cũ) → PHẢI khớp.
    r = _retriever(tmp_path, [
        {"text": "abc", "video": "videos/My_Report_pdf_20260628_120000.mp4"},
    ])
    assert r._filter_by_sources(["my report_pdf"]) == [0]   # trước fix: []
    assert r._filter_by_sources(["My Report.pdf"]) == [0]
    assert r._filter_by_sources(["My_Report_pdf"]) == [0]


def test_filter_prefers_canonical_source_stem_field(tmp_path):
    # Chunk mới ghi sẵn source_stem canonical → chọn bằng tên gốc vẫn khớp.
    r = _retriever(tmp_path, [
        {"text": "abc", "video": "videos/x_20260628_120000.mp4", "source_stem": "my_report_pdf"},
    ])
    assert r._filter_by_sources(["My Report.pdf"]) == [0]


def test_filter_vietnamese_diacritics(tmp_path):
    r = _retriever(tmp_path, [
        {"text": "abc", "video": "videos/Báo_cáo_pdf_20260628_120000.mp4"},
    ])
    assert r._filter_by_sources(["Báo cáo.pdf"]) == [0]


def test_filter_wrong_name_excludes(tmp_path):
    r = _retriever(tmp_path, [
        {"text": "abc", "video": "videos/My_Report_pdf_20260628_120000.mp4"},
    ])
    assert r._filter_by_sources(["khac.pdf"]) == []


def test_filter_multi_source_selects_one(tmp_path):
    r = _retriever(tmp_path, [
        {"text": "alpha noi dung", "video": "videos/My_Report_pdf_20260628_120000.mp4"},
        {"text": "beta noi dung", "video": "videos/Other_Doc_pdf_20260628_120000.mp4"},
    ])
    assert r._filter_by_sources(["My Report.pdf"]) == [0]
    assert r._filter_by_sources(["Other Doc.pdf"]) == [1]
    assert sorted(r._filter_by_sources(["My Report.pdf", "Other Doc.pdf"])) == [0, 1]
    # không chọn gì → tất cả
    assert r._filter_by_sources(None) == [0, 1]
