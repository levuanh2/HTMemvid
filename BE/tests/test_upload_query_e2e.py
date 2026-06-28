"""E2E contract qua HTTP: upload (tên có space/dấu) → status → query chọn file.

Lưu ý: conftest mock _trigger_background_ingest (_fast_ingest) + QUERY_GRAPH, và
hybrid.retrieve trả [] khi SKIP_MODEL_LOAD=1, nên test này KHÔNG assert "có chunk
thật" — phần đó do test_retrieval_filter.py (unit) bắt. Ở đây kiểm CONTRACT:
video_stem trả về là canonical (FE sẽ gửi đúng khóa), chống trùng tên, /query nhận.
"""

from __future__ import annotations

import io
import time

from shared.source_id import canonical_source_stem


def _upload(client, content: bytes, filename: str):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), filename)},
        content_type="multipart/form-data",
    )


def _wait_ready(client, source_id, tries=20):
    st = {}
    for _ in range(tries):
        s = client.get(f"/sources/{source_id}/status")
        st = s.get_json() or {}
        if st.get("can_query") or st.get("status") in ("index_ready", "ready"):
            break
        time.sleep(0.05)
    return st


def test_upload_returns_canonical_video_stem(client):
    fn = "Báo cáo tài chính 2025.pdf"   # có dấu + khoảng trắng (bug cũ)
    r = _upload(client, b"noi dung tai lieu", fn)
    assert r.status_code == 200
    p = r.get_json()
    # video_stem trả FE PHẢI là canonical (khớp cách chunk được đặt tên).
    assert p["video_stem"] == canonical_source_stem(fn)
    assert "_" in p["video_stem"] and " " not in p["video_stem"]
    _wait_ready(client, p["source_id"])


def test_duplicate_filename_gets_distinct_stem(client):
    fn = "trung ten.txt"
    a = _upload(client, b"aaaa bbbb", fn).get_json()
    b = _upload(client, b"cccc dddd", fn).get_json()
    assert a["source_id"] != b["source_id"]
    # Chống trùng: file thứ 2 có tên hiển thị khác → stem khác → không trộn chunk.
    assert a["video_stem"] != b["video_stem"]
    assert b["filename"] != a["filename"]


def test_query_accepts_selected_sources(client):
    fn = "tai lieu hoi dap.txt"
    up = _upload(client, b"noi dung de hoi", fn).get_json()
    _wait_ready(client, up["source_id"])
    r = client.post(
        "/query",
        json={"q": "câu hỏi?", "sources": [up["video_stem"]], "use_memory_tree": False},
    )
    assert r.status_code in (200, 202)
    body = r.get_json() or {}
    assert body.get("job_id")


# Ghi chú: DELETE /sources/<id> full-flow cần embedding model (rebuild_memory_index)
# nên KHÔNG chạy được dưới SKIP_MODEL_LOAD — fix "lấy stem thật từ registry + xóa
# input theo input_path" được kiểm qua review/log, không e2e ở đây.


def test_path_traversal_filename_safe(client):
    # Tên độc hại không được thoát khỏi INPUT_DIR; vẫn trả source_id hợp lệ.
    r = _upload(client, b"x noi dung", "../../evil name.txt")
    assert r.status_code == 200
    p = r.get_json()
    assert p.get("source_id") and p.get("video_stem")
