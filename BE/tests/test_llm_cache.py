from __future__ import annotations

import fnmatch
import importlib
import json
from collections import Counter

import numpy as np
import pytest

from tests._qg_build import base_env, build, init_state, run


class FakeRedis:
    def __init__(self):
        self._values: dict[str, object] = {}
        self._expires_at: dict[str, float] = {}
        self._now = 0.0

    def _time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)

    def _purge_if_expired(self, name: str) -> None:
        exp = self._expires_at.get(name)
        if exp is not None and exp <= self._time():
            self._values.pop(name, None)
            self._expires_at.pop(name, None)

    def get(self, name):
        self._purge_if_expired(name)
        return self._values.get(name)

    def set(self, name, value, ex=None):
        self._values[name] = value
        if ex is None:
            self._expires_at.pop(name, None)
        else:
            self._expires_at[name] = self._time() + float(ex)
        return True

    def setex(self, name, time, value):
        self._values[name] = value
        self._expires_at[name] = self._time() + float(time)
        return True

    def delete(self, *names):
        removed = 0
        for name in names:
            self._purge_if_expired(name)
            if name in self._values:
                removed += 1
            self._values.pop(name, None)
            self._expires_at.pop(name, None)
        return removed

    def sadd(self, name, *values):
        self._purge_if_expired(name)
        cur = self._values.get(name)
        if not isinstance(cur, set):
            cur = set()
            self._values[name] = cur
        added = 0
        for value in values:
            if value not in cur:
                added += 1
            cur.add(value)
        return added

    def srem(self, name, *values):
        self._purge_if_expired(name)
        cur = self._values.get(name)
        if not isinstance(cur, set):
            return 0
        removed = 0
        for value in values:
            if value in cur:
                cur.remove(value)
                removed += 1
        return removed

    def smembers(self, name):
        self._purge_if_expired(name)
        cur = self._values.get(name)
        if isinstance(cur, set):
            return set(cur)
        return set()

    def expire(self, name, time):
        self._purge_if_expired(name)
        if name not in self._values:
            return False
        self._expires_at[name] = self._time() + float(time)
        return True

    def mget(self, names):
        return [self.get(name) for name in names]

    def scan_iter(self, match=None):
        pattern = match or "*"
        for name in list(self._values):
            self._purge_if_expired(name)
        for name in sorted(self._values):
            if fnmatch.fnmatch(name, pattern):
                yield name

    def ping(self):
        return True


class RaisingRedis:
    def __init__(self):
        self.calls = Counter()

    def _raise(self, method: str):
        self.calls[method] += 1
        raise ConnectionError("redis down")

    def get(self, name):
        return self._raise("get")

    def set(self, name, value, ex=None):
        return self._raise("set")

    def setex(self, name, time, value):
        return self._raise("setex")

    def delete(self, *names):
        return self._raise("delete")

    def sadd(self, name, *values):
        return self._raise("sadd")

    def srem(self, name, *values):
        return self._raise("srem")

    def smembers(self, name):
        return self._raise("smembers")

    def expire(self, name, time):
        return self._raise("expire")

    def mget(self, names):
        return self._raise("mget")

    def scan_iter(self, match=None):
        return self._raise("scan_iter")

    def ping(self):
        return self._raise("ping")


@pytest.fixture(autouse=True)
def _reset_cache_state():
    yield
    try:
        import app.clients.redis_client as redis_client

        redis_client.reset_for_tests(None)
    except Exception:
        pass
    try:
        import app.domains.cache.llm_cache as llm_cache

        llm_cache.METRICS.clear()
    except Exception:
        pass


def _emb(values: list[float]) -> np.ndarray:
    return np.asarray([values], dtype=np.float32)


def _make_cache_key(
    q: str,
    sources: list[str] | None = None,
    *,
    use_memory_tree: bool = False,
    category: str | None = None,
    language: str | None = None,
) -> str:
    return json.dumps(
        {
            "q": (q or "").strip(),
            "sources": sorted(str(s) for s in (sources or []) if s is not None),
            "use_memory_tree": bool(use_memory_tree),
            "category": category,
            "language": language,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _load_cache_modules(monkeypatch, **env):
    defaults = {
        "CACHE_ENABLED": "1",
        "REDIS_URL": "redis://fake:6379/0",
        "SEMANTIC_CACHE_ENABLED": "1",
        "RETRIEVAL_CACHE_ENABLED": "1",
        "SEMANTIC_CACHE_TTL_SECONDS": "172800",
        "RETRIEVAL_CACHE_TTL_SECONDS": "3600",
        "SEMANTIC_CACHE_THRESHOLD": "0.85",
        "SEMANTIC_CACHE_THRESHOLD_FLOOR_OVERRIDE": "0",
        "CACHE_NAMESPACE": "memvid",
        "CACHE_ENV": "test",
    }
    defaults.update({k: str(v) for k, v in env.items()})
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)

    import app.clients.redis_client as redis_client
    import app.domains.cache.llm_cache as llm_cache

    redis_client = importlib.reload(redis_client)
    llm_cache = importlib.reload(llm_cache)
    redis_client.reset_for_tests(None)
    llm_cache.METRICS.clear()
    return redis_client, llm_cache


def _patch_vectors(monkeypatch, llm_cache, mapping: dict[str, np.ndarray]):
    monkeypatch.setattr(
        llm_cache,
        "encode_query_cached",
        lambda q: mapping.get(q),
    )


def _patch_index(monkeypatch, llm_cache, version: str):
    monkeypatch.setattr(llm_cache, "index_version", lambda: version)


def _value(answer: str) -> dict:
    return {"payload": {"answer": answer}, "status": 200}


def _first_key(fake: FakeRedis, pattern: str) -> str:
    return next(iter(fake.scan_iter(pattern)))


def test_exact_repeat_hit_increments_hits_exact(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    _patch_vectors(monkeypatch, llm_cache, {"same question": _emb([1, 0, 0, 0, 0, 0, 0, 0])})

    key = _make_cache_key("same question", ["s1"])
    value = _value("cached exact")
    llm_cache.semantic_store(key, value)

    monkeypatch.setattr(llm_cache, "encode_query_cached", lambda q: (_ for _ in ()).throw(AssertionError("exact hit should not embed")))
    assert llm_cache.semantic_lookup(key) == value
    assert llm_cache.METRICS["hits_exact"] == 1


def test_semantic_hit_increments_hits_semantic(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    # Lưu ý: câu hỏi phải trung tính — từ khóa như "password" dính risk-regex và bị deny store.
    _patch_vectors(
        monkeypatch,
        llm_cache,
        {
            "late chunking là gì": _emb([1, 0, 0, 0, 0, 0, 0, 0]),
            "giải thích late chunking": _emb([0.9, 0.4358899, 0, 0, 0, 0, 0, 0]),
        },
    )

    stored_key = _make_cache_key("late chunking là gì", ["s1"])
    lookup_key = _make_cache_key("giải thích late chunking", ["s1"])
    value = _value("reuse semantic answer")
    llm_cache.semantic_store(stored_key, value)

    assert llm_cache.semantic_lookup(lookup_key) == value
    assert llm_cache.METRICS["hits_semantic"] == 1


def test_low_similarity_miss(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    _patch_vectors(
        monkeypatch,
        llm_cache,
        {
            "cached question": _emb([1, 0, 0, 0, 0, 0, 0, 0]),
            "different intent": _emb([0.2, 0.9797959, 0, 0, 0, 0, 0, 0]),
        },
    )

    llm_cache.semantic_store(_make_cache_key("cached question", ["s1"]), _value("answer"))
    assert llm_cache.semantic_lookup(_make_cache_key("different intent", ["s1"])) is None
    assert llm_cache.METRICS["misses"] == 1


def test_different_sources_use_different_bucket(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    _patch_vectors(monkeypatch, llm_cache, {"same question": _emb([1, 0, 0, 0, 0, 0, 0, 0])})

    llm_cache.semantic_store(_make_cache_key("same question", ["source-a"]), _value("answer"))
    assert llm_cache.semantic_lookup(_make_cache_key("same question", ["source-b"])) is None


def test_index_version_change_causes_semantic_miss(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    version = {"value": "idx-v1"}
    monkeypatch.setattr(llm_cache, "index_version", lambda: version["value"])
    _patch_vectors(monkeypatch, llm_cache, {"same question": _emb([1, 0, 0, 0, 0, 0, 0, 0])})

    key = _make_cache_key("same question", ["s1"])
    llm_cache.semantic_store(key, _value("answer"))
    version["value"] = "idx-v2"

    assert llm_cache.semantic_lookup(key) is None


def test_history_bypass_returns_cache_key_none(monkeypatch):
    base_env(monkeypatch)
    graph, _cache = build()
    state = init_state(
        "hoi dap",
        conversation_history=[{"role": "user", "content": "truoc do"}],
    )

    out = run(graph, state, thread_id="history-bypass")
    assert out["cache_key"] is None
    assert not out.get("done", False)


def test_is_standalone_question_heuristic():
    from app.domains.cache import llm_cache

    # standalone: tự đứng được, không marker ngữ cảnh
    assert llm_cache.is_standalone_question("phishing là gì trong an ninh mạng")
    assert llm_cache.is_standalone_question("trình bày các bước tấn công SQL injection")
    # follow-up: câu cụt / anaphora / mở đầu nối tiếp
    assert not llm_cache.is_standalone_question("tại sao?")
    assert not llm_cache.is_standalone_question("giải thích rõ hơn về nó giúp mình")
    assert not llm_cache.is_standalone_question("còn phần 2 thì sao bạn")
    assert not llm_cache.is_standalone_question("nói kỹ hơn về phần này đi bạn")
    assert not llm_cache.is_standalone_question("can you explain that in more detail")


def test_standalone_question_with_history_uses_cache(monkeypatch):
    """Câu standalone trong multi-turn: vẫn có cache_key, generate KHÔNG nhét history
    vào prompt, và lần hỏi lặp lại hit cache (không generate lần 2)."""
    base_env(monkeypatch)
    seen_qs: list[str] = []

    def _summarize(q, chunks, **kwargs):
        seen_qs.append(q)
        return "generated answer"

    graph, cache = build(summarize=_summarize)
    hist = [{"role": "user", "content": "cau truoc do"}, {"role": "assistant", "content": "tra loi truoc do"}]
    q = "phishing là gì trong an ninh mạng"

    out1 = run(graph, init_state(q, conversation_history=list(hist)), thread_id="standalone-1")
    assert out1["cache_key"] == f"ck::{q}"
    assert len(seen_qs) == 1
    assert "Lịch sử trò chuyện" not in seen_qs[0]  # history bị bỏ khỏi prompt
    assert cache  # Finalize đã ghi cache

    out2 = run(graph, init_state(q, conversation_history=list(hist)), thread_id="standalone-2")
    assert out2.get("done") is True
    assert len(seen_qs) == 1  # không generate lần 2 — trả từ cache
    assert out2["payload"] == out1["payload"]


def test_sensitive_question_is_not_cached(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    _patch_vectors(monkeypatch, llm_cache, {"mật khẩu của tôi là gì": _emb([1, 0, 0, 0, 0, 0, 0, 0])})

    assert llm_cache.classify_risk("mật khẩu của tôi là gì") == (False, "personal")
    assert llm_cache.classify_risk("what is the weather today") == (False, "realtime")

    key = _make_cache_key("mật khẩu của tôi là gì", ["s1"])
    llm_cache.semantic_store(key, _value("secret"))

    assert llm_cache.semantic_lookup(key) is None
    assert llm_cache.METRICS["bypass_risk"] == 1
    assert llm_cache.METRICS["writes"] == 0


def test_expired_entry_misses_and_cleans_ids_set(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch, SEMANTIC_CACHE_TTL_SECONDS="1")
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    _patch_vectors(
        monkeypatch,
        llm_cache,
        {
            "stored question": _emb([1, 0, 0, 0, 0, 0, 0, 0]),
            "paraphrase question": _emb([0.95, 0.3122499, 0, 0, 0, 0, 0, 0]),
        },
    )

    llm_cache.semantic_store(_make_cache_key("stored question", ["s1"]), _value("answer"))
    ids_key = _first_key(fake, "*:ids")
    fake._expires_at[ids_key] = fake._time() + 999
    fake.advance(2)

    assert llm_cache.semantic_lookup(_make_cache_key("paraphrase question", ["s1"])) is None
    assert fake.smembers(ids_key) == set()


def test_raising_redis_fails_open_and_respects_unavailable_window(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    raising = RaisingRedis()
    redis_client.reset_for_tests(raising)
    _patch_index(monkeypatch, llm_cache, "idx-v1")

    class _Clock:
        def __init__(self):
            self.now = 1000.0

        def time(self):
            return self.now

    clock = _Clock()
    monkeypatch.setattr(redis_client.time, "time", clock.time)
    monkeypatch.setattr(llm_cache.time, "time", clock.time)

    key = _make_cache_key("fail open", ["s1"])
    assert llm_cache.semantic_lookup(key) is None
    assert llm_cache.METRICS["errors"] == 1
    assert raising.calls["get"] == 1

    assert llm_cache.semantic_lookup(key) is None
    assert raising.calls["get"] == 1

    clock.now += 61
    assert llm_cache.semantic_lookup(key) is None
    assert raising.calls["get"] == 2


def test_threshold_floor_clamps_unless_override(monkeypatch, caplog):
    caplog.set_level("WARNING")

    redis_client, llm_cache = _load_cache_modules(monkeypatch, SEMANTIC_CACHE_THRESHOLD="0.5")
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    _patch_vectors(
        monkeypatch,
        llm_cache,
        {
            "stored": _emb([1, 0, 0, 0, 0, 0, 0, 0]),
            "query": _emb([0.75, 0.6614378, 0, 0, 0, 0, 0, 0]),
        },
    )
    llm_cache.semantic_store(_make_cache_key("stored", ["s1"]), _value("answer"))

    assert llm_cache.semantic_lookup(_make_cache_key("query", ["s1"])) is None
    assert "0.80" in caplog.text or "0.8" in caplog.text

    redis_client, llm_cache = _load_cache_modules(
        monkeypatch,
        SEMANTIC_CACHE_THRESHOLD="0.5",
        SEMANTIC_CACHE_THRESHOLD_FLOOR_OVERRIDE="1",
    )
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    _patch_vectors(
        monkeypatch,
        llm_cache,
        {
            "stored": _emb([1, 0, 0, 0, 0, 0, 0, 0]),
            "query": _emb([0.75, 0.6614378, 0, 0, 0, 0, 0, 0]),
        },
    )
    llm_cache.semantic_store(_make_cache_key("stored", ["s1"]), _value("answer"))

    assert llm_cache.semantic_lookup(_make_cache_key("query", ["s1"])) == _value("answer")


def test_none_embedding_bypasses_without_crash(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    _patch_vectors(monkeypatch, llm_cache, {"stored question": _emb([1, 0, 0, 0, 0, 0, 0, 0])})
    llm_cache.semantic_store(_make_cache_key("stored question", ["s1"]), _value("answer"))

    monkeypatch.setattr(llm_cache, "encode_query_cached", lambda q: None)
    assert llm_cache.semantic_lookup(_make_cache_key("lookup question", ["s1"])) is None
    assert llm_cache.METRICS["bypass_no_embedding"] == 1


def test_retrieval_cache_roundtrip_with_retrieved_chunk(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")

    from app.domains.retrieval.hybrid import RetrievedChunk

    chunks = [
        RetrievedChunk(
            chunk_id=7,
            text="cache me",
            video_stem="doc-a",
            bm25_score=1.2,
            vector_score=0.9,
            category="faq",
            language="vi",
        )
    ]

    llm_cache.retrieval_put("hoi dap", ["doc-a"], 4, "faq", "vi", chunks)
    out = llm_cache.retrieval_get("hoi dap", ["doc-a"], 4, "faq", "vi")

    assert out == chunks


def test_retrieval_cache_miss_after_index_version_change(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(monkeypatch)
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    version = {"value": "idx-v1"}
    monkeypatch.setattr(llm_cache, "index_version", lambda: version["value"])

    from app.domains.retrieval.hybrid import RetrievedChunk

    chunks = [RetrievedChunk(chunk_id=1, text="x", video_stem="doc")]
    llm_cache.retrieval_put("q", ["doc"], 3, None, None, chunks)
    version["value"] = "idx-v2"

    assert llm_cache.retrieval_get("q", ["doc"], 3, None, None) is None


def test_stats_returns_counters_and_config_echo(monkeypatch):
    redis_client, llm_cache = _load_cache_modules(
        monkeypatch,
        CACHE_NAMESPACE="ns-test",
        CACHE_ENV="ci",
        SEMANTIC_CACHE_THRESHOLD="0.91",
    )
    fake = FakeRedis()
    redis_client.reset_for_tests(fake)
    _patch_index(monkeypatch, llm_cache, "idx-v1")
    _patch_vectors(monkeypatch, llm_cache, {"same question": _emb([1, 0, 0, 0, 0, 0, 0, 0])})

    key = _make_cache_key("same question", ["s1"])
    llm_cache.semantic_store(key, _value("answer"))
    llm_cache.semantic_lookup(key)

    out = llm_cache.stats()
    assert isinstance(out, dict)
    assert out["hits_exact"] == 1
    assert out["writes"] == 1
    assert out["cache_namespace"] == "ns-test"
    assert out["cache_env"] == "ci"
    assert out["semantic_cache_threshold"] == 0.91
