"""Unit test cho canonical_source_stem — nguồn sự thật định danh source.

Bắt đúng bug: tên file có khoảng trắng/dấu/ký tự đặc biệt phải cho RA CÙNG stem
với video_path mà ingest tạo ra (sanitize space→'_' + timestamp), để chọn file
truy vấn luôn khớp.
"""

from __future__ import annotations

from shared.source_id import canonical_source_stem


def _ts(prefix: str) -> str:
    """Mô phỏng video_path ingest tạo: sanitize đã làm ở ingest, đây thêm ts+.mp4."""
    return f"videos/{prefix}_20260628_120000.mp4"


def test_space_filename_matches_video_path():
    # Đây là bug chính: tên có khoảng trắng.
    fn = "My Report.pdf"
    assert canonical_source_stem(fn) == "my_report_pdf"
    # video_path ingest tạo từ "My Report.pdf" → video_name "My Report_pdf" →
    # sanitize "My_Report_pdf" → + ts + .mp4
    assert canonical_source_stem(_ts("My_Report_pdf")) == "my_report_pdf"
    # stem registry kiểu cũ (giữ space) cũng quy về cùng giá trị
    assert canonical_source_stem("my report_pdf") == "my_report_pdf"


def test_plain_filename_folds_extension():
    assert canonical_source_stem("report.pdf") == "report_pdf"
    assert canonical_source_stem(_ts("report_pdf")) == "report_pdf"


def test_vietnamese_diacritics_stable():
    fn = "Báo cáo tài chính.pdf"
    stem = canonical_source_stem(fn)
    assert stem == "báo_cáo_tài_chính_pdf"
    # Khớp với video_path tương ứng (sanitize giữ ký tự có dấu, space→'_').
    assert canonical_source_stem(_ts("Báo_cáo_tài_chính_pdf")) == stem
    # NFC vs NFD cùng đầu vào → cùng kết quả (ép NFC).
    import unicodedata
    assert canonical_source_stem(unicodedata.normalize("NFD", fn)) == stem


def test_special_chars_sanitized():
    assert canonical_source_stem("tài liệu (1) - test.txt") == canonical_source_stem(
        _ts("tài_liệu__1__-_test_txt")
    )


def test_path_and_backslash_stripped():
    assert canonical_source_stem("C:\\\\Users\\\\a\\\\My Report.pdf") == "my_report_pdf"
    assert canonical_source_stem("/data/input/My Report.pdf") == "my_report_pdf"


def test_mp4_named_document_not_overstripped():
    # File tài liệu tên thật là 'clip.mp4' (không có timestamp) → KHÔNG bị coi là
    # container; '.mp4' fold thành '_mp4', khớp chunk "clip_mp4".
    assert canonical_source_stem("clip.mp4") == "clip_mp4"
    assert canonical_source_stem(_ts("clip_mp4")) == "clip_mp4"


def test_wrong_name_differs():
    assert canonical_source_stem("My Report.pdf") != canonical_source_stem("Other.pdf")


def test_empty_and_none():
    assert canonical_source_stem("") == ""
    assert canonical_source_stem(None) == ""  # type: ignore[arg-type]


def test_known_limitation_dot_vs_underscore_collide():
    # Giới hạn đã biết (chấp nhận): "a.b.pdf" và "a_b.pdf" trùng stem vì ingest
    # vốn replace('.','_'). Ghi lại để khỏi bất ngờ.
    assert canonical_source_stem("a.b.pdf") == canonical_source_stem("a_b.pdf")
