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
    return cleaned
