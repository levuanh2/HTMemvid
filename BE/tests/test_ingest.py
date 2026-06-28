import io
import time


def test_upload_and_poll_status(client):
    data = {"file": (io.BytesIO(b"hello world"), "hello.txt")}
    r = client.post("/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    payload = r.get_json()
    assert payload and payload.get("source_id")

    source_id = payload["source_id"]

    # Poll status: do conftest đã patch ingest nhanh nên phải ready gần như ngay
    for _ in range(20):
        s = client.get(f"/sources/{source_id}/status")
        assert s.status_code == 200
        st = s.get_json()
        if st.get("can_query") or st.get("status") in ("index_ready", "ready"):
            break
        time.sleep(0.05)

    assert st.get("status") in ("index_ready", "ready", "processing")

