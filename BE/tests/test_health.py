def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, dict)
    assert data.get("status") == "ok"


def test_stats(client):
    r = client.get("/stats")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data, dict)

