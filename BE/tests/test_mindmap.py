import time


def test_generate_mindmap_and_poll(client):
    r = client.post("/generate-mindmap", json={"sources": ["demo"], "strategy": "iterative"})
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

