import time


def test_query_job_and_poll(client):
    r = client.post("/query", json={"q": "test", "sources": [], "use_memory_tree": False})
    assert r.status_code == 202
    job_id = r.get_json().get("job_id")
    assert job_id

    result = None
    for _ in range(50):
        s = client.get(f"/query-status/{job_id}")
        assert s.status_code == 200
        data = s.get_json()
        if data.get("status") == "done":
            result = data.get("result")
            break
        if data.get("status") == "error":
            raise AssertionError(f"query job failed: {data.get('error')}")
        time.sleep(0.05)

    assert result and result.get("payload", {}).get("answer")

