"""Chốt 'một nguồn sự thật': mọi quy tắc chuẩn hoá stem đều quy về canonical.

Bảo vệ chống tái phát bug query-theo-file: nếu ai đó đổi _normalize_video_stem
(memory/tree) hoặc hybrid._norm_stem lệch khỏi canonical_source_stem → fail.
"""

from __future__ import annotations

import os

os.environ.setdefault("SKIP_MODEL_LOAD", "1")

from shared.source_id import canonical_source_stem
from app.domains.memory.tree import _normalize_video_stem
from app.domains.retrieval.hybrid import _norm_stem


CASES = [
    "My Report.pdf",
    "Báo cáo tài chính.pdf",
    "tài liệu (1) - test.txt",
    "report.pdf",
    "clip.mp4",
]


def _video_path(filename: str) -> str:
    """Mô phỏng video_path ingest tạo: sanitize(filename.replace('.','_')) + ts.mp4."""
    video_name = filename.replace(".", "_")
    safe = "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in video_name)
    return f"videos/{safe}_20260628_120000.mp4"


def test_all_normalizers_agree_with_canonical():
    for fn in CASES:
        c = canonical_source_stem(fn)
        assert _normalize_video_stem(fn) == c, fn          # memory/tree
        assert _norm_stem(fn) == c, fn                     # retrieval/hybrid
        # filename ↔ video_path mà ingest tạo → cùng stem (mấu chốt khớp)
        assert canonical_source_stem(_video_path(fn)) == c, fn
        assert _normalize_video_stem(_video_path(fn)) == c, fn


def test_selected_source_matches_indexed_video_path():
    # Người dùng chọn bằng tên gốc; chunk index lưu video_path đã sanitize → khớp.
    fn = "My Report.pdf"
    assert _norm_stem(fn) == _norm_stem(_video_path(fn)) == "my_report_pdf"
