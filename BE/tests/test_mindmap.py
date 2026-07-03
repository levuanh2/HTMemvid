import time


def test_generate_mindmap_and_poll(client, monkeypatch):
    # collect_mindmap_input thật cần index.json + chunk store thật; route mới gọi nó
    # NGAY trong request (không còn deferred trong worker) — mock để test route/job
    # round-trip mà không cần dữ liệu ingest thật (đúng bài học conftest-mock).
    import app.main as be_main
    monkeypatch.setattr(
        be_main, "_mindmap_input_and_hash",
        lambda sources: ({"chunks": [1], "sources": sources, "title": "demo"}, "f" * 64),
    )
    r = client.post("/generate-mindmap", json={"sources": ["demo"]})
    assert r.status_code == 202
    job_id = r.get_json().get("job_id")
    assert job_id

    job = None
    for _ in range(50):
        s = client.get(f"/mindmap-status/{job_id}")
        assert s.status_code == 200
        job = s.get_json()
        if job.get("status") == "done":
            break
        if job.get("status") == "error":
            raise AssertionError(f"mindmap job failed: {job.get('error')}")
        time.sleep(0.05)

    assert job and job.get("result")

