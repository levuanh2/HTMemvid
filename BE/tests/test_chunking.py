"""Test cắt chunk theo cấu trúc (D2)."""
from app.domains.ingest.chunking import chunk_markdown


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
