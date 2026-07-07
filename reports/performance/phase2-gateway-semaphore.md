# Phase 2 — Global LLM concurrency cap (gateway semaphore)

Date: 2026-07-07. Change: `BoundedSemaphore` around the Ollama generation call in
`services/llm_gateway/server.py`. Env: `MAX_CONCURRENT_LLM_CALLS` (default 2),
`LLM_QUEUE_WAIT_TIMEOUT_SECONDS` (default 30). Overflow → controlled `RESOURCE_EXHAUSTED`
busy error (job error, never empty, never a hang). Fail-open, correctness unchanged.

## Test matrix — 10 concurrent, single-CPU Ollama (qwen3.5:9b)

| run | mode | cap | success | error | empty | timeout(420s) | p50 | p95 | max | real gens | gen dur (ok) |
|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| Phase 1 baseline | identical | ∞ (none) | 0/10 | 3 | 0 | 7 | 420s | 420s | 421s | 6 (all err) | — (all 248–284s err) |
| Phase 2 | identical | 2 | 10/10 | 0 | 0 | 0 | 1.2s | 1.7s | 1.7s | 0 | cache hits (warm) |
| Phase 2 | distinct (cold) | 2 | 6/10 | 4 | 0 | 0 | 256s | 406s | 408s | 10 | 44–147s ok / 208–248s err |
| Phase 2 | distinct (cold) | 1 | **9/10** | 1 | 0 | 0 | **76s** | 215s | 242s | 6 (4 semantic-coalesced) | **29–148s** |

Notes:
- The identical/cap=2 run served entirely from warm cache (0 generations) — proof the cache +
  empty-answer contract now work end-to-end (Phase-1 jobs that finished after the client
  deadline seeded it). Not a semaphore-under-load test; that is what the distinct runs are for.
- Distinct runs force cache misses = real generation. Redis was FLUSHed before the cap=1 run so
  both distinct runs started cold and are comparable.

## Findings
- **cap turns collapse into completion.** Phase 1: 0/10, every gen thrashed to 248–284s and
  errored. With a cap: gens complete near solo-time and success jumps.
- **cap=1 beats cap=2 on this box.** One CPU serves ~1 generation cleanly; full serialization
  avoids all thrash (gens 29–148s, p50 76s, 9/10). cap=2 already induces thrash (gens stretch
  to 248s, 4 errors, p50 256s). On this single-CPU deployment **MAX_CONCURRENT_LLM_CALLS=1 is
  recommended**; keep 2 only for multi-core / GPU / multi-replica Ollama.
- **Empty responses stayed 0** in every Phase-2 run — the fallback contract holds under the cap.
- **Overflow is controlled**, not a collapse: gateway logs show `llm_semaphore_timeout waited=30.0`
  → `RESOURCE_EXHAUSTED` → job error at ~30s, vs Phase-1's 420s hangs.
- **Residual tail errors remain** even at cap=1 (1 gen at 201s): the uncapped **bge-m3 embeddings**
  from the 8 concurrent queries co-thrash the CPU alongside generation. This is a Phase-4/5 item
  (move heavy embed off the hot path / cap it), NOT part of Phase 2.
- **Duplicate generation still happens for truly-distinct questions** (10 gens for 10 distinct at
  cap=2) — expected; Phase 3 single-flight collapses *identical/paraphrase* storms, not distinct.

## Is Phase 3 (Redis single-flight) still needed? YES.
Phase 2 bounds concurrency but does not coalesce duplicates. An identical-question storm with a
COLD cache would still generate up to `cap` copies before the first completes and seeds the cache.
Single-flight at job submit collapses N identical/paraphrase to ~1 generation + N-1 waiters. The
semantic cache already coalesces *some* near-duplicates once warm (seen in the cap=1 run: 4 of 10
distinct served from cache), but only single-flight closes the cold-start duplicate window.

## Gateway semaphore structured logs (grep `llm_semaphore` / `llm_generation` in `docker compose logs llm-gateway`)
`llm_semaphore_waiting` · `llm_semaphore_acquired` · `llm_semaphore_released` ·
`llm_semaphore_timeout` · `llm_generation_started` · `llm_generation_finished` (with `elapsed_s`),
each with `label` (Ask/AskStream), `active` (in-flight count), `max`.

## Recommended next
1. Set `MAX_CONCURRENT_LLM_CALLS=1` on this single-CPU deployment (measured optimum).
2. Phase 3 — Redis SETNX single-flight at job submit (coalesce identical storms).
3. Later (Phase 4/5) — take bge-m3 embeddings off the query hot path so they stop co-thrashing gens.
