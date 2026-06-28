"""
Định danh source DUY NHẤT (canonical) — nguồn sự thật cho việc khớp file giữa
upload → ingest → index metadata → registry → query-selection → delete.

Vì sao cần: trước đây "stem" được suy ra ở nhiều nơi với quy tắc khác nhau
(upload giữ khoảng trắng; video_path sanitize khoảng trắng → '_'; retrieval dùng
NFKD...). Tên file có khoảng trắng/ký tự đặc biệt/dấu tiếng Việt làm hai phía lệch
nhau → chọn file để hỏi bị "Không tìm thấy dữ liệu phù hợp". Module này gom về MỘT
quy tắc, MIRROR đúng cách `video_name`/`video_path` được tạo nên hai phía luôn trùng.

Quy tắc `canonical_source_stem` (khớp chính xác cách ingest đặt tên):
  - `video_name = filename.replace('.', '_')` (ingest_graph) → dấu '.' của đuôi tài
    liệu bị FOLD thành '_' (nên "report.pdf" ↔ chunk "report_pdf").
  - `video_path` = sanitize(video_name) + "_<YYYYMMDD_HHMMSS>.mp4" (video_utils):
    ký tự không [alnum/_/-] → '_'.
  Do đó canonicalizer:
    1. basename (bỏ path);
    2. NFC normalize (ổn định, miễn nhiễm lệch NFC/NFKD; GIỮ dấu tiếng Việt);
    3. bỏ '.mp4' CHỈ khi là container do ta tạo (có hậu tố timestamp) — file tài
       liệu tên '*.mp4' thì để nguyên cho bước sanitize fold;
    4. sanitize char-by-char giống video_utils (non [alnum/_/-] → '_'); bước này tự
       fold mọi '.' còn lại (đuôi tài liệu) và khoảng trắng (kể cả no-break) → '_';
    5. bỏ hậu tố timestamp '_YYYYMMDD_HHMMSS';
    6. lower + strip('_').

Ví dụ (đều ra "my_report_pdf"):
  "My Report.pdf", "my report_pdf", "videos/My_Report_pdf_20250228_143022.mp4"
"""

from __future__ import annotations

import os
import re
import unicodedata

# Hậu tố timestamp ingest gắn vào video (giống hybrid._STEM_TS_SUFFIX, tree.py).
_TS_SUFFIX = re.compile(r"_\d{8}_\d{6}$")
# ".mp4" CHỈ là container do ta tạo khi đứng ngay sau timestamp.
_GENERATED_MP4 = re.compile(r"_\d{8}_\d{6}\.mp4$", re.IGNORECASE)


def _sanitize_char(c: str) -> str:
    # Mirror video_utils.save_qr_frames_to_video: giữ alnum (kể cả ký tự có dấu
    # unicode vì str.isalnum() True), '_' và '-'; còn lại (gồm '.', space,  ) → '_'.
    return c if (c.isalnum() or c in ("_", "-")) else "_"


def canonical_source_stem(name: str) -> str:
    """Chuẩn hoá tên file / video_path / stem về MỘT định danh khớp duy nhất."""
    s = (name or "").strip()
    if not s:
        return ""
    # 1) basename
    if "/" in s or "\\" in s:
        s = os.path.basename(s)
    # 2) NFC (ổn định, giữ dấu)
    s = unicodedata.normalize("NFC", s)
    # 3) chỉ bỏ '.mp4' khi là container ta tạo (có timestamp) — giữ '.mp4' của
    #    file tài liệu tên '*.mp4' để bước sanitize fold thành '_mp4' (khớp chunk).
    if _GENERATED_MP4.search(s):
        s = s[:-4]  # bỏ đúng ".mp4"
    # 4) sanitize (fold mọi '.' còn lại + khoảng trắng/ký tự lạ → '_')
    s = "".join(_sanitize_char(c) for c in s)
    # 5) bỏ hậu tố timestamp
    s = _TS_SUFFIX.sub("", s)
    # 6) chuẩn hoá cuối
    return s.strip("_").lower()


def display_filename(filename: str) -> str:
    """Tên hiển thị cho UI (NFC, bỏ no-break space). KHÔNG dùng để so khớp."""
    return unicodedata.normalize("NFC", (filename or "").strip()).replace(" ", " ")
