"""Auth Hardening Phase E — user-scoped cache + single-flight keys.

Unit tests exercise the pure key functions (no Redis). Graph tests use the real
query graph (tests/_qg_build) with a fake in-memory cache dict to prove cross-user
isolation and same-user reuse through the cache_scope threaded on the state.
"""

from __future__ import annotations

import json

import pytest

from app.domains.cache import llm_cache


@pytest.fixture()
def be(client):
    import app.main as main
    return main


# ---- 1-2: semantic bucket scope --------------------------------------------

def test_bucket_id_differs_by_scope():
    assert llm_cache._bucket_id(["s1"], None, None, False, "u1") != \
           llm_cache._bucket_id(["s1"], None, None, False, "u2")


def test_bucket_id_public_matches_default():
    # "public" (flag off / old keys) reproduces the default (pre-Phase-E) bucket.
    assert llm_cache._bucket_id(["s1"], None, None, False, "public") == \
           llm_cache._bucket_id(["s1"], None, None, False)


# ---- 3: single-flight scope ------------------------------------------------

def test_single_flight_key_differs_by_scope():
    a = llm_cache.single_flight_key("nội dung là gì", ["s1"], None, None, True, "u1")
    b = llm_cache.single_flight_key("nội dung là gì", ["s1"], None, None, True, "u2")
    assert a and b and a != b  # A and B never coalesce under enforcement


def test_single_flight_key_same_scope_coalesces():
    # same user + same resolved sources + same query → identical key (may coalesce)
    a = llm_cache.single_flight_key("nội dung là gì", ["s1"], None, None, True, "u1")
    b = llm_cache.single_flight_key("nội dung là gì", ["s1"], None, None, True, "u1")
    assert a == b


# ---- 4: retrieval cache scope ----------------------------------------------

def test_retrieval_key_differs_by_scope():
    a = llm_cache._retrieval_key("q", ["s1"], 4, None, None, "u1")
    b = llm_cache._retrieval_key("q", ["s1"], 4, None, None, "u2")
    assert a != b
    # public default preserves the flag-off key shape
    assert llm_cache._retrieval_key("q", ["s1"], 4, None, None, "public") == \
           llm_cache._retrieval_key("q", ["s1"], 4, None, None)


# ---- 5-6: main query cache key ---------------------------------------------

def test_make_query_cache_key_differs_by_scope(be):
    a = be._make_query_cache_key("q", ["s1"], True, None, "u1")
    b = be._make_query_cache_key("q", ["s1"], True, None, "u2")
    assert a != b
    assert json.loads(a)["cache_scope"] == "u1"


def test_old_key_without_scope_parses_as_public():
    # key shape emitted before Phase E (no cache_scope field)
    old = json.dumps({"q": "x", "sources": ["s1"], "use_memory_tree": False,
                      "category": None, "language": None}, sort_keys=True)
    d = llm_cache._parse_cache_key(old)
    assert d is not None
    scope = d.get("cache_scope") or "public"
    assert scope == "public"
    # bucket from an old key equals the explicit-public bucket → backward compatible
    assert llm_cache._bucket_id(d["sources"], d["language"], d["category"], d["use_memory_tree"], scope) == \
           llm_cache._bucket_id(["s1"], None, None, False, "public")


# ---- 11-12: resolved sources + sentinel scoping ----------------------------

def test_protected_key_uses_resolved_sources_and_scope(be):
    public_empty = be._make_query_cache_key("q", [], True, None, "public")
    resolved = be._make_query_cache_key("q", ["doc_a"], True, None, "userA")
    assert public_empty != resolved
    d = json.loads(resolved)
    assert d["sources"] == ["doc_a"] and d["cache_scope"] == "userA"


def test_no_owned_sentinel_does_not_collide_across_users(be):
    a = be._make_query_cache_key("q", be._NO_OWNED_SOURCES, True, None, "userA")
    b = be._make_query_cache_key("q", be._NO_OWNED_SOURCES, True, None, "userB")
    assert a != b  # sentinel + distinct scope → distinct key


# ---- 13: flag-off compatibility --------------------------------------------

def test_flag_off_key_is_public(be):
    k = be._make_query_cache_key("q", ["s1"], True, None)
    assert json.loads(k)["cache_scope"] == "public"


# ---- 7-8: real-graph isolation + reuse -------------------------------------

def test_cross_user_no_shared_cache_real_graph(monkeypatch):
    from tests import _qg_build as qb
    qb.base_env(monkeypatch)
    g, cache = qb.build()
    qb.run(g, qb.init_state("một câu hỏi độc lập rõ ràng", cache_scope="userA"), thread_id="ta")
    qb.run(g, qb.init_state("một câu hỏi độc lập rõ ràng", cache_scope="userB"), thread_id="tb")
    keys = list(cache)
    assert any("::userA::" in k for k in keys)
    assert any("::userB::" in k for k in keys)
    assert len(keys) == 2  # separate entries → B never reads A's answer


def test_same_user_same_query_reuses_cache_real_graph(monkeypatch):
    from tests import _qg_build as qb
    qb.base_env(monkeypatch)
    calls = {"n": 0}

    def _sum(*a, **k):
        calls["n"] += 1
        return "generated answer"

    g, cache = qb.build(summarize=_sum)
    qb.run(g, qb.init_state("một câu hỏi độc lập rõ ràng", cache_scope="u1"), thread_id="t1")
    qb.run(g, qb.init_state("một câu hỏi độc lập rõ ràng", cache_scope="u1"), thread_id="t2")
    assert calls["n"] == 1  # second run hit the u1-scoped cache (no re-generate)
