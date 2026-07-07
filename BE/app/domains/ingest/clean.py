from __future__ import annotations

import re
import unicodedata
from collections import Counter

_PAGE_NUMBER_RE = re.compile(r"^\s*\d+\s*$")
_MULTISPACE_RE = re.compile(r"\s+")
_MULTIBLANK_RE = re.compile(r"\n{3,}")
# Ảnh nhúng base64 data-URI (mammoth/markdownify hay sinh ra) — nhiễu thuần, không
# embed được, làm phình markdown hàng MB. Bỏ hẳn.
_DATA_URI_IMG_RE = re.compile(r"!\[[^\]]*\]\(data:[^)]*\)")
# Anchor TOC của Word (mammoth sinh <a id="_Toc..."></a>) — bỏ tag, giữ nội dung.
_HTML_ANCHOR_RE = re.compile(r"</?a\b[^>]*>")


def _normalize_for_count(line: str) -> str:
    return _MULTISPACE_RE.sub(" ", line).strip()


def clean_markdown(md: str, *, source: str | None = None) -> str:
    _ = source
    text = unicodedata.normalize("NFC", md or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _DATA_URI_IMG_RE.sub("", text)
    text = _HTML_ANCHOR_RE.sub("", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    lines = [line.rstrip() for line in text.split("\n")]
    normalized_lines = [_normalize_for_count(line) for line in lines]
    repeated = {
        normalized
        for normalized, count in Counter(normalized_lines).items()
        if normalized and count >= 3
    }

    kept_lines: list[str] = []
    for line, normalized in zip(lines, normalized_lines):
        if _PAGE_NUMBER_RE.match(line):
            continue
        if normalized in repeated:
            continue
        kept_lines.append(line)

    cleaned = "\n".join(kept_lines)
    cleaned = _MULTIBLANK_RE.sub("\n\n", cleaned).strip()
    # Unescape TRƯỚC promote: mammoth escape "1\." làm pattern đánh số trượt,
    # và text sạch hơn cho LLM/BM25/hiển thị bằng chứng.
    return promote_headings(unescape_mammoth(cleaned))


# mammoth backslash-escape punctuation thường (\. \( \)…) — bỏ escape cho bộ AN TOÀN.
# KHÔNG đụng ký tự cấu trúc markdown (# * - _ [ ] ` > + =) để không tạo heading/list/
# emphasis/link giả từ text vốn được escape có chủ đích.
_SAFE_UNESCAPE_RE = re.compile(r"\\([.()!?,:;…\"'])")


def unescape_mammoth(text: str) -> str:
    return _SAFE_UNESCAPE_RE.sub(r"\1", text or "")


# --- Heading promotion cho tài liệu không dùng Word Heading styles -----------
# mammoth chỉ sinh #/##/### từ style Heading 1-3; docx sinh viên thường dùng
# bold/đánh số tay → markdown không có heading nào → mindmap skeleton mất cấu trúc.
# Chỉ chạy khi doc CHƯA có heading nào (tránh phá cấu trúc pdf/pymupdf4llm đã tốt).
_HAS_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
# mammoth sinh __bold__, markdown khác sinh **bold** — nhận CẢ HAI (group 2 = text)
_BOLD_LINE_RE = re.compile(r"^(\*\*|__)(.+?)\1[:：]?$")
# (pattern, depth): Chương/Phần/Bài/Mục + số La Mã → #, "1." → ##, "1.1" → ###
_NUMBERED_PATTERNS = [
    (re.compile(r"^(Chương|Phần|Bài|Mục)\s+\S+", re.IGNORECASE), 1),
    (re.compile(r"^[IVX]+\.\s+\S"), 1),
    (re.compile(r"^\d+\.\d+\.?\s+\S"), 3),
    # \S chứ không \D: "1. 2024 Kết quả" vẫn là heading; guard standalone-line
    # + không-dấu-chấm-cuối đã chặn item list (codex #4)
    (re.compile(r"^\d+\.\s+\S"), 2),
]
# Bold đứng một mình = tác giả CHỦ ĐÍCH format tiêu đề → cap dài (đo thật: câu hỏi
# Q&A tiếng Việt tới 203 ký tự trong doc mẫu). Dòng đánh số trần = tín hiệu yếu → cap chặt.
_MAX_HEADING_CHARS_BOLD = 250
_MAX_HEADING_CHARS = 90


def _promote_line(line: str) -> str | None:
    """Trả line đã promote thành heading, hoặc None nếu không phải heading."""
    stripped = line.strip()
    if not stripped:
        return None
    m = _BOLD_LINE_RE.match(stripped)
    if len(stripped) > (_MAX_HEADING_CHARS_BOLD if m else _MAX_HEADING_CHARS):
        return None
    text = m.group(2).strip() if m else stripped
    if text.endswith("."):
        return None  # câu văn thường, không phải tiêu đề
    for pat, depth in _NUMBERED_PATTERNS:
        if pat.match(text):
            return f"{'#' * depth} {text}"
    if m:  # cả dòng bold ngắn không kết thúc bằng dấu chấm → heading cấp 2
        return f"## {text}"
    return None


def promote_headings(md: str) -> str:
    if not md or _HAS_HEADING_RE.search(md):
        return md  # đã có heading thật (Word styles / pdf) — không đụng
    lines = md.split("\n")
    out = []
    for i, line in enumerate(lines):
        # Chỉ promote dòng đứng MỘT MÌNH (blank 2 phía) — item trong ordered list
        # ("1. mua trứng") nằm sát các dòng khác sẽ không bị promote nhầm.
        standalone = (i == 0 or not lines[i - 1].strip()) and \
                     (i == len(lines) - 1 or not lines[i + 1].strip())
        promoted = _promote_line(line) if standalone else None
        out.append(promoted if promoted is not None else line)
    return "\n".join(out)
