"""Phase 4 — ingress overload protection: rate limit + /ready + admission shed."""
from __future__ import annotations

import math

import pytest

import app.main as main
from app.main import app
from app.clients import redis_client


class FakeRedis:
    """Implements just enough for the rate-limit Lua token bucket + ping."""
    def __init__(self):
        self.hstore = {}
        self.store = {}

    def ping(self):
        return True

    def eval(self, script, numkeys, *args):
        if "tokens" in script:  # rate-limit token bucket
            key = args[0]
            rate, cap, now, ttl = float(args[1]), float(args[2]), float(args[3]), int(args[4])
            h = self.hstore.setdefault(key, {})
            tokens = float(h.get("tokens", cap))
            ts = float(h.get("ts", now))
            tokens = min(cap, tokens + max(0.0, now - ts) * rate)
            if tokens >= 1:
                tokens -= 1
                allowed, retry = 1, 0
            else:
                allowed, retry = 0, math.ceil((1 - tokens) / rate)
            h["tokens"], h["ts"] = tokens, now
            return [allowed, retry]
        raise AssertionError("unexpected script")


@pytest.fixture
def rl_on(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_RPS", "0.001")  # negligible refill during the test
    monkeypatch.setenv("RATE_LIMIT_BURST", "2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "60")
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    yield fake
    redis_client.reset_for_tests(None)


# --------------------------------------------------------------- rate limit
def test_rate_limit_allows_under_limit_then_rejects(rl_on):
    a1 = main._rate_limit_check("ip:1.2.3.4")
    a2 = main._rate_limit_check("ip:1.2.3.4")
    a3 = main._rate_limit_check("ip:1.2.3.4")
    assert a1 == (True, 0)
    assert a2 == (True, 0)
    assert a3[0] is False and a3[1] > 0  # burst of 2 exhausted -> rejected with retry


def test_rate_limit_disabled_allows(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    assert main._rate_limit_check("ip:x") == (True, 0)


def test_rate_limit_redis_down_fail_open(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUIRE_REDIS", "false")
    redis_client.reset_for_tests(None)  # no client
    try:
        assert main._rate_limit_check("ip:x") == (True, 0)  # fail-open
    finally:
        redis_client.reset_for_tests(None)


def test_rate_limit_require_redis_rejects_when_down(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUIRE_REDIS", "true")
    redis_client.reset_for_tests(None)
    try:
        allowed, retry = main._rate_limit_check("ip:x")
        assert allowed is False and retry > 0
    finally:
        redis_client.reset_for_tests(None)


def test_rate_limited_response_is_structured():
    with app.app_context():
        resp = main._rate_limited_response(10)
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "10"
    body = resp.get_json()
    assert body["error"] == "rate_limited" and body["retry_after_seconds"] == 10 and body["message"]


def test_admission_rejected_response_is_structured():
    with app.app_context():
        resp = main._admission_rejected_response()
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After")
    body = resp.get_json()
    assert body["error"] == "admission_rejected" and body["message"] and body["retry_after_seconds"] >= 0


# --------------------------------------------------------------- health vs ready
def test_health_is_liveness_no_redis():
    redis_client.reset_for_tests(None)  # no redis at all
    client = app.test_client()
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"  # liveness never depends on redis


def test_ready_ok_when_deps_ok(monkeypatch):
    monkeypatch.setattr(main, "QUERY_GRAPH", object())  # graph present
    monkeypatch.setenv("RATE_LIMIT_REQUIRE_REDIS", "false")
    redis_client.reset_for_tests(FakeRedis())  # redis pings ok
    try:
        r = app.test_client().get("/ready")
        assert r.status_code == 200
        assert r.get_json()["status"] == "ready"
    finally:
        redis_client.reset_for_tests(None)


def test_ready_503_when_graph_not_ready(monkeypatch):
    monkeypatch.setattr(main, "QUERY_GRAPH", None)
    r = app.test_client().get("/ready")
    assert r.status_code == 503
    body = r.get_json()
    assert body["status"] == "not_ready" and "graph_not_ready" in body["reason"]


def test_ready_503_when_redis_required_down(monkeypatch):
    monkeypatch.setattr(main, "QUERY_GRAPH", object())
    monkeypatch.setenv("RATE_LIMIT_REQUIRE_REDIS", "true")
    monkeypatch.setenv("REDIS_URL", "redis://nope:6379/0")
    redis_client.reset_for_tests(None)
    try:
        r = app.test_client().get("/ready")
        assert r.status_code == 503
        assert "redis_required_down" in r.get_json()["reason"]
    finally:
        redis_client.reset_for_tests(None)


def test_ready_503_when_admission_saturated(monkeypatch):
    monkeypatch.setattr(main, "QUERY_GRAPH", object())
    redis_client.reset_for_tests(FakeRedis())
    grabbed = 0
    try:
        while main._query_semaphore.acquire(blocking=False):
            grabbed += 1
            if grabbed > 100:
                break
        r = app.test_client().get("/ready")
        assert r.status_code == 503
        assert "admission_saturated" in r.get_json()["reason"]
    finally:
        for _ in range(grabbed):
            main._query_semaphore.release()
        redis_client.reset_for_tests(None)


def test_stats_exposes_overload_block():
    r = app.test_client().get("/stats")
    assert r.status_code == 200
    ov = r.get_json()["overload"]
    for k in ("rate_limit_allowed", "rate_limit_rejected", "admission_rejected", "rate_limit_enabled"):
        assert k in ov
