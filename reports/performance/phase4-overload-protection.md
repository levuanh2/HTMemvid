# Phase 4 — Ingress overload protection

Date: 2026-07-07. Adds a Redis token-bucket rate limit on `/query` (OFF by default), a `/ready`
readiness endpoint distinct from `/health` liveness, and structured admission-full responses.
All in `app/main.py`, all fail-open, no change to the Phase 2 gateway cap or Phase 3 single-flight.

## Load test — cold identical storms (cap=1, single-flight on, rate limit OFF)

| run | conc | success | empty | error | timeout | LLM gens | p50 | p95 | admission_rejected (worker) |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| Phase 1 (no protection) | 10 | 0/10 | 0 | 10 | 7 | 6 | 420s | 420s | n/a |
| Phase 4 | 10 | 10/10 | 0 | 0 | 0 | 1 | 86s | 86s | (few) |
| **Phase 4** | **50** | **50/50** | **0** | **0** | **0** | **1** | **3.0s** | **70s** | **341** |

50 concurrent identical, cold: **1 generation (38.5s) served all 50**; overload shed as
**structured HTTP 429 `admission_rejected`** (341 rejections on one worker, 186 client-side
429 retries total), every request eventually served from the leader's cache. No collapse, no
empty, no hang — the exact failure Phase 1 showed (0/10) is now 50/50.

## `/health` vs `/ready`
- **`/health` (liveness, unchanged):** 200 if the web process is up. Never depends on Redis/LLM.
  Used by the compose healthcheck.
- **`/ready` (new, readiness):** 200 when the worker can take meaningful traffic; **503** otherwise.
  Body: `{status, redis: ok|down|disabled, llm_gateway: unknown, query_graph_ready, admission_available, reason?}`.
  Not-ready reasons: `graph_not_ready`, `admission_saturated` (this worker's admission semaphore at 0),
  `redis_required_down` (only when `RATE_LIMIT_REQUIRE_REDIS=true`). Lets a load balancer back off a
  saturated worker instead of piling on. (`llm_gateway` health is `unknown` — future: cheap gRPC probe.)

## Rate limit
- **Location:** `app/main.py::query`, first thing after input validation, before the admission
  semaphore. Helper `_rate_limit_check(scope_id)` → `(allowed, retry_after)`.
- **Algorithm:** Redis token bucket via a small Lua script (atomic): refill `RATE_LIMIT_RPS`
  tokens/sec up to `RATE_LIMIT_BURST`, spend 1 per request; key TTL `RATE_LIMIT_WINDOW_SECONDS`.
- **Scope:** `RATE_LIMIT_SCOPE=ip` (X-Forwarded-For first, else remote_addr) or `session` (session_id).
- **Reject:** HTTP 429 `{"error":"rate_limited","message":...,"retry_after_seconds":N}` + `Retry-After`.
- **Fail-open:** disabled by default; Redis unavailable → allow (unless `RATE_LIMIT_REQUIRE_REDIS=true`,
  for strict prod). Never applied to `/health`, `/ready`, `/stats`, or static assets.

## Admission-full response (improved)
Was: `{"error":"Too many concurrent queries, please retry."}` 429.
Now: `{"error":"admission_rejected","message":"Server is at capacity, please retry shortly.",
"retry_after_seconds":N}` 429 + `Retry-After` (`ADMISSION_RETRY_AFTER_SECONDS`, default 5),
logged `admission_rejected` + counted in `/stats.overload.admission_rejected`. Same 429 status =
frontend-compatible (the baseline script's 429-retry logic worked unchanged).

## Overload / circuit view (`/stats.overload`, per worker)
`rate_limit_allowed`, `rate_limit_rejected`, `rate_limit_redis_error`, `admission_rejected`,
`rate_limit_enabled`, `admission_available`, `admission_capacity`. Plus `/stats.single_flight`
(leader/follower/dup_avoided/timeout/fail_open) from Phase 3. LLM busy/timeout counts live in the
gateway logs (`llm_semaphore_timeout`) — cross-process aggregation is future work.

## Env vars (backend, host-overridable in compose; conservative local defaults)
```
RATE_LIMIT_ENABLED=false
RATE_LIMIT_RPS=1
RATE_LIMIT_BURST=5
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_SCOPE=ip
RATE_LIMIT_REQUIRE_REDIS=false
ADMISSION_RETRY_AFTER_SECONDS=5
```

## Logs (grep `overload` / `readiness` in `docker compose logs backend`)
`rate_limit_allowed` · `rate_limit_rejected` · `rate_limit_redis_error_fail_open` ·
`admission_rejected` · `overload_response_sent` · `readiness_check_ok` · `readiness_check_failed`.

## Tests — `pytest tests/test_overload.py tests/test_single_flight.py tests/test_llm_gateway_semaphore.py` = 33 passed
Overload (12): rate-limit allow-then-reject, disabled allows, redis-down fail-open,
require-redis rejects-when-down, structured 429 (rate + admission) w/ Retry-After, `/health`
liveness no-redis, `/ready` ok, `/ready` 503 (graph-not-ready / redis-required-down /
admission-saturated), `/stats` overload block. Phase 3 (13) + Phase 2 (8) still green.

## Empty responses stayed 0 across every Phase-4 run.

## Next: Phase 5 (RQ worker) or stop here?
Ingress is now controlled. The remaining structural item is Phase 5 — move heavy ingest/mindmap/
summary (embed+LLM) onto an RQ worker so they stop competing with interactive queries for the one
CPU (also the root of the residual bge-m3 co-thrash seen in Phase 2/3). Recommended before a real
multi-user launch; optional if the workload is query-dominated. Rate limit + `/ready` should be
enabled in prod (`RATE_LIMIT_ENABLED=true`, front the app with a LB that honors `/ready`).
