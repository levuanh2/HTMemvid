"""Phase 3 — single-flight / request coalescing.

Two layers, no real Redis and no Ollama:
  A) pure key policy (llm_cache.single_flight_key) — coalescing scope + safety
  B) decision logic (app.main._single_flight_try) with a fake Redis + stubbed cache.
"""
from __future__ import annotations

import threading
import time

import pytest

import app.main as main
from app.clients import redis_client
from app.domains.cache import llm_cache


# --------------------------------------------------------------- A) key policy
def test_key_coalesces_vietnamese_variants():
    a = llm_cache.single_flight_key("noi dung la gi", [], use_memory_tree=True)
    b = llm_cache.single_flight_key("nội dung là gì", [], use_memory_tree=True)
    c = llm_cache.single_flight_key("nọi dung là gì", [], use_memory_tree=True)
    assert a and a == b == c  # VN diacritic variants share ONE leader


def test_key_never_coalesces_across_documents():
    generic = "nội dung là gì"
    k_all = llm_cache.single_flight_key(generic, [], use_memory_tree=True)
    k_doc1 = llm_cache.single_flight_key(generic, ["doc_a"], use_memory_tree=True)
    k_doc2 = llm_cache.single_flight_key(generic, ["doc_b"], use_memory_tree=True)
    assert len({k_all, k_doc1, k_doc2}) == 3  # different context = different key


def test_key_none_for_unsafe_query():
    assert llm_cache.single_flight_key("mật khẩu tài khoản của tôi là gì", []) is None


# --------------------------------------------------------------- fake redis
class FakeRedis:
    def __init__(self):
        self.store = {}
        self.last_ex = None

    def set(self, k, v, nx=False, ex=None):
        self.last_ex = ex
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def get(self, k):
        return self.store.get(k)

    def exists(self, k):
        return 1 if k in self.store else 0

    def eval(self, lua, numkeys, key, arg):
        if self.store.get(key) == arg:
            self.store.pop(key, None)
            return 1
        return 0

    def ping(self):
        return True


class _CacheStub:
    """Returns a sequence of values across calls; last value repeats."""
    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def __call__(self, _cache_key):
        v = self.seq[min(self.calls, len(self.seq) - 1)]
        self.calls += 1
        return v


_GOOD = {"payload": {"answer": "real answer"}, "status": 200}


@pytest.fixture
def sf_env(monkeypatch):
    monkeypatch.setenv("SINGLE_FLIGHT_ENABLED", "true")
    monkeypatch.setenv("SINGLE_FLIGHT_WAIT_SECONDS", "1.0")
    monkeypatch.setenv("SINGLE_FLIGHT_POLL_INTERVAL_SECONDS", "0.05")
    monkeypatch.setenv("SINGLE_FLIGHT_LOCK_TTL_SECONDS", "180")
    monkeypatch.setattr(main, "_get_session_history_safe", lambda sid, n: [])
    served = []
    monkeypatch.setattr(main, "_finalize_from_cache",
                        lambda jid, sid, q, cached: served.append((jid, cached)))
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    yield {"fake": fake, "served": served, "monkeypatch": monkeypatch}
    redis_client.reset_for_tests(None)


# --------------------------------------------------------------- B) decision logic
def test_leader_acquires_lock(sf_env):
    sf_env["monkeypatch"].setattr(main, "_get_cached_query", _CacheStub([None]))
    r = main._single_flight_try("j1", "nội dung là gì", [], True, None, None, "s1")
    assert r["served"] is False
    assert r["lock"] is not None
    key, token = r["lock"]
    assert sf_env["fake"].store.get(key) == token
    assert sf_env["fake"].last_ex == 180  # TTL passed to SET EX


def test_follower_served_from_warm_cache(sf_env):
    sf_env["monkeypatch"].setattr(main, "_get_cached_query", _CacheStub([_GOOD]))
    r = main._single_flight_try("j2", "nội dung là gì", [], True, None, None, "s1")
    assert r["served"] is True
    assert sf_env["served"] and sf_env["served"][0][0] == "j2"


def test_follower_waits_then_served_when_leader_writes(sf_env):
    # lock already held by a leader; cache empty for the first 2 polls, then appears.
    key = llm_cache.single_flight_key("nội dung là gì", [], use_memory_tree=True)
    sf_env["fake"].store[key] = "leader-token"
    sf_env["monkeypatch"].setattr(main, "_get_cached_query", _CacheStub([None, None, None, _GOOD]))
    r = main._single_flight_try("j3", "nội dung là gì", [], True, None, None, "s1")
    assert r["served"] is True
    assert sf_env["served"][0][0] == "j3"


def test_follower_timeout_fail_open(sf_env):
    key = llm_cache.single_flight_key("nội dung là gì", [], use_memory_tree=True)
    sf_env["fake"].store[key] = "leader-token"  # lock stays held, cache never fills
    sf_env["monkeypatch"].setattr(main, "_get_cached_query", _CacheStub([None]))
    t0 = time.time()
    r = main._single_flight_try("j4", "nội dung là gì", [], True, None, None, "s1")
    waited = time.time() - t0
    assert r == {"served": False, "lock": None}  # fail-open, no lock
    assert waited <= 3.0  # bounded by SINGLE_FLIGHT_WAIT_SECONDS


def test_follower_fail_open_when_leader_vanishes(sf_env):
    # lock present at start; disappears on first poll with no cache -> fail open early.
    key = llm_cache.single_flight_key("nội dung là gì", [], use_memory_tree=True)
    fake = sf_env["fake"]
    fake.store[key] = "leader-token"
    calls = {"n": 0}

    def cache_stub(_k):
        calls["n"] += 1
        if calls["n"] >= 2:      # after the first poll, simulate leader gone
            fake.store.pop(key, None)
        return None

    sf_env["monkeypatch"].setattr(main, "_get_cached_query", cache_stub)
    r = main._single_flight_try("j5", "nội dung là gì", [], True, None, None, "s1")
    assert r == {"served": False, "lock": None}


def test_redis_down_bypass(monkeypatch):
    monkeypatch.setenv("SINGLE_FLIGHT_ENABLED", "true")
    monkeypatch.setattr(main, "_get_session_history_safe", lambda sid, n: [])
    redis_client.reset_for_tests(None)  # no client -> get_redis() returns None
    try:
        r = main._single_flight_try("j6", "nội dung là gì", [], True, None, None, "s1")
        assert r == {"served": False, "lock": None}  # fail-open
    finally:
        redis_client.reset_for_tests(None)


def test_unsafe_query_bypass(sf_env):
    sf_env["monkeypatch"].setattr(main, "_get_cached_query", _CacheStub([None]))
    r = main._single_flight_try("j7", "mật khẩu tài khoản của tôi là gì", [], True, None, None, "s1")
    assert r == {"served": False, "lock": None}
    assert not sf_env["fake"].store  # never touched the lock


def test_disabled_bypass(sf_env):
    sf_env["monkeypatch"].setenv("SINGLE_FLIGHT_ENABLED", "false")
    sf_env["monkeypatch"].setattr(main, "_get_cached_query", _CacheStub([None]))
    r = main._single_flight_try("j8", "nội dung là gì", [], True, None, None, "s1")
    assert r == {"served": False, "lock": None}
    assert not sf_env["fake"].store


def test_release_frees_lock(sf_env):
    sf_env["monkeypatch"].setattr(main, "_get_cached_query", _CacheStub([None]))
    r = main._single_flight_try("j9", "nội dung là gì", [], True, None, None, "s1")
    key, token = r["lock"]
    assert sf_env["fake"].store.get(key) == token
    main._single_flight_release(key, token)
    assert key not in sf_env["fake"].store  # stale lock cannot block future requests
    # wrong token must NOT delete someone else's lock
    sf_env["fake"].store[key] = "other-token"
    main._single_flight_release(key, token)
    assert sf_env["fake"].store.get(key) == "other-token"


def test_two_leaders_for_two_documents(sf_env):
    sf_env["monkeypatch"].setattr(main, "_get_cached_query", _CacheStub([None]))
    r1 = main._single_flight_try("ja", "nội dung là gì", ["doc_a"], True, None, None, "s1")
    r2 = main._single_flight_try("jb", "nội dung là gì", ["doc_b"], True, None, None, "s2")
    assert r1["lock"] and r2["lock"]
    assert r1["lock"][0] != r2["lock"][0]  # no cross-document coalescing: both lead
