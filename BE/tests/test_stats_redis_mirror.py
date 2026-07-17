"""Phase 0 observability — Redis-mirrored counters + /stats aggregate.

Contract:
- MirroredCounter hoạt động y hệt Counter khi Redis vắng/hỏng (fail-open tuyệt đối).
- Mỗi increment dương mirror `INCRBY metrics:<section>:<key> <delta>` khi Redis có.
- metric_totals(...) đọc tổng cross-worker từ Redis; Redis vắng → None (caller
  rơi về counter local — /stats không bao giờ vỡ vì Redis).
"""

from __future__ import annotations

from app.clients import redis_client as rc


class _FakeRedis:
    def __init__(self):
        self.data = {}

    def incrby(self, key, amount=1):
        self.data[key] = int(self.data.get(key, 0)) + int(amount)
        return self.data[key]

    def mget(self, keys):
        return [self.data.get(k) for k in keys]


class _BoomRedis:
    def incrby(self, *a, **kw):
        raise RuntimeError("redis down")

    def mget(self, *a, **kw):
        raise RuntimeError("redis down")


def test_mirrored_counter_local_behavior_without_redis(monkeypatch):
    monkeypatch.setattr(rc, "get_redis", lambda: None)
    c = rc.MirroredCounter("sf", {"leader": 0, "follower": 0})
    c["leader"] += 1
    c["leader"] += 1
    c["new_key"] += 1  # Counter semantics: key lạ default 0
    assert c["leader"] == 2
    assert c["new_key"] == 1
    assert dict(c)["follower"] == 0


def test_mirrored_counter_mirrors_delta_to_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(rc, "get_redis", lambda: fake)
    c = rc.MirroredCounter("sf", {"leader": 0})
    c["leader"] += 1
    c["leader"] += 3  # += 3 → delta 3
    assert fake.data["metrics:sf:leader"] == 4


def test_mirrored_counter_swallows_redis_errors(monkeypatch):
    monkeypatch.setattr(rc, "get_redis", lambda: _BoomRedis())
    c = rc.MirroredCounter("sf", {"leader": 0})
    c["leader"] += 1  # không raise
    assert c["leader"] == 1  # local vẫn đúng


def test_metric_totals_reads_aggregate(monkeypatch):
    fake = _FakeRedis()
    fake.data = {"metrics:sf:leader": 7, "metrics:llm:calls": 12}
    monkeypatch.setattr(rc, "get_redis", lambda: fake)
    out = rc.metric_totals({"sf": ["leader", "follower"], "llm": ["calls"]})
    assert out == {"sf": {"leader": 7, "follower": 0}, "llm": {"calls": 12}}


def test_metric_totals_none_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(rc, "get_redis", lambda: None)
    assert rc.metric_totals({"sf": ["leader"]}) is None
    monkeypatch.setattr(rc, "get_redis", lambda: _BoomRedis())
    assert rc.metric_totals({"sf": ["leader"]}) is None


def test_stats_route_works_without_redis(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "_auth_protect_enabled", lambda: False)
    monkeypatch.setattr(rc, "get_redis", lambda: None)
    r = client.get("/stats")
    assert r.status_code == 200
    data = r.get_json()
    assert "llm" in data  # counter LLM mới (local, per-worker)
    assert data.get("aggregate") is None  # Redis vắng → không có aggregate, không lỗi
