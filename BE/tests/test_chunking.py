"""Test cắt chunk theo cấu trúc (D2)."""
from app.domains.ingest.chunking import chunk_markdown, chunk_markdown_spans


import re


def _markers(text: str) -> set:
    return set(re.findall(r"S(\d+)", text))


def test_heading_path_and_overlap():
    # câu duy nhất, có marker S<n> để phát hiện overlap đáng tin cậy
    long_para = " ".join(f"S{i} đây là câu nội dung số {i} của phần hai." for i in range(200))
    md = (
        "# Tài liệu\n\n"
        "## Phần 1\n\n"
        "Nội dung ngắn của phần một.\n\n"
        "## Phần 2\n\n"
        f"{long_para}\n"
    )
    chunks = chunk_markdown(md, chunk_size=400, chunk_overlap=120)
    assert chunks, "phải có chunk"
    for c in chunks:
        assert "heading_path" in c and "text" in c
        assert "Tài liệu" in c["heading_path"]  # h1 luôn có mặt
    p2 = [c for c in chunks if c["heading_path"].endswith("Phần 2")]
    assert len(p2) > 1, "section dài phải bị cắt thành nhiều chunk"
    # overlap: tồn tại 1 cặp chunk liên tiếp chia sẻ marker câu
    assert any(
        _markers(p2[i]["text"]) & _markers(p2[i + 1]["text"]) for i in range(len(p2) - 1)
    ), "phải có overlap giữa các chunk liên tiếp"


def test_no_heading():
    chunks = chunk_markdown("Văn bản thuần không có tiêu đề nào cả.")
    assert chunks
    assert all(c["heading_path"] == "" for c in chunks)


def test_chunk_markdown_spans_returns_doc_text_and_aligned_spans():
    """Late chunking cần MỘT hệ toạ độ char nhất quán: chunk_markdown_spans trả
    (doc_text, pieces) sao cho doc_text[start:end] == text của mỗi piece — để
    map char-span sang token-span khi mean-pool. (Không dùng md gốc vì
    MarkdownHeaderTextSplitter viết lại khoảng trắng.)"""
    long_para = " ".join(f"S{i} câu nội dung số {i} của phần hai." for i in range(120))
    md = (
        "# Tài liệu\n\n"
        "## Phần 1\n\n"
        "Nội dung ngắn của phần một.\n\n"
        "## Phần 2\n\n"
        f"{long_para}\n"
    )
    doc_text, chunks = chunk_markdown_spans(md, chunk_size=300, chunk_overlap=60)
    assert isinstance(doc_text, str) and doc_text
    assert chunks
    for c in chunks:
        assert "start" in c and "end" in c and "text" in c and "heading_path" in c
        if c["start"] >= 0:
            assert doc_text[c["start"] : c["end"]] == c["text"], "span phải trỏ đúng substring"
    located = [c for c in chunks if c["start"] >= 0]
    # đại đa số piece phải định vị được (cho phép số ít fallback -1)
    assert len(located) >= max(1, int(0.8 * len(chunks)))


def test_chunk_markdown_still_returns_list_with_spans():
    """chunk_markdown (signature cũ) vẫn trả list[dict], thêm start/end (backward-compat)."""
    chunks = chunk_markdown("# H\n\nNội dung phần một dài vừa đủ để có một chunk.")
    assert chunks and isinstance(chunks, list)
    assert all({"text", "heading_path", "start", "end"} <= set(c) for c in chunks)
