# Phase 3 — Single-flight / request coalescing

Date: 2026-07-07. Change: Redis SETNX single-flight at `app/main.py::query` job submit.
One leader per (semantic-cache bucket + no-diacritics query) runs the graph and writes the
cache; followers wait briefly and return the leader's cached answer. Optimization-only,
fail-open. Config on the BACKEND process (default cap=1 gateway + `SINGLE_FLIGHT_ENABLED=true`).

## Headline: 10 concurrent identical, COLD cache

| phase | success | empty | LLM generations | p50 | p95 | p99 |
|---|--:|--:|--:|--:|--:|--:|
| Phase 1 (no cap) | 0/10 | 0 | 6 (all errored) | 420s | 420s | 421s |
| Phase 2 (cap=1, distinct cold) | 9/10 | 0 | 6 | 76s | 215s | 236s |
| **Phase 3 (cap=1 + single-flight, identical cold)** | **10/10** | **0** | **1** | **84s** | **85s** | **85s** |

`node_logs` ground truth for the Phase-3 run: exactly **1** `GenerateAnswer` execution (38.7s)
served all 10 identical requests. `single_flight` metrics (per gunicorn worker):
`leader=1, follower=3, follower_hit=5, dup_avoided=5, timeout=0, fail_open=0, release_ok=1`.

## What changed vs Phase 2
- **Duplicate LLM generations for an identical storm dropped from 6 (P1) / N (P2) to 1.**
  Single-flight coalesces; the cap only bounds concurrency.
- **Success 10/10, empty 0.** All followers returned the leader's real answer.
- **p95 flat at ~85s** (leader's full job time) instead of a long thrash tail — every request
  finishes together the instant the leader writes cache.
- Latency floor is the LEADER's end-to-end job time (~84s: retrieve+embed+gen 38.7s+finalize),
  not the ~1s of a warm-cache hit — followers must wait for the first answer to exist.

## Single-flight key
```
{namespace}:{env}:sf:{bucket}:{eid_nd}
  bucket  = llm_cache._bucket_id(sources, language, category, use_memory_tree)
            # hashes ns/env/prompt_version/embed_model/late_chunking/index_version/sources/lang/cat/mem
  eid_nd  = sha256(strip_diacritics(normalize_question(q)))[:16]
```
- `bucket` includes `sources` + `index_version` → a generic question like "nội dung là gì"
  **never coalesces across different documents**, and any ingest/delete auto-invalidates the key.
- `eid_nd` is the no-diacritics normalized query → VN variants ("noi dung la gi" / "nội dung là
  gì" / "nọi dung là gì") share ONE leader. Followers still read the answer back through the
  exact→alias→cosine→**judge** cache path, so homograph safety is unchanged.

## Leader / follower behaviour
- **Leader:** first to `SET NX EX` the lock → runs the normal graph (cache write happens in the
  finalize node) → releases the lock (token compare-delete via Lua) AFTER finalize, in `finally`.
- **Follower:** lock already held → polls `_get_cached_query` (exact→semantic) every
  `SINGLE_FLIGHT_POLL_INTERVAL_SECONDS` up to `SINGLE_FLIGHT_WAIT_SECONDS`; on a non-empty answer
  → finalizes its own job with that result (atomic done+result). If the lock vanishes with no
  cache (leader errored) → fail-open immediately. On wait timeout → fail-open (runs its own graph).
- **Warm cache** at submit → served immediately, no lock taken.

## Redis / failure behaviour (all fail-open — never empty, never hang)
- `REDIS_URL` unset or Redis down (`get_redis()` None) → bypass, run normal graph.
- Lock `SET`/`exists` raises → `mark_unavailable()` + bypass.
- Leader crashes → lock TTL (`SINGLE_FLIGHT_LOCK_TTL_SECONDS`=180) expires; followers fail-open
  at wait timeout; stale lock can never block future requests forever.
- Empty/null cached answer → treated as miss (`_sf_nonempty`), keep waiting / fail-open.
- Unsafe query (`classify_risk` deny: personal/realtime/action) or follow-up (`is_standalone`
  false with history) → bypass, never coalesced.

## Env vars (backend service)
```
SINGLE_FLIGHT_ENABLED=true
SINGLE_FLIGHT_LOCK_TTL_SECONDS=180
SINGLE_FLIGHT_WAIT_SECONDS=120
SINGLE_FLIGHT_POLL_INTERVAL_SECONDS=0.5
```

## Logs (grep `singleflight` in `docker compose logs backend`)
`singleflight_disabled` · `singleflight_leader_acquired` · `singleflight_follower_waiting` ·
`singleflight_follower_cache_hit` · `singleflight_follower_timeout_fail_open` ·
`singleflight_lock_release_success|failed` · `singleflight_redis_error_fail_open` ·
`singleflight_bypass_unsafe`. Metrics at `/stats` → `single_flight`.

## Tests
`python -m pytest tests/test_single_flight.py` = **13 passed**: VN-variant coalescing, no
cross-document coalescing, unsafe→None, leader acquires (TTL passed), warm-cache serve,
follower-waits-then-served, follower timeout fail-open, leader-vanishes fail-open, Redis-down
bypass, unsafe bypass, disabled bypass, release frees lock (wrong-token safe), two docs → two leaders.

## Known tradeoff / next
- A waiting follower holds a per-worker query-semaphore slot for the leader's job duration
  (~84s here). Under a large storm the ~8 admission slots fill with waiters → excess requests get
  HTTP 429 (322 admission-429 retries seen). Acceptable, but it motivates **Phase 4** (ingress
  rate limit + `/ready` vs `/health` split + circuit view) so overload sheds politely.
- Residual per-generation slowness (leader 38.7s vs solo ~57s here is fine, but distinct storms
  still climb) is the uncapped bge-m3 embeddings — Phase 4/5.
