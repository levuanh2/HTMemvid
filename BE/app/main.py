import os
from pathlib import Path

# Load .env early so all modules see env vars (Windows/dev friendly).
try:
    from shared.env_loader import load_project_env
    load_project_env(override=False)
except Exception:
    pass

import unicodedata
import json
import re
import uuid
import threading
import time
import logging
import signal
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any, Callable
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS

# File locking (Unix only, fallback on Windows)
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

from app.domains.ingest.ingest_utils import extract_text, split_text
from app.domains.ingest.video_utils import  save_qr_frames_to_video
from app.domains.vectorstore.store import (
    append_to_index,
    search_index,
    delete_source_from_index,
    delete_chunks_by_source,
    rebuild_chunk_index,
    MODEL_NAME,
)
from app.clients.llm_factory import ask_ai, summarize_results
from app.domains.cache import llm_cache
# Chỉ dùng cho local Ollama (Gemini sẽ bỏ qua model).
SLM_MODEL = os.environ.get("SLM_MODEL_CHAT", os.environ.get("SLM_MODEL", "qwen3.6:35b-a3b"))
from app.domains.mindmap import store as mindmap_store
from app.domains.mindmap.input_collector import collect_mindmap_input
from services.mindmap.pipeline import schema as mindmap_schema
from app.domains.summary import store as summary_store
from services.summary.pipeline import schema as summary_schema
from app.domains.ingest.chunk_processor import process_and_store_chunks
from app.domains.memory.tree import (
    build_memory_tree_for_sources,
    query_with_memory_tree,
    delete_memory_tree_by_source,
    rebuild_memory_index,
    _normalize_video_stem,
)
app = Flask(__name__)

# Init SQLite job store (idempotent). Chưa thay logic endpoint ở bước này.
try:
    from app.domains.jobs.jobs_store import init_db as _jobs_init_db, migrate_from_dict as _jobs_migrate_from_dict, mark_interrupted_jobs as _jobs_mark_interrupted
    _jobs_init_db()
except Exception:
    _jobs_migrate_from_dict = None
    _jobs_mark_interrupted = None

# Conversation Context Layer store (idempotent; feature-flagged at the call sites).
try:
    from app.domains.conversation.store import init_db as _conv_init_db
    _conv_init_db()
except Exception:
    _conv_init_db = None

# Debug log AI mode (không in ra API key thật)
print("=== AI MODE ===")
print("OLLAMA_HOST:", os.getenv("OLLAMA_HOST"))
print("GEMINI_API_KEY:", "SET" if os.getenv("GEMINI_API_KEY") else "MISSING")

# CORS:
# - Mặc định giữ hành vi hiện tại (cho phép tất cả origins) để không phá flow/FE.
# - Khi deploy (Railway + Vercel) nên set CORS_ORIGINS để allowlist domain Vercel.
_cors_origins_raw = (os.environ.get("CORS_ORIGINS") or "*").strip()
if _cors_origins_raw == "*":
    _cors_origins: str | list[str] = "*"
else:
    _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

CORS(
    app,
    resources={r"/*": {"origins": _cors_origins}},
    methods=["GET", "POST", "DELETE", "OPTIONS"],
    # "Authorization" for Bearer-token auth (no cookie credentials in this phase).
    allow_headers=["Content-Type", "Authorization"],
)


from shared.paths import BE_ROOT
BASE_DIR = BE_ROOT

DATA_DIR_DEFAULT = str(BASE_DIR)
DATA_DIR = Path(os.environ.get("DATA_DIR", DATA_DIR_DEFAULT))
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", str(DATA_DIR / "videos")))
INPUT_DOCS_DIR = Path(os.environ.get("INPUT_DOCS_DIR", str(DATA_DIR / "input_docs")))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", str(DATA_DIR / "index")))
MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", str(DATA_DIR / "memory")))

VIDEOS_DIR = str(VIDEO_DIR)
INPUT_DIR = str(INPUT_DOCS_DIR)
INDEX_META_JSON_PATH = INDEX_DIR / "index.json"
INDEX_FAISS_PATH = INDEX_DIR / "index.faiss"

# Thư mục lưu các artefact trí nhớ tầng cao (mindmap, summary, memory tree, ...)
MINDMAPS_PATH = MEMORY_DIR / 'mindmaps.json'
SUMMARIES_PATH = MEMORY_DIR / 'summaries.json'
SOURCE_REGISTRY_PATH = INDEX_DIR / "source_registry.json"

os.makedirs(INPUT_DIR, exist_ok=True)
os.makedirs(VIDEOS_DIR, exist_ok=True)
os.makedirs(MEMORY_DIR, exist_ok=True)
os.makedirs(INDEX_DIR, exist_ok=True)

# LangGraph ingest pipeline (Bước 2) sẽ được khởi tạo sau khi các helper (vd: _update_source_status) sẵn sàng.
INGEST_GRAPH = None
QUERY_GRAPH = None
SUMMARY_GRAPH = None
MINDMAP_GRAPH = None
QUERY_GRAPH_BUILD_ERROR: Optional[str] = None

_jobs_update_job = None
_jobs_create_job = None
_jobs_get_job = None
try:
    from app.domains.jobs.jobs_store import (
        update_job as _jobs_update_job,
        create_job as _jobs_create_job,
        get_job as _jobs_get_job,
    )
except Exception:
    pass


def _reconcile_jobs_safe():
    """Phase 5: queue-aware orphan reconciliation. QUEUE_ENABLED=false -> mark all
    active interrupted (today's behaviour); true -> only mark jobs absent from RQ
    registries (never kills a live worker job); RQ down -> touch nothing."""
    try:
        from app.jobs.queue import reconcile_interrupted
        reconcile_interrupted()
    except Exception:
        # fall back to the legacy single-process behaviour if the queue module fails
        try:
            if _jobs_mark_interrupted is not None:
                _jobs_mark_interrupted()
        except Exception:
            pass


def _handle_sigterm(*_args):
    # best-effort: mark orphaned jobs interrupted (queue-aware) để tránh trạng thái mồ côi
    try:
        _reconcile_jobs_safe()
    finally:
        sys.exit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)

# Lightweight in-memory query cache (phù hợp offline, giảm gọi Ollama)
QUERY_CACHE_MAX_SIZE = int(os.environ.get("QUERY_CACHE_MAX_SIZE", "200"))
QUERY_CACHE_TTL_SEC = int(os.environ.get("CACHE_TTL_SEC", os.environ.get("QUERY_CACHE_TTL_SEC", "1800")))
_query_cache: "OrderedDict[str, dict]" = OrderedDict()
_query_cache_lock = threading.Lock()

# File lock để chặn rebuild đồng thời giữa nhiều gunicorn workers
REBUILD_LOCK_PATH = INDEX_DIR / ".rebuild.lock"

# In-memory async job manager (giữ offline, nhẹ)
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()
JOB_TTL_MINUTES = int(os.environ.get("JOB_TTL_MINUTES", "30"))

# Migrate legacy in-memory jobs dict sang SQLite (idempotent, best-effort)
try:
    if _jobs_migrate_from_dict is not None:
        _jobs_migrate_from_dict(jobs, job_type="rebuild")
except Exception:
    pass

def _cleanup_old_jobs() -> None:
    # Lazily cleanup on request (per-process, per gunicorn worker)
    if JOB_TTL_MINUTES <= 0:
        return
    cutoff = time.time() - (JOB_TTL_MINUTES * 60)
    with jobs_lock:
        expired = [jid for jid, j in jobs.items() if isinstance(j.get("created_at"), (int, float)) and j["created_at"] < cutoff]
        for jid in expired:
            jobs.pop(jid, None)

# Query async job store (separate from rebuild jobs)
query_jobs: Dict[str, Dict[str, Any]] = {}
query_jobs_lock = threading.Lock()
QUERY_JOB_TTL_MINUTES = int(os.environ.get("QUERY_JOB_TTL_MINUTES", "30"))
QUERY_JOB_TIMEOUT_SEC = int(os.environ.get("QUERY_JOB_TIMEOUT_SEC", str(5 * 60)))
QUERY_MAX_CONCURRENT = int(os.environ.get("QUERY_MAX_CONCURRENT", "4"))
_query_semaphore = threading.Semaphore(max(1, QUERY_MAX_CONCURRENT))

# Migrate legacy in-memory query_jobs dict sang SQLite (idempotent)
try:
    if _jobs_migrate_from_dict is not None:
        _jobs_migrate_from_dict(query_jobs, job_type="query")
except Exception:
    pass

def _cleanup_old_query_jobs() -> None:
    if QUERY_JOB_TTL_MINUTES <= 0:
        return
    cutoff = time.time() - (QUERY_JOB_TTL_MINUTES * 60)
    with query_jobs_lock:
        expired = [
            jid for jid, j in query_jobs.items()
            if isinstance(j.get("created_at"), (int, float)) and j["created_at"] < cutoff
        ]
        for jid in expired:
            query_jobs.pop(jid, None)

def _make_query_cache_key(q: str, selected_sources: list, use_memory_tree: bool, filters: dict | None = None) -> str:
    # Normalize list để key ổn định theo thứ tự chọn
    # LƯU Ý: llm_cache.semantic_lookup/store PARSE JSON này (keys: q/sources/
    # use_memory_tree/category/language) — đổi format ở đây phải xem lại
    # app/domains/cache/llm_cache.py::_parse_cache_key (lệch = miss im lặng).
    sources_norm = selected_sources or []
    sources_norm = [str(s) for s in sources_norm if s is not None]
    sources_norm = sorted(sources_norm)
    f = filters or {}
    return json.dumps(
        {
            "q": (q or "").strip(),
            "sources": sources_norm,
            "use_memory_tree": bool(use_memory_tree),
            "category": (f.get("category") or None),
            "language": (f.get("language") or None),
        },
        ensure_ascii=False,
        sort_keys=True
    )

def _get_cached_query(cache_key: str) -> Optional[dict]:
    now = time.time()
    with _query_cache_lock:
        entry = _query_cache.get(cache_key)
        if entry:
            if now - entry["ts"] > QUERY_CACHE_TTL_SEC:
                _query_cache.pop(cache_key, None)
            else:
                _query_cache.move_to_end(cache_key)
                return entry["value"]
    # L2: semantic cache Redis (cross-worker, exact + cosine) — fail-open, None nếu miss/Redis chết.
    return llm_cache.semantic_lookup(cache_key)

def _set_cached_query(cache_key: str, value: dict) -> None:
    # INVARIANT: answer rỗng không được vào L1 lẫn L2 — hit sau sẽ trả "Không có phản hồi."
    _p = value.get("payload") if isinstance(value, dict) else None
    if not (isinstance(_p, dict) and str(_p.get("answer") or "").strip()):
        llm_cache.METRICS["write_skipped_empty"] += 1
        return
    with _query_cache_lock:
        if cache_key in _query_cache:
            _query_cache.move_to_end(cache_key)
        _query_cache[cache_key] = {"ts": time.time(), "value": value}
        while len(_query_cache) > QUERY_CACHE_MAX_SIZE:
            _query_cache.popitem(last=False)
    # L2: ghi semantic cache (chỉ khi classify_risk cho phép) — fire-and-forget, fail-open.
    llm_cache.semantic_store(cache_key, value)


# ============================================================================
# Phase 3 — Single-flight / request coalescing (DR-3 D3)
# ----------------------------------------------------------------------------
# A storm of identical/equivalent questions with a COLD cache would each spawn a
# full RAG/LLM job (Phase 2 caps concurrency but does NOT coalesce). Single-flight
# elects ONE leader per (bucket + no-diacritics query) via a Redis SETNX lock;
# followers wait briefly and return the leader's cached answer. OPTIMIZATION ONLY:
# any Redis/lock miss/timeout/empty result → fail open to the normal path. It can
# never block or empty an answer. Runs in the BACKEND process, at job submit.
# ============================================================================
from app.clients import redis_client as _redis_client  # noqa: E402

_SF_RELEASE_LUA = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)

_SF_METRICS: Dict[str, int] = {
    "leader": 0, "follower": 0, "follower_hit": 0, "timeout": 0,
    "fail_open": 0, "bypass_unsafe": 0, "bypass_followup": 0, "bypass_disabled": 0,
    "redis_error_fail_open": 0, "dup_avoided": 0, "release_ok": 0, "release_fail": 0,
}


def _sf_enabled() -> bool:
    return (os.getenv("SINGLE_FLIGHT_ENABLED", "true") or "").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _sf_num(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def _sf_log(event: str, **kv: object) -> None:
    parts = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"singleflight {event} {parts}".rstrip(), flush=True)


def _sf_nonempty(cached: Optional[dict]) -> bool:
    if not isinstance(cached, dict):
        return False
    p = cached.get("payload")
    return isinstance(p, dict) and bool(str(p.get("answer") or "").strip())


def _finalize_from_cache(jid: str, session_id: str, question: str, cached: dict,
                         *, source_ids: Optional[list] = None,
                         source_context_hash: Optional[str] = None,
                         user_id: Optional[str] = None, enforce_owner: bool = False) -> None:
    """Mark a follower job done using the leader's cached result. Atomic done+result
    (mirrors _finalize_query_job) — never sets done without the payload."""
    payload = cached.get("payload") if isinstance(cached, dict) else None
    payload = dict(payload) if isinstance(payload, dict) else {}
    status = int(cached.get("status") or 200) if isinstance(cached, dict) else 200
    result_obj = {"payload": payload, "status": status}
    with query_jobs_lock:
        if jid in query_jobs:
            query_jobs[jid]["status"] = "done"
            query_jobs[jid]["result"] = result_obj
    if _jobs_update_job:
        try:
            _jobs_update_job(jid, status="done", progress=100,
                             current_node="SingleFlightFollower", result=result_obj)
        except Exception:
            pass
    try:
        if payload.get("answer"):
            from app.domains.jobs.sessions_store import append_messages as _ss_append
            _ss_append(session_id, [
                {"role": "user", "content": question},
                {"role": "assistant", "content": str(payload.get("answer"))},
            ])
    except Exception:
        pass

    # Conversation Context Layer: record the follower's turn in its own session (flag-gated).
    try:
        if payload.get("answer"):
            cited = [c.get("chunk_id") for c in (payload.get("chunks") or []) if isinstance(c, dict) and c.get("chunk_id")]
            _save_conversation_turns(
                session_id, question, str(payload.get("answer")),
                source_ids=source_ids, source_context_hash=source_context_hash, cited_chunk_ids=cited or None,
                user_id=user_id, enforce_owner=enforce_owner,
            )
    except Exception:
        pass


def _single_flight_try(jid: str, question: str, sources: list, use_mem: bool,
                       category: Optional[str], language: Optional[str],
                       session_id: str, *, user_id: Optional[str] = None,
                       enforce_owner: bool = False) -> dict:
    """Decide leader/follower/bypass for one query job. Returns:
      {"served": True}                    -> follower finalized from cache (skip graph)
      {"served": False, "lock": (k, tok)} -> leader (run graph, release lock after)
      {"served": False, "lock": None}     -> bypass or fail-open (run graph, no lock)
    Fail-open on ANY Redis/lock issue — never raises into the answer path.
    """
    if not _sf_enabled():
        _SF_METRICS["bypass_disabled"] += 1
        _sf_log("singleflight_disabled")
        return {"served": False, "lock": None}

    # Unsafe/private → never coalesce (respect cache risk policy).
    cacheable, risk = llm_cache.classify_risk(question)
    if not cacheable:
        _SF_METRICS["bypass_unsafe"] += 1
        _sf_log("singleflight_bypass_unsafe", risk=risk)
        return {"served": False, "lock": None}

    # Follow-up (context-specific) → don't coalesce (mirror cache_lookup behaviour).
    try:
        history = _get_session_history_safe(session_id, 8)
    except Exception:
        history = []
    if history and not llm_cache.is_standalone_question(question):
        _SF_METRICS["bypass_followup"] += 1
        return {"served": False, "lock": None}

    r = _redis_client.get_redis()
    if r is None:
        _SF_METRICS["redis_error_fail_open"] += 1
        _sf_log("singleflight_redis_error_fail_open", reason="unavailable")
        return {"served": False, "lock": None}

    sf_key = llm_cache.single_flight_key(question, sources, language, category, use_mem)
    if not sf_key:
        _SF_METRICS["bypass_unsafe"] += 1
        return {"served": False, "lock": None}
    try:
        _sf_sch = llm_cache.source_context_hash(sources, language, category, use_mem)
    except Exception:
        _sf_sch = None
    cache_key = _make_query_cache_key(question, sources, use_mem,
                                      {"category": category, "language": language})

    # Warm cache already? Serve immediately, no lock needed.
    cached = _get_cached_query(cache_key)
    if _sf_nonempty(cached):
        _SF_METRICS["follower_hit"] += 1
        _SF_METRICS["dup_avoided"] += 1
        _sf_log("singleflight_follower_cache_hit", kind="warm")
        _finalize_from_cache(jid, session_id, question, cached, source_ids=sources, source_context_hash=_sf_sch, user_id=user_id, enforce_owner=enforce_owner)
        return {"served": True}

    token = uuid.uuid4().hex
    try:
        got = bool(r.set(sf_key, token, nx=True, ex=int(_sf_num("SINGLE_FLIGHT_LOCK_TTL_SECONDS", 180))))
    except Exception as exc:
        _redis_client.mark_unavailable()
        _SF_METRICS["redis_error_fail_open"] += 1
        _sf_log("singleflight_redis_error_fail_open", err=str(exc)[:80])
        return {"served": False, "lock": None}

    if got:
        _SF_METRICS["leader"] += 1
        _sf_log("singleflight_leader_acquired", key=sf_key[-24:])
        return {"served": False, "lock": (sf_key, token)}

    # Follower: poll for the leader's cached answer.
    _SF_METRICS["follower"] += 1
    _sf_log("singleflight_follower_waiting", key=sf_key[-24:])
    poll = max(0.05, _sf_num("SINGLE_FLIGHT_POLL_INTERVAL_SECONDS", 0.5))
    deadline = time.time() + _sf_num("SINGLE_FLIGHT_WAIT_SECONDS", 120)
    while time.time() < deadline:
        time.sleep(poll)
        cached = _get_cached_query(cache_key)
        if _sf_nonempty(cached):
            _SF_METRICS["follower_hit"] += 1
            _SF_METRICS["dup_avoided"] += 1
            _sf_log("singleflight_follower_cache_hit", kind="leader")
            _finalize_from_cache(jid, session_id, question, cached, source_ids=sources, source_context_hash=_sf_sch, user_id=user_id, enforce_owner=enforce_owner)
            return {"served": True}
        # Leader vanished (errored/released) without a cached answer → fail open early,
        # but re-check cache once to close the write-then-release race.
        try:
            still_locked = bool(r.exists(sf_key))
        except Exception:
            _redis_client.mark_unavailable()
            break
        if not still_locked:
            cached = _get_cached_query(cache_key)
            if _sf_nonempty(cached):
                _SF_METRICS["follower_hit"] += 1
                _SF_METRICS["dup_avoided"] += 1
                _sf_log("singleflight_follower_cache_hit", kind="leader_race")
                _finalize_from_cache(jid, session_id, question, cached, source_ids=sources, source_context_hash=_sf_sch, user_id=user_id, enforce_owner=enforce_owner)
                return {"served": True}
            break

    _SF_METRICS["timeout"] += 1
    _SF_METRICS["fail_open"] += 1
    _sf_log("singleflight_follower_timeout_fail_open", key=sf_key[-24:])
    return {"served": False, "lock": None}


def _single_flight_release(key: str, token: str) -> None:
    """Release the leader lock (token compare-delete). Fail-open, never raises."""
    r = _redis_client.get_redis()
    if r is None:
        return
    try:
        r.eval(_SF_RELEASE_LUA, 1, key, token)
        _SF_METRICS["release_ok"] += 1
        _sf_log("singleflight_lock_release_success", key=key[-24:])
    except Exception as exc:
        _SF_METRICS["release_fail"] += 1
        _sf_log("singleflight_lock_release_failed", err=str(exc)[:80])


# ============================================================================
# Phase 4 — Ingress overload protection (DR-3: rate limit + readiness + shed)
# ----------------------------------------------------------------------------
# Redis token-bucket rate limit on /query, a /ready endpoint (503 when a load
# balancer should back off), and structured admission-full responses. All
# OPTIONAL and fail-open: rate limit is OFF by default and Redis errors never
# block traffic in dev. Never touches the answer path or single-flight/gateway.
# ============================================================================

# Token bucket: refill `rate` tokens/sec up to `cap`; spend 1 per request.
# KEYS[1]=bucket, ARGV=rate, cap, now, ttl. Returns {allowed(0/1), retry_after_s}.
_RL_LUA = """
local rate=tonumber(ARGV[1]); local cap=tonumber(ARGV[2])
local now=tonumber(ARGV[3]); local ttl=tonumber(ARGV[4])
local d=redis.call('HMGET',KEYS[1],'tokens','ts')
local tokens=tonumber(d[1]); local ts=tonumber(d[2])
if tokens==nil then tokens=cap; ts=now end
tokens=math.min(cap, tokens + math.max(0, now-ts)*rate)
local allowed=0; local retry=0
if tokens>=1 then tokens=tokens-1; allowed=1
else retry=math.ceil((1-tokens)/rate) end
redis.call('HMSET',KEYS[1],'tokens',tokens,'ts',now)
redis.call('EXPIRE',KEYS[1],ttl)
return {allowed, retry}
"""

_OVERLOAD_METRICS: Dict[str, int] = {
    "rate_limit_allowed": 0, "rate_limit_rejected": 0, "rate_limit_redis_error": 0,
    "admission_rejected": 0,
}


def _ovl_log(event: str, **kv: object) -> None:
    parts = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"overload {event} {parts}".rstrip(), flush=True)


def _ovl_bool(name: str, default: bool) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    if not v:
        return default
    return v not in ("0", "false", "no", "off")


def _ovl_num(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def _client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rl_scope_id(session_id: str) -> str:
    scope = (os.getenv("RATE_LIMIT_SCOPE", "ip") or "ip").strip().lower()
    if scope in ("session", "user") and session_id:
        return f"sess:{session_id}"
    return f"ip:{_client_ip()}"


def _rate_limit_check(scope_id: str) -> tuple[bool, int]:
    """(allowed, retry_after_seconds). Fail-open unless RATE_LIMIT_REQUIRE_REDIS."""
    if not _ovl_bool("RATE_LIMIT_ENABLED", False):
        return True, 0
    require_redis = _ovl_bool("RATE_LIMIT_REQUIRE_REDIS", False)
    r = _redis_client.get_redis()
    if r is None:
        _OVERLOAD_METRICS["rate_limit_redis_error"] += 1
        if require_redis:
            return False, int(_ovl_num("RATE_LIMIT_WINDOW_SECONDS", 60))
        _ovl_log("rate_limit_redis_error_fail_open", reason="unavailable")
        return True, 0
    rate = _ovl_num("RATE_LIMIT_RPS", 1)
    cap = _ovl_num("RATE_LIMIT_BURST", 5)
    ttl = int(_ovl_num("RATE_LIMIT_WINDOW_SECONDS", 60))
    key = f"rl:{llm_cache._ENV}:{scope_id}"
    try:
        allowed, retry = r.eval(_RL_LUA, 1, key, rate, cap, time.time(), ttl)
    except Exception as exc:
        _redis_client.mark_unavailable()
        _OVERLOAD_METRICS["rate_limit_redis_error"] += 1
        if require_redis:
            return False, ttl
        _ovl_log("rate_limit_redis_error_fail_open", err=str(exc)[:60])
        return True, 0
    if int(allowed) == 1:
        _OVERLOAD_METRICS["rate_limit_allowed"] += 1
        _ovl_log("rate_limit_allowed", scope=scope_id[:48])
        return True, 0
    _OVERLOAD_METRICS["rate_limit_rejected"] += 1
    _ovl_log("rate_limit_rejected", scope=scope_id[:48], retry=int(retry))
    return False, int(retry)


def _rate_limited_response(retry_after: int):
    _ovl_log("overload_response_sent", kind="rate_limit", retry=retry_after)
    resp = jsonify({
        "error": "rate_limited",
        "message": "Too many requests. Please retry later.",
        "retry_after_seconds": int(retry_after),
    })
    resp.status_code = 429
    resp.headers["Retry-After"] = str(int(retry_after))
    return resp


def _admission_rejected_response():
    _OVERLOAD_METRICS["admission_rejected"] += 1
    retry = int(_ovl_num("ADMISSION_RETRY_AFTER_SECONDS", 5))
    _ovl_log("admission_rejected")
    _ovl_log("overload_response_sent", kind="admission", retry=retry)
    resp = jsonify({
        "error": "admission_rejected",
        "message": "Server is at capacity, please retry shortly.",
        "retry_after_seconds": retry,
    })
    resp.status_code = 429
    resp.headers["Retry-After"] = str(retry)
    return resp


def _admission_available() -> Optional[int]:
    """Best-effort free permits on this worker's admission semaphore (private attr)."""
    try:
        return int(getattr(_query_semaphore, "_value"))
    except Exception:
        return None


def _queue_stats_safe() -> dict:
    """Phase 5: RQ queue depth for /stats and /ready. Never raises."""
    try:
        from app.jobs.queue import queue_stats
        return queue_stats()
    except Exception as exc:  # noqa: BLE001
        return {"enabled": False, "error": str(exc)[:80]}


def _queue_depth_max() -> int:
    try:
        return int(os.getenv("QUEUE_DEPTH_MAX", "20"))
    except ValueError:
        return 20


def _readiness() -> tuple[bool, dict]:
    """Is this worker ready to accept meaningful traffic? Distinct from liveness."""
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    r = _redis_client.get_redis()
    if r is None:
        redis_state = "down" if redis_url else "disabled"
    else:
        try:
            r.ping()
            redis_state = "ok"
        except Exception:
            _redis_client.mark_unavailable()
            redis_state = "down"
    avail = _admission_available()
    reasons = []
    if QUERY_GRAPH is None:
        reasons.append("graph_not_ready")
    if isinstance(avail, int) and avail <= 0:
        reasons.append("admission_saturated")
    if redis_state == "down" and _ovl_bool("RATE_LIMIT_REQUIRE_REDIS", False):
        reasons.append("redis_required_down")
    # Phase 5: shed when the RQ queue is backed up (only when queue mode is on).
    # Does NOT gate /query — the interactive path stays available regardless of backlog.
    qs = _queue_stats_safe()
    queue_depth = qs.get("queued_count")
    if qs.get("enabled") and isinstance(queue_depth, int) and queue_depth > _queue_depth_max():
        reasons.append("queue_full")
    ready = not reasons
    detail = {
        "status": "ready" if ready else "not_ready",
        "redis": redis_state,
        "llm_gateway": "unknown",  # future work: cheap gRPC health probe
        "query_graph_ready": QUERY_GRAPH is not None,
        "admission_available": avail if avail is not None else "unknown",
        "queue_enabled": bool(qs.get("enabled")),
        "queue_depth": queue_depth,
    }
    if reasons:
        detail["reason"] = ",".join(reasons)
    return ready, detail


@app.get('/ready')
def ready():
    ok, detail = _readiness()
    if ok:
        _ovl_log("readiness_check_ok")
        return jsonify(detail), 200
    _ovl_log("readiness_check_failed", reason=detail.get("reason"))
    return jsonify(detail), 503


@app.get('/')
def home():
    return 'MemvidX API is running.'

@app.get('/health')
def health():
    payload: Dict[str, Any] = {
        "status": "ok",
        "mode": "ci" if os.environ.get("SKIP_MODEL_LOAD") == "1" else "normal",
        "query_graph_ready": QUERY_GRAPH is not None,
        "ingest_graph_ready": INGEST_GRAPH is not None,
    }
    err = globals().get("QUERY_GRAPH_BUILD_ERROR")
    if err:
        payload["query_graph_error"] = err[:800]
    return jsonify(payload), 200


# -------------------------
# 🔐 Auth MVP (Bearer token) — register / login / logout / me
# Additive; existing app APIs stay OPEN in this phase (no @require_auth applied).
# -------------------------
@app.post('/auth/register')
def auth_register():
    from app.domains.auth import service as _auth
    from app.domains.auth import tokens as _tokens
    from app.domains.auth import users_store as _users
    data = request.json or {}
    email = data.get("email") or ""
    password = data.get("password") or ""
    display_name = data.get("display_name")
    if not _auth.valid_email(email):
        return jsonify({"error": "invalid_email"}), 400
    if not _auth.valid_password(password):
        return jsonify({"error": "weak_password", "message": "Mật khẩu cần ít nhất 8 ký tự."}), 400
    try:
        user = _users.create_user(email, password, display_name)
    except _users.EmailExistsError:
        return jsonify({"error": "email_exists"}), 409
    token = _tokens.make_token(user)
    return jsonify({"token": token, "user": _auth.public_user(user)}), 201


@app.post('/auth/login')
def auth_login():
    from app.domains.auth import service as _auth
    from app.domains.auth import tokens as _tokens
    from app.domains.auth import users_store as _users
    data = request.json or {}
    email = data.get("email") or ""
    password = data.get("password") or ""
    # Reuse the Phase-4 token-bucket limiter (off by default; fail-open).
    allowed, retry_after = _rate_limit_check(f"login:{_client_ip()}")
    if not allowed:
        return _rate_limited_response(retry_after)
    user = _users.verify_password(email, password)
    if user is None:
        # Generic error — no user enumeration.
        return jsonify({"error": "invalid_credentials"}), 401
    token = _tokens.make_token(user)
    return jsonify({"token": token, "user": _auth.public_user(user)}), 200


@app.post('/auth/logout')
def auth_logout():
    # Stateless: the client drops the token. (No token_version bump = not logout-all.)
    return jsonify({"ok": True}), 200


@app.get('/auth/me')
def auth_me():
    from app.domains.auth import service as _auth
    user = _auth.current_user_from_request()
    if user is None:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"user": _auth.public_user(user)}), 200


@app.get('/stats')
def stats():
    # index meta: key là chunk_id (số dạng string). Các key không phải số được coi là metadata nội bộ.
    num_chunks = 0
    video_stems: set[str] = set()

    try:
        if INDEX_META_JSON_PATH.exists():
            with open(INDEX_META_JSON_PATH, encoding="utf-8") as f:
                meta = json.load(f)
        else:
            meta = {}
    except Exception as exc:
        print(f"[STATS] Failed to read index meta: {exc}")
        meta = {}

    for k, m in (meta or {}).items():
        if not isinstance(k, str) or not k.isdigit():
            continue
        num_chunks += 1
        video_raw = (m.get("video") or "").strip() if isinstance(m, dict) else ""
        if video_raw:
            stem = _normalize_video_stem(video_raw)
            if stem:
                video_stems.add(stem)

    try:
        num_videos = len(list(Path(VIDEOS_DIR).glob("*.mp4")))
    except Exception:
        num_videos = 0

    # num_documents ~ số lượng video stems có trong index (tương ứng mỗi source)
    num_documents = len(video_stems)

    return jsonify({
        "num_documents": num_documents,
        "num_chunks": num_chunks,
        "num_videos": num_videos,
        # Counter per-worker (gunicorn); aggregate thật xem redis-cli INFO stats.
        "cache": llm_cache.stats(),
        # Phase 3 single-flight counters (per-worker). duplicate_llm_calls_avoided ~= dup_avoided.
        "single_flight": {**_SF_METRICS, "enabled": _sf_enabled()},
        # Phase 4 overload/circuit view (per-worker). LLM busy/timeout lives in gateway logs (future).
        "overload": {
            **_OVERLOAD_METRICS,
            "rate_limit_enabled": _ovl_bool("RATE_LIMIT_ENABLED", False),
            "admission_available": _admission_available(),
            "admission_capacity": QUERY_MAX_CONCURRENT,
        },
        # Phase 5 RQ queue depth (fail-open; zeros when QUEUE_ENABLED=false or Redis down).
        "queue": _queue_stats_safe(),
    }), 200


@app.post('/rebuild-index')
def rebuild_index_from_video():
    """
    Rebuild FAISS index ONLY from QR videos asynchronously (video-as-source-of-truth).
    Returns immediately with a job_id for progress tracking.
    """
    _cleanup_old_jobs()

    # Only one rebuild at a time across gunicorn workers (file lock)
    try:
        lock_fd = os.open(str(REBUILD_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(os.getpid()).encode("utf-8"))
        os.close(lock_fd)
    except FileExistsError:
        return jsonify({"error": "Rebuild index is already running"}), 409
    except Exception as exc:
        return jsonify({"error": f"Cannot create rebuild lock: {str(exc)}"}), 500

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "progress": 0,
            "num_videos": 0,
            "num_chunks": 0,
            "error": None,
            "created_at": time.time(),
        }
    # Phase 5 Step 4: also mirror status into the shared jobs.sqlite so an RQ worker in a
    # separate process is visible to /rebuild-status (in-mem `jobs` dict is per-process).
    if _jobs_create_job is not None:
        try:
            _jobs_create_job(job_id, job_type="rebuild", status="pending", progress=0, current_node="Queued", user_id=_current_user_id())
        except Exception:
            pass

    from app.jobs.queue import enqueue_job
    try:
        res = enqueue_job(run_rebuild_index_job, args=(job_id,), queue="rebuild", job_id=job_id)
    except Exception as exc:
        # enqueue_job already falls back to a thread; a raise here is unexpected -> cleanup.
        try:
            if REBUILD_LOCK_PATH.exists():
                REBUILD_LOCK_PATH.unlink()
        except Exception:
            pass
        with jobs_lock:
            jobs.pop(job_id, None)
        return jsonify({"error": f"Failed to start rebuild job: {str(exc)}"}), 500
    _event = {"rq": "rebuild_enqueue_rq", "thread": "rebuild_enqueue_thread",
              "thread_fallback": "rebuild_queue_fallback_thread"}.get(res.get("mode"),
                                                                      f"rebuild_enqueue_{res.get('mode')}")
    print(f"{_event} job_id={job_id}", flush=True)
    return jsonify({"status": "started", "job_id": job_id}), 202


def run_rebuild_index_job(job_id: str) -> None:
    """Rebuild FAISS index from QR videos. Runs in a daemon thread (QUEUE_ENABLED=false)
    OR an RQ worker process (QUEUE_ENABLED=true) — identical behaviour, no Flask request
    context. Enqueued by dotted path `app.main.run_rebuild_index_job`. Status is mirrored
    into the shared jobs.sqlite (worker-visible) AND the in-mem `jobs` dict (legacy/same
    process). Releases REBUILD_LOCK_PATH in finally (path on the shared index volume)."""
    print(f"rebuild_job_running job_id={job_id}", flush=True)

    def _set_store(**kw):
        if _jobs_update_job is not None:
            try:
                _jobs_update_job(job_id, **kw)
            except Exception:
                pass

    def _set_dict(**kw):
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id].update(kw)

    try:
        _set_dict(status="running", progress=0)
        _set_store(status="running", progress=0, current_node="Rebuild")

        _counts = {"num_videos": 0, "num_chunks": 0}

        def progress_cb(progress: int, extra: Optional[Dict[str, Any]] = None) -> None:
            upd: Dict[str, Any] = {"progress": progress}
            if extra:
                if extra.get("num_videos") is not None:
                    _counts["num_videos"] = int(extra["num_videos"])
                if extra.get("num_chunks") is not None:
                    _counts["num_chunks"] = int(extra["num_chunks"])
            _set_dict(**upd, **_counts)
            _set_store(progress=progress, result=dict(_counts))

        from app.scripts.rebuild_index_from_video import rebuild_faiss_index_from_videos
        result = rebuild_faiss_index_from_videos(progress_cb=progress_cb)
        num_chunks = int(result.get("num_chunks") or 0)
        num_videos = int(result.get("num_videos") or _counts["num_videos"] or 0)
        _set_dict(status="done", progress=100, num_chunks=num_chunks, num_videos=num_videos)
        _set_store(status="done", progress=100,
                   result={"num_chunks": num_chunks, "num_videos": num_videos})
        print(f"rebuild_job_done job_id={job_id} num_videos={num_videos} num_chunks={num_chunks}", flush=True)
    except Exception as exc:
        _set_dict(status="error", error=str(exc))
        _set_store(status="error", error_text=_job_error_text(exc))
        print(f"rebuild_job_failed job_id={job_id} err={str(exc)[:80]}", flush=True)
    finally:
        try:
            if REBUILD_LOCK_PATH.exists():
                REBUILD_LOCK_PATH.unlink()
        except Exception:
            pass


@app.get('/rebuild-status/<job_id>')
def rebuild_status(job_id: str):
    _cleanup_old_jobs()
    # Prefer the shared jobs.sqlite (worker-visible); fall back to the in-mem dict (legacy).
    if _jobs_get_job is not None:
        try:
            j = _jobs_get_job(job_id)
        except Exception:
            j = None
        if j is not None and j.get("job_type") in ("rebuild", None):
            res = j.get("result") if isinstance(j.get("result"), dict) else {}
            return jsonify({
                "status": j.get("status"),
                "progress": j.get("progress"),
                # Default 0 before the first progress/result write (preserve legacy contract:
                # the in-mem dict returned 0/0 on a freshly started job, not null).
                "num_chunks": res.get("num_chunks") or 0,
                "num_videos": res.get("num_videos") or 0,
                "error": j.get("error"),
            }), 200
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        # Return only required fields (but include error when exists)
        return jsonify({
            "status": job.get("status"),
            "progress": job.get("progress"),
            "num_chunks": job.get("num_chunks"),
            "num_videos": job.get("num_videos"),
            "error": job.get("error"),
        }), 200


# -------------------------
# 📋 Source Registry (tracking upload status)
# -------------------------
def _load_source_registry() -> Dict[str, Dict]:
    """Load source registry với file locking để tránh race condition."""
    if not SOURCE_REGISTRY_PATH.exists():
        return {}
    try:
        # Try file locking (works on Unix, fallback on Windows)
        with open(SOURCE_REGISTRY_PATH, 'r', encoding='utf-8') as f:
            if HAS_FCNTL:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock for read
                    data = json.load(f)
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except (AttributeError, OSError):
                    # Fallback: just read
                    data = json.load(f)
            else:
                # Windows: no locking, just read
                data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"⚠️ Không thể đọc source_registry.json: {exc}")
        return {}


def _save_source_registry(registry: Dict[str, Dict]) -> None:
    """Save source registry với file locking."""
    try:
        tmp_path = SOURCE_REGISTRY_PATH.with_suffix('.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            if HAS_FCNTL:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock for write
                    json.dump(registry, f, ensure_ascii=False, indent=2)
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except (AttributeError, OSError):
                    # Fallback: just write
                    json.dump(registry, f, ensure_ascii=False, indent=2)
            else:
                # Windows: no locking, just write
                json.dump(registry, f, ensure_ascii=False, indent=2)
        tmp_path.replace(SOURCE_REGISTRY_PATH)
    except Exception as exc:
        print(f"⚠️ Không thể lưu source_registry.json: {exc}")


# -------------------------
# 🔐 Auth Hardening Phase A — ownership helpers (UNENFORCED this phase).
# Flag default OFF; helpers are wired to storage (registry/jobs) but no route
# changes its auth/scoping behavior yet. See the auth-hardening plan.
# -------------------------
def _auth_protect_enabled() -> bool:
    return (os.getenv("AUTH_PROTECT_APP_APIS", "false") or "").strip().lower() in ("1", "true", "yes", "on")


def _current_user_id() -> Optional[str]:
    """Resolve the caller's user_id from the Bearer token, or None. Fail-safe:
    any error → None (never raises into a route)."""
    try:
        from app.domains.auth import service as _auth
        user = _auth.current_user_from_request()
        return user.get("user_id") if user else None
    except Exception:
        return None


def _require_app_user():
    """Auth gate for protected app routes. Returns (user_id, error_response).

    Flag OFF → (None, None): allow, open/backward-compatible.
    Flag ON  → (user_id, None) for a valid token, else (None, 401 response).
    The ownership resolution here is deliberately OUTSIDE any fail-open block —
    an auth failure denies (fail-closed), it never downgrades to open."""
    if not _auth_protect_enabled():
        return None, None
    uid = _current_user_id()
    if not uid:
        return None, (jsonify({"error": "unauthorized"}), 401)
    return uid, None


def owned_stems(user_id: Optional[str]) -> set:
    """Canonical source stems owned by `user_id`, from the registry.

    When AUTH_PROTECT_APP_APIS is OFF, legacy/None-owner rows are included (today's
    open behavior). When ON, only rows whose user_id matches are returned (and, for
    a None user_id, nothing) — the fail-closed base the later phases enforce."""
    try:
        registry = _load_source_registry()
    except Exception:
        return set()
    protect = _auth_protect_enabled()
    out: set = set()
    for row in registry.values():
        if not isinstance(row, dict):
            continue
        stem = row.get("source_stem")
        if not stem:
            continue
        owner = row.get("user_id")
        if protect:
            if user_id is not None and owner == user_id:
                out.add(stem)
        else:
            # open mode: everything visible (owner filter is a no-op)
            out.add(stem)
    return out


def user_data_root(user_id: Optional[str]) -> "Path":
    """Physical-ready seam. Returns the CURRENT global data root today; a future
    physical-partition phase swaps this to DATA_DIR/users/<user_id>/ without
    touching call sites."""
    return Path(DATA_DIR_DEFAULT)


# Phase C — source/query ownership.
# Sentinel stem that matches NO chunk: used when an enforced query resolves to zero
# owned sources, so retrieval returns [] instead of falling back to the global corpus.
_NO_OWNED_SOURCES = ["\x00__no_owned_sources__"]


def _source_owner_ok(stem_or_id: str, user_id: Optional[str]) -> bool:
    """True when the source (matched by source_id key OR canonical stem) is owned by
    user_id. Registry is the authoritative owner map; never trusts client-supplied
    user_id."""
    try:
        registry = _load_source_registry()
    except Exception:
        return False
    norm = _normalize_video_stem(stem_or_id)
    for sid, row in registry.items():
        if not isinstance(row, dict):
            continue
        row_stem = _normalize_video_stem(row.get("source_stem") or row.get("filename") or "")
        if sid == stem_or_id or (norm and row_stem == norm):
            return row.get("user_id") == user_id
    return False


def _resolve_owned_query_sources(raw_sources, user_id: Optional[str]):
    """Return (resolved_sources, error_response|None).

    Flag OFF → (raw, None): today's behavior (empty means global).
    Flag ON  → owner-scoped:
      * raw empty   → all owned stems (or the NO-OWNED sentinel → retrieval returns [])
      * raw present → every requested stem must be owned, else 403; returns the
        canonicalized owned subset. Never falls back to the global corpus."""
    if not _auth_protect_enabled():
        return (list(raw_sources) if raw_sources else []), None
    owned = owned_stems(user_id)  # set of canonical stems
    raw = [s for s in (raw_sources or []) if s]
    if not raw:
        return (sorted(owned) if owned else list(_NO_OWNED_SOURCES)), None
    norm = [_normalize_video_stem(s) for s in raw]
    if any(n not in owned for n in norm):
        return None, (jsonify({"error": "forbidden_source"}), 403)
    return norm, None


def _query_job_owner_ok(job_id: str, user_id: Optional[str]) -> Optional[bool]:
    """None if the job is unknown; True if owned by user_id; False if foreign.
    Checks the in-memory query_jobs map first (carries user_id), then jobs_store —
    so neither the sqlite nor the in-process fallback path can leak across users."""
    with query_jobs_lock:
        j = query_jobs.get(job_id)
    if j is not None and "user_id" in j:
        return j.get("user_id") == user_id
    try:
        from app.domains.jobs.jobs_store import get_job as _js_get
        row = _js_get(job_id)
    except Exception:
        row = None
    if row is not None:
        return row.get("user_id") == user_id
    if j is not None:
        return False  # in-mem present without user_id (legacy) → deny under enforcement
    return None


def _update_source_status(
    source_id: str,
    status: str,
    progress: float = None,
    error: Optional[str] = None,
    substatus: Optional[str] = None,
    capabilities: Optional[Dict[str, bool]] = None
) -> None:
    """
    Update status của một source trong registry.
    - status: "processing" | "index_ready" | "ready" | "error"
    - substatus: "faiss_ready" | "building_memory_tree" | "memory_tree_ready" (optional)
    - capabilities: {"chunk_query": bool, "memory_query": bool} (optional)
    """
    registry = _load_source_registry()
    if source_id not in registry:
        # Nếu chưa có, tạo mới (shouldn't happen, but safe)
        registry[source_id] = {
            "filename": source_id,
            "status": status,
            "progress": progress if progress is not None else 0.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        registry[source_id]["status"] = status
        if progress is not None:
            registry[source_id]["progress"] = progress
        if error is not None:
            registry[source_id]["error"] = error
        elif "error" in registry[source_id] and status != "error":
            # Clear error if status changed from error
            del registry[source_id]["error"]
        
        # Update substatus (optional field)
        if substatus is not None:
            registry[source_id]["substatus"] = substatus
        elif substatus is None and "substatus" in registry[source_id] and status == "error":
            # Clear substatus on error
            del registry[source_id]["substatus"]
        
        # Update capabilities (optional field)
        if capabilities is not None:
            registry[source_id]["capabilities"] = capabilities
        elif capabilities is None and "capabilities" in registry[source_id] and status == "error":
            # Clear capabilities on error
            del registry[source_id]["capabilities"]
    
    _save_source_registry(registry)


def _get_source_status(source_id: str) -> Optional[Dict]:
    """Get status của một source."""
    registry = _load_source_registry()
    return registry.get(source_id)


def _get_source_status_by_stem(source_stem: str) -> Optional[Dict]:
    """
    Get status của source dựa trên source_stem (normalized filename).
    Tìm trong registry source nào có source_stem trùng.
    """
    registry = _load_source_registry()
    # Canonical hoá CẢ HAI phía để khớp kể cả registry cũ (format pre-canonical).
    target = _normalize_video_stem(source_stem)
    for source_id, info in registry.items():
        stored = info.get("source_stem") or info.get("filename", "")
        if stored and _normalize_video_stem(stored) == target:
            return info
    return None


def _check_sources_status(selected_sources: List[str]) -> Dict[str, str]:
    """
    Check status của các selected_sources.
    Trả về dict: {source_stem: status}
    """
    status_map = {}
    for source in selected_sources:
        stem = _normalize_video_stem(source)
        status_info = _get_source_status_by_stem(stem)
        if status_info:
            status_map[stem] = status_info.get("status", "ready")
        else:
            status_map[stem] = "ready"
    return status_map


# LangGraph pipelines được dựng tập trung ở app/wiring.py — gọi ở CUỐI khối init
# (sau khi mọi callback/helper cần thiết đã sẵn sàng).

def _get_session_history_safe(session_id: str, limit: int) -> list:
    try:
        from app.domains.jobs.sessions_store import get_history as _gh
        return _gh(session_id, limit_messages=limit)
    except Exception:
        return []


def _warmup_ollama_background() -> None:
    if (os.getenv("OLLAMA_WARMUP", "1") or "").strip().lower() in ("0", "false", "no", "off"):
        return
    host = (os.getenv("OLLAMA_HOST") or "").strip().rstrip("/")
    if not host:
        return
    model = os.getenv("SLM_MODEL_CHAT", os.getenv("OLLAMA_MODEL", "qwen3.5:9b"))

    def _run():
        try:
            import requests

            r = requests.post(
                f"{host}/api/generate",
                json={
                    "model": model,
                    "prompt": "Hi",
                    "stream": False,
                    "options": {"num_predict": 1, "temperature": 0},
                },
                timeout=120,
            )
            if r.status_code == 200:
                print(f"[warmup] Ollama model {model!r} ready")
            else:
                print(f"[warmup] Ollama HTTP {r.status_code}")
        except Exception as e:
            print(f"[warmup] Ollama warmup failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


_warmup_ollama_background()


# === Dựng toàn bộ LangGraph pipeline qua wiring tập trung (T4) ===
from app.wiring import build_graphs as _build_graphs
from app.clients.mindmap_factory import get_mindmap_pipeline as _get_mindmap_pipeline
from app.clients.summary_factory import get_summary_pipeline as _get_summary_pipeline

# Migrate legacy mindmaps.json / summaries.json → sqlite một lần khi startup
# (best-effort, idempotent — file được rename .migrated sau khi import).
try:
    mindmap_store.migrate_from_json(MINDMAPS_PATH)
except Exception:
    pass
try:
    summary_store.migrate_from_json(SUMMARIES_PATH)
except Exception:
    pass

_graphs = _build_graphs(
    data_dir=DATA_DIR,
    index_meta_path=INDEX_META_JSON_PATH,
    update_source_status=lambda sid, status="processing", **kw: _update_source_status(sid, status, **kw),
    extract_text=extract_text,
    split_text=split_text,
    process_and_store_chunks=process_and_store_chunks,
    append_to_index=append_to_index,
    build_memory_tree_for_sources=build_memory_tree_for_sources,
    jobs_update=_jobs_update_job,
    make_cache_key=_make_query_cache_key,
    get_cached=_get_cached_query,
    set_cached=_set_cached_query,
    check_sources_status=_check_sources_status,
    get_source_status_by_stem=_get_source_status_by_stem,
    search_index=search_index,
    summarize_results=summarize_results,
    query_with_memory_tree=query_with_memory_tree,
    get_session_history=_get_session_history_safe,
    collect_mindmap_input=collect_mindmap_input,
    mindmap_pipeline=_get_mindmap_pipeline(),
    persist_mindmap=mindmap_store.save_record,
    summary_pipeline=_get_summary_pipeline(),
    persist_summary=summary_store.save_record,
)
INGEST_GRAPH = _graphs.ingest
QUERY_GRAPH = _graphs.query
QUERY_GRAPH_BUILD_ERROR = _graphs.query_build_error
MINDMAP_GRAPH = _graphs.mindmap
SUMMARY_GRAPH = _graphs.summary

# Phase 5: reconcile orphaned jobs at startup (queue-aware; never kills live worker jobs).
_reconcile_jobs_safe()


def _langgraph_invoke(graph: Any, state: dict, *, thread_id: str, command: Any = None) -> dict:
    """Graph compile với SqliteSaver yêu cầu configurable.thread_id.

    command != None → resume một interrupt (HITL): truyền Command(resume=...) thay cho state.
    """
    tid = (thread_id or "").strip() or str(uuid.uuid4())
    try:
        return graph.invoke(command if command is not None else state, config={"configurable": {"thread_id": tid}})
    except Exception as e:
        # LangGraph / thư viện đôi khi ném exception str() rỗng — bọc để job/SSE có nội dung.
        if not str(e).strip():
            raise RuntimeError(_job_error_text(e)) from e
        raise


def _job_error_text(exc: BaseException) -> str:
    """Nhiều built-in (TimeoutError, RuntimeError…) có str(exc)==''; không bao giờ trả chuỗi rỗng."""
    msg = str(exc).strip()
    if msg:
        return msg
    name = getattr(type(exc), "__name__", None) or type(exc).__qualname__ or "Exception"
    return f"{name}: không có nội dung chi tiết (xem traceback trong log server)."


def _detect_query_interrupt(graph: Any, thread_id: str) -> Optional[dict]:
    """HITL: phát hiện graph đang tạm dừng tại interrupt().

    langgraph 0.2.x KHÔNG đặt key '__interrupt__' trong kết quả invoke → đọc qua get_state().
    Trả về payload review (dict) nếu đang chờ duyệt, ngược lại None.
    """
    try:
        st = graph.get_state({"configurable": {"thread_id": thread_id}})
    except Exception:
        return None
    if not getattr(st, "next", None):
        return None
    for task in getattr(st, "tasks", []) or []:
        intrs = getattr(task, "interrupts", None) or ()
        if intrs:
            return getattr(intrs[0], "value", None) or {}
    return None


def _mark_query_interrupted(jid: str, review: dict) -> None:
    """HITL: đánh dấu job chờ người duyệt (SSE coi 'interrupted' là terminal)."""
    review = review or {}
    result_obj = {"payload": {"review": review}, "status": 200}
    with query_jobs_lock:
        if jid in query_jobs:
            query_jobs[jid]["status"] = "interrupted"
            query_jobs[jid]["result"] = result_obj
    if _jobs_update_job:
        try:
            _jobs_update_job(jid, status="interrupted", current_node="ReviewGate", result=result_obj)
        except Exception:
            pass


_CITE_PREFIX_RE = re.compile(r"^\s*\[\s*Nguồn\s*:\s*(.+?)\s*,\s*đoạn\s*(\d+)\s*\]\s*", re.IGNORECASE)


def _attach_evidence(payload: dict, out: dict, max_chunks: int = 12) -> None:
    """Bổ sung provenance (`sources` + `chunks`) vào payload query từ state của graph,
    để FE dựng "lề bằng chứng". CHỈ THÊM (additive) — không đổi answer/error.

    Tái dùng stem canonical đã có trong state (`retrieved_sources`/`retrieved_stems`)
    và prefix "[Nguồn: <stem>, đoạn <id>]" do node RetrieveFAISS gắn — KHÔNG suy lại
    định danh (xem .playbook: một nguồn sự thật cho source_stem)."""
    if not isinstance(payload, dict) or not isinstance(out, dict):
        return
    srcs = out.get("retrieved_sources")
    if isinstance(srcs, list) and srcs and not payload.get("sources"):
        seen: list[str] = []
        for s in srcs:
            s = str(s).strip()
            if s and s not in seen:
                seen.append(s)
        if seen:
            payload["sources"] = seen
    chunks = out.get("retrieved_chunks")
    stems = out.get("retrieved_stems")
    if isinstance(chunks, list) and chunks and not payload.get("chunks"):
        ev: list[dict] = []
        for i, c in enumerate(chunks[:max_chunks]):
            text = str(c) if c is not None else ""
            stem, chunk_id = "", ""
            m = _CITE_PREFIX_RE.match(text)
            if m:
                stem = m.group(1).strip()
                chunk_id = m.group(2)
                text = text[m.end():]
            elif isinstance(stems, list) and i < len(stems) and stems[i]:
                stem = str(stems[i]).strip()
            snippet = text.strip().replace("\x00", "")
            if len(snippet) > 600:
                snippet = snippet[:600].rstrip() + "…"
            ev.append({"stem": stem, "chunk_id": chunk_id, "snippet": snippet})
        if ev:
            payload["chunks"] = ev


def _conversation_enabled() -> bool:
    """Feature gate for the Conversation Context Layer (default OFF)."""
    try:
        from shared.config import get_settings
        return bool(get_settings().conversation_context_enabled)
    except Exception:
        return False


def _save_conversation_turns(
    session_id: str,
    question: str,
    answer: str,
    *,
    source_ids: Optional[list] = None,
    source_context_hash: Optional[str] = None,
    cited_chunk_ids: Optional[list] = None,
    rewritten_query: Optional[str] = None,
    metadata: Optional[dict] = None,
    user_id: Optional[str] = None,
    enforce_owner: bool = False,
) -> None:
    """Persist a user+assistant turn pair to the conversation store. Best-effort:
    a DB failure must never break /query (fail-open). Gated on the feature flag.
    When owner-enforced without a user, skip (never write NULL-owned protected rows)."""
    if not _conversation_enabled():
        return
    if not session_id or not (answer or "").strip():
        return
    if enforce_owner and not user_id:
        return  # flag on + no authenticated user → do not persist
    try:
        from app.domains.conversation import store as _conv
        _conv.append_message(
            session_id, "user", question,
            selected_source_ids=source_ids, source_context_hash=source_context_hash,
            user_id=user_id, enforce_owner=enforce_owner,
        )
        _conv.append_message(
            session_id, "assistant", str(answer),
            selected_source_ids=source_ids, source_context_hash=source_context_hash,
            cited_chunk_ids=cited_chunk_ids, rewritten_query=rewritten_query,
            answer_summary=str(answer)[:300], metadata=metadata,
            user_id=user_id, enforce_owner=enforce_owner,
        )
    except Exception:
        pass


def _finalize_query_job(jid: str, session_id: str, question: str, out: dict,
                        *, user_id: Optional[str] = None, enforce_owner: bool = False) -> None:
    """Trích payload/status từ kết quả graph → cập nhật query_jobs/jobs_store + persist history.

    Dùng chung cho /query và /query-resume. user_id/enforce_owner scope the
    conversation-turn persistence to the caller (Phase B).
    """
    raw_pl = out.get("payload")
    payload = dict(raw_pl) if isinstance(raw_pl, dict) else {}
    ans_state = (out.get("answer") or "").strip()
    if ans_state and not (payload.get("answer") or "").strip():
        payload["answer"] = out["answer"]
    has_ans = bool((payload.get("answer") or "").strip())
    has_err = bool((payload.get("error") or "").strip())
    if not has_ans and not has_err:
        payload["error"] = out.get("error") or "Unknown error"
    if has_ans:
        try:
            _attach_evidence(payload, out)
        except Exception:
            pass
    status = int(out.get("status_code") or 200)
    result_obj = {"payload": payload, "status": status}

    with query_jobs_lock:
        if jid in query_jobs:
            query_jobs[jid]["status"] = "done"
            query_jobs[jid]["result"] = result_obj

    if _jobs_update_job:
        try:
            _jobs_update_job(jid, status="done", progress=100, current_node="Finalize", result=result_obj)
        except Exception:
            pass

    # Persist conversation history (best-effort)
    try:
        if isinstance(payload, dict) and payload.get("answer"):
            from app.domains.jobs.sessions_store import append_messages as _ss_append
            _ss_append(session_id, [{"role": "user", "content": question}, {"role": "assistant", "content": str(payload.get("answer"))}])
    except Exception:
        pass

    # Conversation Context Layer: structured turn store with source scope (flag-gated).
    try:
        if isinstance(payload, dict) and payload.get("answer"):
            src = out.get("selected_sources") or []
            try:
                import app.domains.cache.llm_cache as _lc
                sch = _lc.source_context_hash(src, out.get("language"), out.get("category"), bool(out.get("use_memory_tree", True)))
            except Exception:
                sch = None
            cited = [c.get("chunk_id") for c in (payload.get("chunks") or []) if isinstance(c, dict) and c.get("chunk_id")]
            _rewritten = out.get("standalone_question")
            _mode = out.get("context_mode") or "standalone"
            _save_conversation_turns(
                session_id, question, str(payload.get("answer")),
                source_ids=src, source_context_hash=sch, cited_chunk_ids=cited or None,
                rewritten_query=(_rewritten if _rewritten and _rewritten != question else None),
                metadata={"context_mode": _mode, "context_signature": out.get("context_signature")},
                user_id=user_id, enforce_owner=enforce_owner,
            )
    except Exception:
        pass


# -------------------------
# 🔄 Background tasks (non-blocking)
# -------------------------
def run_memory_tree_job(source_stems: List[str]):
    """Build Memory Tree for the given sources. Runs in a daemon thread
    (QUEUE_ENABLED=false) OR an RQ worker process (QUEUE_ENABLED=true) — identical
    behaviour, no Flask request context. Enqueued by dotted path
    `app.main.run_memory_tree_job`. Fire-and-forget: there is no per-job status store
    today (results land in memory_trees.json, surfaced by /memory-tree-status); errors are
    logged, not persisted, matching the existing contract."""
    print(f"memory_tree_job_running sources={source_stems}", flush=True)
    try:
        build_memory_tree_for_sources(source_stems)
        print(f"memory_tree_job_done sources={source_stems}", flush=True)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"memory_tree_job_failed sources={source_stems} err={str(exc)[:80]}", flush=True)


# Backward-compatible alias (older callers / tests may reference this name).
_build_memory_tree_background = run_memory_tree_job


def _trigger_memory_tree_build(source_stems: List[str]):
    """Trigger Memory Tree build (non-blocking). Phase 5 Step 4: via enqueue_job — daemon
    thread when QUEUE_ENABLED=false (default, unchanged), RQ 'memory' queue when true."""
    if not source_stems:
        return
    from app.jobs.queue import enqueue_job
    res = enqueue_job(run_memory_tree_job, args=(source_stems,), queue="memory")
    _event = {"rq": "memory_tree_enqueue_rq", "thread": "memory_tree_enqueue_thread",
              "thread_fallback": "memory_tree_queue_fallback_thread"}.get(res.get("mode"),
                                                                          f"memory_tree_enqueue_{res.get('mode')}")
    print(f"{_event} sources={source_stems}", flush=True)


def _run_ingest_job(source_id: str, file_path: str, filename: str) -> None:
    """Ingest execution body. Runs EITHER in a daemon thread (QUEUE_ENABLED=false)
    OR in an RQ worker process (QUEUE_ENABLED=true) — identical behaviour. Enqueued
    by dotted path `app.main._run_ingest_job`, so the worker builds/reuses INGEST_GRAPH
    on import. Status/result land in the shared jobs.sqlite; the graph nodes own the
    done/error writes (atomic-with-result invariant preserved)."""
    job_id = source_id
    try:
        if _jobs_update_job:
            _jobs_update_job(job_id, status="running", current_node="Ingest")
    except Exception:
        pass
    try:
        init_state = {
            "job_id": job_id,
            "source_id": source_id,
            "file_path": file_path,
            "filename": filename,
            "progress": 0,
            "current_node": "Queued",
            "artifacts": {},
            "error": None,
        }
        _langgraph_invoke(INGEST_GRAPH, init_state, thread_id=job_id)
    except Exception as exc:
        try:
            _update_source_status(source_id, "error", progress=0.0, error=_job_error_text(exc))
        except Exception:
            pass


def _trigger_background_ingest(source_id: str, file_path: str, filename: str):
    """
    Trigger ingest qua LangGraph. Phase 5: qua enqueue_job — daemon thread khi
    QUEUE_ENABLED=false (mặc định), RQ worker khi bật. FE polling không đổi.
    """
    if INGEST_GRAPH is None or _jobs_create_job is None:
        print("[INGEST] INGEST_GRAPH hoặc jobs_store không khả dụng — không thể xử lý upload.")
        try:
            _update_source_status(source_id, "error", progress=0.0, error="Ingest LangGraph unavailable")
        except Exception:
            pass
        return

    job_id = source_id  # re-use source_id làm job_id để FE polling đơn giản
    try:
        _jobs_create_job(job_id, job_type="ingest", status="pending", progress=0, current_node="Queued", user_id=_current_user_id())
    except Exception:
        pass

    from app.jobs.queue import enqueue_job
    res = enqueue_job(_run_ingest_job, args=(source_id, file_path, filename),
                      queue="ingest", job_id=job_id)
    print(f"🚀 [Background] ingest source={source_id} mode={res.get('mode')}")


# -------------------------
# 📤 Process raw text
# -------------------------
@app.post('/process-doc')
def process_doc():
    _uid, err = _require_app_user()
    if err:
        return err
    text = request.json.get('text', '')
    if not text:
        return jsonify({'error': 'Missing text'}), 400

    chunks = split_text(text)

    # Thay toàn bộ logic cũ bằng hàm mới
    video_name = "raw_text"  # hoặc tạo tên có nghĩa hơn
    video_path, metadata_entries = process_and_store_chunks(
        chunks=chunks,
        video_name=video_name,
        timestamp=datetime.now().isoformat()
    )

    # Append từng entry với custom metadata
    for entry in metadata_entries:
        append_to_index(
            chunks=[entry["text"]],
            video_name=video_path,
            custom_metadata=[{
                "parent_id": entry.get("parent_id"),
                "sub_order": entry.get("sub_order"),
                "total_parts": entry.get("total_parts"),
                "is_subchunk": entry.get("is_subchunk", False)
            }]
        )

    # Trigger background task để build Memory Tree (non-blocking)
    source_stem = Path(video_name).stem.lower()
    _trigger_memory_tree_build([source_stem])

    return jsonify({
        'video_path': video_path,
        'status': 'uploaded',
        'message': 'File processed and index built. Memory Tree is being built in background.'
    })


# -------------------------
# 📤 Upload single file (ASYNC)
# -------------------------
# Ký tự cấm trên tên file Windows (+ control chars). NFKD ở canonicalizer lo phần
# khớp; ở đây chỉ lo lưu file vật lý an toàn (chặn ký tự cấm + path traversal).
_ILLEGAL_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_save_path(filename: str) -> str:
    """Đường lưu vật lý AN TOÀN trong INPUT_DIR: bỏ thành phần thư mục (chống
    traversal), thay ký tự cấm → '_', và đảm bảo không trùng file sẵn có."""
    base = os.path.basename((filename or "").strip()) or "file"
    safe = _ILLEGAL_FS_CHARS.sub("_", base).strip().strip(".") or "file"
    path = os.path.join(INPUT_DIR, safe)
    root, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{root}_{n}{ext}"
        n += 1
    return path


def _unique_display_filename(filename: str, registry: dict) -> str:
    """Chống trùng tên: nếu canonical stem đã có trong registry → thêm hậu tố
    " (n)" trước đuôi để hai tài liệu cùng tên KHÔNG trộn chunk (mỗi cái 1 stem)."""
    fn = filename or "file"
    existing = {(info.get("source_stem") or "") for info in registry.values()}
    if _normalize_video_stem(fn) not in existing:
        return fn
    base, ext = os.path.splitext(fn)
    n = 2
    while _normalize_video_stem(f"{base} ({n}){ext}") in existing:
        n += 1
    return f"{base} ({n}){ext}"


def _ingest_uploaded_file(file) -> dict:
    """Đăng ký + lưu an toàn + trigger ingest cho 1 file. Dùng chung cho
    /upload-file và /upload-multiple (đồng nhất: source_id + registry + poll)."""
    source_id = str(uuid.uuid4())
    registry = _load_source_registry()
    # Tên hiển thị (chống trùng) — canonical stem suy từ tên này nên FE chọn theo
    # tên hiển thị sẽ khớp chunk; lưu vật lý theo path an toàn riêng.
    filename = _unique_display_filename(file.filename or "file", registry)
    save_path = _safe_save_path(filename)
    os.makedirs(INPUT_DIR, exist_ok=True)
    file.save(save_path)

    source_stem = _normalize_video_stem(filename)
    registry[source_id] = {
        "filename": filename,
        "source_stem": source_stem,
        "input_path": save_path,   # để xóa file gốc khi delete
        "status": "processing",
        "progress": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Auth Hardening Phase A: owner stamp (None when no token). Unenforced this phase.
        "user_id": _current_user_id(),
    }
    _save_source_registry(registry)
    _trigger_background_ingest(source_id, save_path, filename)
    return {
        'source_id': source_id,
        'filename': filename,
        'video_stem': source_stem,
        'status': 'processing',
        'progress': 0.0,
        'can_query': False,
    }


@app.post('/upload-file')
def upload_file():
    """Upload file và trả response ngay, xử lý ingest chạy background."""
    _uid, err = _require_app_user()
    if err:
        return err
    file = request.files.get('file')
    if not file or not (file.filename or "").strip():
        return jsonify({'error': 'Missing file'}), 400
    return jsonify(_ingest_uploaded_file(file))


@app.post('/upload')
def upload():
    """
    Alias cho /upload-file để đồng nhất với spec.
    """
    return upload_file()


# -------------------------
# 📊 Source Status Endpoint
# -------------------------
@app.get('/sources/<source_id>/status')
def get_source_status(source_id: str):
    """
    Lấy status của một source (processing | ready | error).
    UI sẽ polling endpoint này để cập nhật progress.
    """
    uid, err = _require_app_user()
    if err:
        return err
    status_info = _get_source_status(source_id)
    if not status_info:
        return jsonify({'error': 'Source not found'}), 404
    # Owner scope: a foreign source is indistinguishable from a missing one (404).
    if _auth_protect_enabled() and status_info.get("user_id") != uid:
        return jsonify({'error': 'Source not found'}), 404
    
    status = status_info.get('status', 'processing')
    capabilities = status_info.get('capabilities') if isinstance(status_info, dict) else None
    if not isinstance(capabilities, dict):
        capabilities = {}

    # Single source of truth: can_query
    # TRUE when FAISS is ready (chunk-level search is available). Do NOT wait for memory tree / mindmap / summary.
    can_query = (
        status in ("index_ready", "ready")
        and bool(capabilities.get("chunk_query", True))  # default True for backward compatibility
        and status != "error"
    )

    response = {
        'status': status,
        'progress': status_info.get('progress', 0.0),
        'substatus': status_info.get('substatus'),
        'capabilities': capabilities if capabilities else None,
        'can_query': bool(can_query),
        'video_stem': status_info.get('source_stem') or status_info.get('video_stem'),
    }
    
    if status_info.get('error'):
        response['error'] = status_info['error']
    
    return jsonify(response)

# -------------------------
# 📤 Upload multiple files
# -------------------------
@app.post('/upload-multiple')
def upload_multiple():
    """Upload nhiều file — mỗi file đi CÙNG luồng async với /upload-file
    (tạo source_id + registry + background ingest) nên FE poll status được."""
    _uid, err = _require_app_user()
    if err:
        return err
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'Missing files'}), 400

    sources, results = [], []
    for file in files:
        if not (file.filename or "").strip():
            results.append({'file': file.filename, 'error': 'Empty filename'})
            continue
        try:
            info = _ingest_uploaded_file(file)
            sources.append(info)
            results.append({'file': info['filename'], 'source_id': info['source_id'], 'status': 'processing'})
        except Exception as e:
            import traceback; traceback.print_exc()
            results.append({'file': file.filename, 'error': f'Upload failed: {str(e)}'})

    return jsonify({'sources': sources, 'results': results})
@app.get('/list-indexed')
def list_indexed():
    """
    Lấy danh sách tất cả sources đã được index.
    Trả về format mà frontend expect: { video: stem, chunks: [...], num_chunks: N }
    """
    uid, err = _require_app_user()
    if err:
        return err
    try:
        with open(INDEX_META_JSON_PATH, encoding='utf-8') as f:
            meta = json.load(f)

        # Map canonical source_stem -> tên hiển thị (filename gốc) từ registry.
        stem_to_filename = {}
        try:
            for info in (_load_source_registry() or {}).values():
                st = _normalize_video_stem(info.get('source_stem') or info.get('filename') or '')
                if st and st not in stem_to_filename:
                    stem_to_filename[st] = info.get('filename') or st
        except Exception:
            pass

        video_map = {}
        from app.domains.vectorstore import chunk_text_store
        for key, item in meta.items():
            if not isinstance(key, str) or not key.isdigit():
                continue
            video = item.get('video', '').strip()
            if not video or video.lower() == 'unknown':
                continue
            # Canonical stem DÙNG CHUNG với retrieval/upload (bỏ path/ext/timestamp,
            # sanitize space/đặc biệt). Gộp các chunk cùng nguồn dù video_path khác ts.
            video_stem = _normalize_video_stem(item.get('source_stem') or video)
            if not video_stem:
                continue
            t = chunk_text_store.get_text(int(key)) or item.get('text') or ''
            video_map.setdefault(video_stem, []).append(t)

        sources = []
        for video_stem, chunks in video_map.items():
            sources.append({
                'video': video_stem,          # giữ key cũ (FE đang đọc s.video)
                'video_stem': video_stem,     # khóa canonical (FE nên dùng cái này)
                'filename': stem_to_filename.get(video_stem) or video_stem,  # tên hiển thị
                'chunks': chunks,
                'num_chunks': len(chunks),
                'can_query': True,
            })

        # Owner scope (flag on): return only the caller's sources; legacy NULL-owner
        # sources are hidden (owned_stems excludes them under enforcement).
        if _auth_protect_enabled():
            owned = owned_stems(uid)
            sources = [s for s in sources if s.get('video_stem') in owned]

        return jsonify({'sources': sources})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'sources': []})


# -------------------------
# 🎥 Serve video
# -------------------------
@app.get('/videos/<name>')
def serve_video(name):
    uid, err = _require_app_user()
    if err:
        return err
    # Owner scope: derive the canonical stem from the (path-safe) basename and deny
    # a foreign/missing video with 404 (no existence oracle, no path traversal —
    # send_from_directory already confines to VIDEOS_DIR).
    if _auth_protect_enabled():
        stem = _normalize_video_stem(os.path.basename(name or ""))
        if not _source_owner_ok(stem, uid):
            return jsonify({'error': 'Not found'}), 404
    return send_from_directory(VIDEOS_DIR, name)


# -------------------------
# 🔍 Query — chỉ LangGraph (QUERY_GRAPH)
# -------------------------
@app.post('/query')
def query():
    """
    Async job-based query:
    - POST /query returns immediately with job_id
    - background thread runs the existing pipeline
    - FE polls GET /query-status/<job_id>
    """
    _cleanup_old_query_jobs()

    data = request.json or {}
    q = data.get('q') or data.get('question') or ''
    selected_sources = data.get('sources') or []
    use_memory_tree = data.get('use_memory_tree', True)
    session_id = (data.get("session_id") or "").strip()
    f_category = (data.get('category') or '').strip() or None
    f_language = (data.get('language') or '').strip() or None

    # Phase C: auth gate FIRST (before input validation) so a no-token request is
    # 401, not 400. /query stays in-process. Then owner-scope the selected sources.
    q_uid, q_err = _require_app_user()
    if q_err:
        return q_err

    if not (q or "").strip():
        return jsonify({'error': 'Missing query'}), 400

    selected_sources, src_err = _resolve_owned_query_sources(selected_sources, q_uid)
    if src_err:
        return src_err

    # Phase 4: ingress rate limit (off by default; fail-open). Structured 429.
    allowed, retry_after = _rate_limit_check(_rl_scope_id(session_id))
    if not allowed:
        return _rate_limited_response(retry_after)

    # Limit concurrent query threads (per-worker admission). Structured 429 + Retry-After.
    acquired = _query_semaphore.acquire(blocking=False)
    if not acquired:
        return _admission_rejected_response()

    job_id = str(uuid.uuid4())
    if not session_id:
        session_id = str(uuid.uuid4())
    # Auth Hardening Phase B: capture the caller identity NOW (request context;
    # the query runs in a thread with no request). /query itself is NOT gated in
    # Phase B — but with the flag on, conversation persistence/read is owner-scoped,
    # and with no authenticated user we skip it entirely (no NULL-owned rows).
    req_user_id = _current_user_id()
    conv_enforce = _auth_protect_enabled()
    auth_enforce = conv_enforce  # Phase C: same flag gates source/query protection
    # Conversation Context Layer: ensure the conversation row exists (flag-gated, fail-open).
    if _conversation_enabled() and not (conv_enforce and not req_user_id):
        try:
            from app.domains.conversation import store as _conv
            _conv.ensure_conversation(session_id, active_source_scope=selected_sources or [],
                                      user_id=req_user_id, enforce_owner=conv_enforce)
        except Exception:
            pass
    try:
        from app.domains.jobs.jobs_store import create_job as _js_create
        _js_create(job_id, job_type="query", status="pending", progress=0, current_node="Queued", user_id=req_user_id)
    except Exception:
        pass
    with query_jobs_lock:
        query_jobs[job_id] = {
            "status": "pending",
            "result": None,
            "error": None,
            "created_at": time.time(),
            "user_id": req_user_id,  # Phase C: owner guard for status/stream/resume
        }

    def process_query_job(jid: str, question: str, sources: list, use_mem: bool, category: str | None = None, language: str | None = None) -> None:
        start_ts = time.time()
        sf_lock = None  # (key, token) if we became the single-flight leader
        try:
            with query_jobs_lock:
                if jid in query_jobs:
                    query_jobs[jid]["status"] = "running"

            if QUERY_GRAPH is None:
                raise RuntimeError("QUERY_GRAPH chưa khởi tạo — kiểm tra logs khởi động.")

            # Phase 3 single-flight: coalesce duplicate/equivalent concurrent queries.
            # Fail-open — leader runs the graph below; follower served from cache returns here.
            # Phase C: with the flag on, BYPASS single-flight (no cross-user coalescing)
            # until Phase E adds user-scoped keys. Resolved per-user sources already
            # scope the bucket; the bypass is belt-and-suspenders.
            if auth_enforce:
                _sf = {"served": False, "lock": None}
            else:
                try:
                    _sf = _single_flight_try(jid, question, sources, use_mem, category, language, session_id, user_id=req_user_id, enforce_owner=conv_enforce)
                except Exception:
                    _sf = {"served": False, "lock": None}  # single-flight must never break the answer path
            if _sf.get("served"):
                return  # follower finalized from the leader's cached answer
            sf_lock = _sf.get("lock")

            try:
                from app.domains.jobs.jobs_store import clear_token_buffer as _js_tb_clear
                _js_tb_clear(jid)
            except Exception:
                pass

            try:
                from app.domains.jobs.sessions_store import get_history as _ss_get
                history = _ss_get(session_id, limit_messages=8)
            except Exception:
                history = []

            # Conversation Context Layer (flag-gated): replace the unscoped session
            # history with source-scoped, reset-aware turns so context never leaks
            # across documents and respects Clear-context. Fail-open to the legacy history.
            conv_ctx = None
            conv_sch = None
            if _conversation_enabled() and not (conv_enforce and not req_user_id):
                try:
                    conv_sch = llm_cache.source_context_hash(sources or [], language, category, bool(use_mem))
                except Exception:
                    conv_sch = None
                try:
                    from app.domains.conversation.context_builder import build_recent_conversation_context
                    conv_ctx = build_recent_conversation_context(
                        session_id, selected_sources=sources or [], source_context_hash=conv_sch,
                        user_id=req_user_id, enforce_owner=conv_enforce,
                    )
                    if not conv_ctx.is_empty:
                        history = [{"role": t["role"], "content": t["content"]} for t in conv_ctx.turns]
                    else:
                        history = []  # no same-scope context → treat as standalone (no leak)
                except Exception:
                    conv_ctx = None

            # Phase C: rewrite the follow-up into a standalone question for retrieval.
            # Original question is preserved for answer generation. Fail-open to original.
            standalone_q = question
            context_mode = "standalone"
            context_sig = None
            if _conversation_enabled() and conv_ctx is not None and not conv_ctx.is_empty:
                context_sig = conv_ctx.context_signature
                try:
                    from app.domains.conversation.rewrite import rewrite_followup_question, decide_context_mode
                    _rw = rewrite_followup_question(question, conv_ctx, sources or [])
                    context_mode = decide_context_mode(_rw)
                    if context_mode == "contextual":
                        standalone_q = (_rw.get("standalone_question") or question)
                        print(f"conversation_rewrite mode={context_mode} conf={_rw.get('confidence')} "
                              f"q={question[:40]!r} -> {standalone_q[:60]!r}", flush=True)
                except Exception:
                    pass

            init_state = {
                "job_id": jid,
                "session_id": session_id,
                "conversation_history": history,
                "conversation_context": (conv_ctx.to_dict() if conv_ctx is not None else None),
                "source_context_hash": conv_sch,
                "original_question": question,
                "standalone_question": standalone_q,
                "context_mode": context_mode,
                "context_signature": context_sig,
                "q": question,
                "selected_sources": sources or [],
                "use_memory_tree": bool(use_mem),
                "category": category,
                "language": language,
                # Phase C: with auth protection on, bypass the shared semantic answer
                # cache (read + write) so no cross-user answer reuse (e.g. via stem
                # recycling after delete/reupload). Proper user-scoped keys land in Phase E.
                "auth_no_cache": auth_enforce,
                "retrieved_chunks": [],
                "retrieved_sources": [],
                "context": "",
                "answer": "",
                "retry_count": 0,
                "low_confidence": False,
                "progress": 0,
                "current_node": "Queued",
                "error": None,
            }
            # thread_id = jid (duy nhất/truy vấn) → tránh rò state/interrupt giữa các lượt cùng session; lưu để /query-resume dùng lại.
            with query_jobs_lock:
                if jid in query_jobs:
                    query_jobs[jid]["thread_id"] = jid
                    query_jobs[jid]["question"] = question
                    query_jobs[jid]["session_id"] = session_id
            out = _langgraph_invoke(QUERY_GRAPH, init_state, thread_id=jid)
            review = _detect_query_interrupt(QUERY_GRAPH, jid)
            if review is not None:
                _mark_query_interrupted(jid, review)
                return
            _finalize_query_job(jid, session_id, question, out, user_id=req_user_id, enforce_owner=conv_enforce)
        except Exception as exc:
            err_txt = _job_error_text(exc)
            with query_jobs_lock:
                if jid in query_jobs:
                    query_jobs[jid]["status"] = "error"
                    query_jobs[jid]["error"] = err_txt
            if _jobs_update_job:
                try:
                    _jobs_update_job(jid, status="error", error_text=err_txt)
                except Exception:
                    pass
            logging.exception("[QUERY_JOB] job_id=%s failed: %s", jid, err_txt)
        finally:
            # Release the single-flight lock AFTER finalize (cache already written) so
            # followers read the leader's answer rather than racing an empty cache.
            if sf_lock:
                try:
                    _single_flight_release(sf_lock[0], sf_lock[1])
                except Exception:
                    pass
            elapsed = time.time() - start_ts
            if elapsed > QUERY_JOB_TIMEOUT_SEC:
                print(f"[QUERY_JOB] job_id={jid} exceeded timeout={QUERY_JOB_TIMEOUT_SEC}s (elapsed={elapsed:.1f}s)")
            _query_semaphore.release()

    thread = threading.Thread(
        target=process_query_job,
        args=(job_id, q, selected_sources, use_memory_tree, f_category, f_language),
        daemon=True
    )
    thread.start()

    # Return immediately (no blocking)
    return jsonify({"job_id": job_id, "status": "pending", "session_id": session_id}), 202


@app.get('/query-status/<job_id>')
def query_status(job_id: str):
    uid, err = _require_app_user()
    if err:
        return err
    if _auth_protect_enabled() and _query_job_owner_ok(job_id, uid) is not True:
        return jsonify({"error": "Job not found"}), 404  # foreign/unknown → no oracle
    _cleanup_old_query_jobs()
    # Ưu tiên SQLite jobs store nếu bật
    if (os.getenv("USE_SQLITE_JOBS", "1") or "").strip() not in ("0", "false", "False"):
        try:
            from app.domains.jobs.jobs_store import get_job as _js_get
            j = _js_get(job_id)
            if j and j.get("job_type") in ("query", None):
                return jsonify({
                    "status": j.get("status"),
                    "result": j.get("result"),
                    "error": j.get("error"),
                }), 200
        except Exception:
            pass
    with query_jobs_lock:
        job = query_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({
            "status": job.get("status"),
            "result": job.get("result"),
            "error": job.get("error"),
        }), 200


@app.post('/query-resume/<job_id>')
def query_resume(job_id: str):
    """HITL: tiếp tục một job đang chờ duyệt (status='interrupted').

    Body: {"action": "approve"|"edit"|"reject", "answer": "..."}.
    """
    uid, err = _require_app_user()
    if err:
        return err
    if _auth_protect_enabled() and _query_job_owner_ok(job_id, uid) is not True:
        return jsonify({"error": "Job not found"}), 404  # cannot resume another user's job
    data = request.json or {}
    action = str(data.get("action") or "approve").strip().lower()
    if action not in ("approve", "edit", "reject"):
        return jsonify({"error": "action phải là approve, edit hoặc reject"}), 400
    decision = {"action": action}
    if action == "edit":
        decision["answer"] = str(data.get("answer") or "").strip()

    with query_jobs_lock:
        job = query_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        if job.get("status") != "interrupted":
            return jsonify({"error": "Job không ở trạng thái chờ duyệt"}), 409
        tid = job.get("thread_id") or job_id
        session_id = job.get("session_id") or ""
        question = job.get("question") or ""
        job["status"] = "running"

    acquired = _query_semaphore.acquire(blocking=False)
    if not acquired:
        with query_jobs_lock:
            if job_id in query_jobs:
                query_jobs[job_id]["status"] = "interrupted"
        return _admission_rejected_response()

    # Capture caller identity in request context (resume_job runs in a thread).
    resume_uid = _current_user_id()
    resume_enforce = _auth_protect_enabled()

    def resume_job() -> None:
        try:
            from langgraph.types import Command
            out = _langgraph_invoke(QUERY_GRAPH, None, thread_id=tid, command=Command(resume=decision))
            review = _detect_query_interrupt(QUERY_GRAPH, tid)
            if review is not None:
                _mark_query_interrupted(job_id, review)
                return
            _finalize_query_job(job_id, session_id, question, out, user_id=resume_uid, enforce_owner=resume_enforce)
        except Exception as exc:
            err_txt = _job_error_text(exc)
            with query_jobs_lock:
                if job_id in query_jobs:
                    query_jobs[job_id]["status"] = "error"
                    query_jobs[job_id]["error"] = err_txt
            if _jobs_update_job:
                try:
                    _jobs_update_job(job_id, status="error", error_text=err_txt)
                except Exception:
                    pass
            logging.exception("[QUERY_RESUME] job_id=%s failed: %s", job_id, err_txt)
        finally:
            _query_semaphore.release()

    threading.Thread(target=resume_job, daemon=True).start()
    return jsonify({"job_id": job_id, "status": "running"}), 202


@app.get("/query-stream/<job_id>")
def query_stream(job_id: str):
    """
    SSE: stream trạng thái job query realtime (thay polling).
    Dữ liệu đọc từ jobs_store (SQLite) nếu có.
    Phase 2C: header chuẩn proxy + timeout SSE_TIMEOUT_SEC.
    """
    uid, err = _require_app_user()
    if err:
        return err
    if _auth_protect_enabled() and _query_job_owner_ok(job_id, uid) is not True:
        return jsonify({"error": "Job not found"}), 404  # SSE token_buffer must not leak cross-user
    sse_timeout = int(os.getenv("SSE_TIMEOUT_SEC", "300"))

    def generate():
        waited = 0.0
        interval = float(os.getenv("SSE_POLL_INTERVAL_SEC", "0.4"))
        last_token_len = 0
        while waited < sse_timeout:
            try:
                from app.domains.jobs.jobs_store import get_job as _js_get
                j = _js_get(job_id) or {}
            except Exception:
                j = {}

            buf = j.get("token_buffer") or ""
            if isinstance(buf, str) and len(buf) > last_token_len:
                delta = buf[last_token_len:]
                last_token_len = len(buf)
                yield f"data: {json.dumps({'type': 'token', 'content': delta, 'job_id': job_id}, ensure_ascii=False)}\n\n"

            st = j.get("status")
            err_raw = j.get("error")
            if isinstance(err_raw, str):
                err_sse = err_raw.strip()
            elif err_raw is not None:
                err_sse = str(err_raw).strip()
            else:
                err_sse = ""
            if st == "error" and not err_sse:
                err_sse = "Lỗi không xác định khi xử lý truy vấn."

            payload = {
                "type": "status",
                "job_id": job_id,
                "status": st,
                "progress": j.get("progress"),
                "current_node": j.get("current_node"),
                "result": j.get("result"),
                "error": err_sse or None,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if st in ("done", "error", "interrupted"):
                return
            time.sleep(interval)
            waited += interval

        yield f"data: {json.dumps({'job_id': job_id, 'error': 'SSE timeout', 'status': 'error', 'type': 'status'}, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

# -------------------------
# 📝 Summary v2 — section-first, job async (mirror mindmap; spec docs/SUMMARY_V2_SPEC.md)
# -------------------------
def _summary_input_and_hash(source_names: list[str], length_mode: str) -> tuple[dict, str]:
    mm = collect_mindmap_input(INDEX_META_JSON_PATH, source_names)
    h = summary_schema.content_hash(mm.get("sources") or [],
                                    [c["text"] for c in mm.get("chunks") or []],
                                    [c.get("heading_path", "") for c in mm.get("chunks") or []],
                                    length_mode)
    return mm, h


def _start_summary_job(source_names: list[str], mm_input: dict, content_hash: str,
                       length_mode: str) -> str:
    """Phase 5 Step 2: dispatch Summary v2 via enqueue_job — daemon thread when
    QUEUE_ENABLED=false (default, unchanged), RQ 'summary' queue when true. FE polling
    (/summary-status) unchanged; result still in summary_store."""
    job_id = str(uuid.uuid4())
    from app.domains.jobs.jobs_store import create_job
    create_job(job_id, job_type="summary", status="pending", progress=0, current_node="Queued", user_id=_current_user_id())
    from app.jobs.queue import enqueue_job
    res = enqueue_job(run_summary_job,
                      args=(job_id, source_names, mm_input, content_hash, length_mode),
                      queue="summary", job_id=job_id)
    _event = {"rq": "summary_enqueue_rq", "thread": "summary_enqueue_thread",
              "thread_fallback": "summary_queue_fallback_thread"}.get(res.get("mode"),
                                                                      f"summary_enqueue_{res.get('mode')}")
    print(f"{_event} job_id={job_id}", flush=True)
    return job_id


def run_summary_job(job_id: str, source_names: list[str], mm_input: dict,
                    content_hash: str, length_mode: str) -> None:
    """Summary v2 execution body. Runs in a daemon thread (QUEUE_ENABLED=false) OR an RQ
    worker process (QUEUE_ENABLED=true) — identical behaviour, no Flask request context
    needed. Enqueued by dotted path `app.main.run_summary_job`. The graph owns the
    done/result write (atomic); this wraps errors -> job error. Cancellation uses the
    existing cooperative flag (summary graph `_guard` checks jobs_store cancel_requested)."""
    print(f"summary_job_running job_id={job_id}", flush=True)
    try:
        from app.domains.jobs.jobs_store import update_job as _uj
        try:
            _uj(job_id, status="running", current_node="Summary")
        except Exception:
            pass
        if SUMMARY_GRAPH is None:
            raise RuntimeError("SUMMARY_GRAPH chưa khởi tạo — kiểm tra logs khởi động.")
        _langgraph_invoke(SUMMARY_GRAPH, {
            "job_id": job_id, "source_names": source_names, "mm_input": mm_input,
            "content_hash": content_hash, "length_mode": length_mode,
            "progress": 0, "current_node": "", "error": None,
        }, thread_id=job_id)
        print(f"summary_job_done job_id={job_id}", flush=True)
    except Exception as e:
        from app.domains.jobs.jobs_store import update_job
        update_job(job_id, status="error", error_text=_job_error_text(e))
        print(f"summary_job_failed job_id={job_id} err={str(e)[:80]}", flush=True)


# -------------------------
# 💬 Conversation Context Layer — controls (Phase B)
# Flag-gated; when disabled the routes report a clear disabled state so the FE can
# degrade gracefully. All handlers fail open — a store error never 500s the chat.
# -------------------------
@app.post('/conversations/<conversation_id>/clear-context')
def clear_conversation_context(conversation_id: str):
    """Clear context: stop using older turns from now on. Keeps messages in the DB."""
    if not _conversation_enabled():
        return jsonify({"ok": False, "error": "conversation_context_disabled"}), 404
    uid, err = _require_app_user()
    if err:
        return err
    enforce = _auth_protect_enabled()
    try:
        from app.domains.conversation import store as _conv
        if enforce and _conv.owner_check(conversation_id, uid) is False:
            return jsonify({"ok": False, "error": "not_found"}), 404  # owner mismatch → no oracle
        reset_at = _conv.set_context_reset(conversation_id, user_id=uid, enforce_owner=enforce)
        if enforce and reset_at is None:
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": True, "conversation_id": conversation_id, "context_reset_at": reset_at})
    except Exception as e:
        logging.exception("[conversation] clear-context failed: %s", e)
        return jsonify({"ok": False, "error": "clear_context_failed"}), 500


@app.delete('/conversations/<conversation_id>')
def delete_conversation(conversation_id: str):
    """Delete chat history: hard-delete this conversation's messages (storage-saving)."""
    if not _conversation_enabled():
        return jsonify({"ok": False, "error": "conversation_context_disabled"}), 404
    uid, err = _require_app_user()
    if err:
        return err
    enforce = _auth_protect_enabled()
    try:
        from app.domains.conversation import store as _conv
        if enforce and _conv.owner_check(conversation_id, uid) is False:
            return jsonify({"ok": False, "error": "not_found"}), 404  # owner mismatch → no oracle
        removed = _conv.delete_messages(conversation_id, user_id=uid, enforce_owner=enforce)
        _conv.soft_delete(conversation_id, user_id=uid, enforce_owner=enforce)
        return jsonify({"ok": True, "conversation_id": conversation_id, "removed": removed})
    except Exception as e:
        logging.exception("[conversation] delete failed: %s", e)
        return jsonify({"ok": False, "error": "delete_failed"}), 500


@app.get('/conversations/<conversation_id>/messages')
def get_conversation_messages(conversation_id: str):
    """Return this conversation's messages (for restoring the chat UI)."""
    if not _conversation_enabled():
        return jsonify({"conversation_id": conversation_id, "messages": []}), 404
    uid, err = _require_app_user()
    if err:
        return err
    enforce = _auth_protect_enabled()
    try:
        from app.domains.conversation import store as _conv
        if enforce and _conv.owner_check(conversation_id, uid) is False:
            return jsonify({"error": "not_found"}), 404  # owner mismatch → no oracle
        msgs = _conv.get_messages(conversation_id, user_id=uid, enforce_owner=enforce)
        return jsonify({"conversation_id": conversation_id, "messages": msgs})
    except Exception as e:
        logging.exception("[conversation] get-messages failed: %s", e)
        return jsonify({"conversation_id": conversation_id, "messages": []}), 500


@app.post("/generate-summary")
def generate_summary():
    data = request.json or {}
    raw_sources = data.get("sources") or []
    if not isinstance(raw_sources, list):
        return jsonify({"error": "Sources phải là list"}), 400

    source_names: list[str] = []
    for item in raw_sources:
        candidate = None
        if isinstance(item, str):
            candidate = item.strip()
        elif isinstance(item, dict):
            for key in ("video", "name", "id", "source", "title"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    candidate = value.strip()
                    break
        if candidate and candidate not in source_names:
            source_names.append(candidate)

    if not source_names:
        return jsonify({"error": "No sources selected"}), 400

    raw_mode = str(data.get("length_mode") or "").strip().lower()
    length_mode = raw_mode if raw_mode in summary_schema.LENGTH_MODES else "medium"

    force = bool(data.get("force"))
    try:
        mm_input, content_hash = _summary_input_and_hash(source_names, length_mode)
    except Exception as e:
        return jsonify({"error": f"Không đọc được dữ liệu nguồn: {e}"}), 500
    if not mm_input.get("chunks"):
        return jsonify({"error": "Nguồn chưa có dữ liệu đã index"}), 400
    if not force:
        cached = summary_store.get_by_hash(content_hash)
        if cached:
            # Cache hit KHÔNG có job_id — FE phải branch theo status="done" trước (aec6017)
            return jsonify({"status": "done", "result": cached, "cached": True}), 200
    job_id = _start_summary_job(source_names, mm_input, content_hash, length_mode)
    return jsonify({"job_id": job_id, "status": "started"}), 202


@app.get("/summary-status/<job_id>")
def summary_status(job_id: str):
    from app.domains.jobs.jobs_store import get_job as _js_get
    j = _js_get(job_id)
    if not j or j.get("job_type") not in ("summary", None):
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": j.get("status"),
        "progress": j.get("progress", 0),
        "current_node": j.get("current_node") or "",
        "result": j.get("result"),
        "error": j.get("error"),
    }), 200


@app.post("/summary-cancel/<job_id>")
def summary_cancel(job_id: str):
    from app.domains.jobs.jobs_store import request_cancel
    request_cancel(job_id)
    return jsonify({"ok": True}), 200


@app.route('/summaries', methods=['OPTIONS'])
def summaries_options():
    return jsonify({"ok": True}), 200


@app.get('/summaries')
def list_summaries():
    return jsonify({"summaries": summary_store.list_records()})


@app.delete('/summaries/<string:summary_id>')
def delete_summary(summary_id: str):
    if not summary_store.delete_record(summary_id):
        return jsonify({"error": "Summary not found"}), 404
    return jsonify({"message": "Deleted", "removed": 1})



# -------------------------
# 🗑️ Delete source
# -------------------------
@app.post('/delete-source')
def delete_source():
    uid, err = _require_app_user()
    if err:
        return err
    data = request.json or {}
    video_name = data.get('video', '')
    if not video_name:
        return jsonify({'error': 'Missing video name'}), 400

    target_stem = _normalize_video_stem(video_name)
    # Owner scope: cannot delete another user's source (404, no oracle).
    if _auth_protect_enabled() and not _source_owner_ok(target_stem, uid):
        return jsonify({'error': 'Source not found'}), 404
    meta_path = INDEX_META_JSON_PATH
    if not meta_path.exists():
        return jsonify({'error': 'No index metadata found'}), 404

    try:
        with open(meta_path, encoding='utf-8') as f:
            meta = json.load(f)

        # Khớp theo canonical stem DÙNG CHUNG (không glob prefix → không xóa nhầm).
        stored_names = set()
        removed_total = 0
        for v in meta.values():
            if not isinstance(v, dict):
                continue
            stem = _normalize_video_stem(v.get('source_stem') or v.get('video') or '')
            if stem and stem == target_stem:
                stored_names.add(v.get('video', ''))
                removed_total += 1

        if not stored_names:
            return jsonify({'message': 'No matching source found', 'removed': 0})

        for stored in stored_names:
            try:
                delete_source_from_index(stored)
            except Exception as e:
                print("delete_source_from_index failed:", stored, e)

        # Cache Redis: index_version (mtime index.json) đã đổi nên entry cũ tự orphan;
        # xoá chủ động thêm cho chắc (best-effort, fail-open).
        try:
            llm_cache.invalidate_all()
        except Exception as e:
            print("cache invalidate failed:", e)

        # Xóa file video vật lý CHÍNH XÁC theo path đã lưu (KHÔNG glob).
        for stored in stored_names:
            for cand in {stored, os.path.join(VIDEOS_DIR, os.path.basename(stored or ''))}:
                try:
                    pf = Path(cand)
                    if cand and pf.is_file():
                        pf.unlink()
                except Exception as e:
                    print("Could not delete video file:", cand, e)

        # Dọn registry: bỏ entry cùng canonical stem (+ xóa file input gốc).
        try:
            reg = _load_source_registry()
            to_del = [sid for sid, info in reg.items()
                      if _normalize_video_stem(info.get('source_stem') or info.get('filename') or '') == target_stem]
            for sid in to_del:
                ip = reg[sid].get('input_path')
                if ip:
                    try:
                        if Path(ip).is_file():
                            Path(ip).unlink()
                    except Exception:
                        pass
                reg.pop(sid, None)
            if to_del:
                _save_source_registry(reg)
        except Exception as e:
            print("registry cleanup failed:", e)

        try:
            mindmap_store.delete_by_source(target_stem)
        except Exception as e:
            print("mindmap store cleanup failed:", e)

        try:
            summary_store.delete_by_source(target_stem)
        except Exception as e:
            print("summary store cleanup failed:", e)

        return jsonify({'message': 'Deleted', 'removed': removed_total})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


def _mindmap_input_and_hash(source_names: list[str]) -> tuple[dict, str]:
    mm = collect_mindmap_input(INDEX_META_JSON_PATH, source_names)
    h = mindmap_schema.content_hash(mm.get("sources") or [],
                                    [c["text"] for c in mm.get("chunks") or []],
                                    [c.get("heading_path", "") for c in mm.get("chunks") or []])
    return mm, h


def _start_mindmap_job(source_names: list[str], mm_input: dict, content_hash: str) -> str:
    """Phase 5 Step 3: dispatch Mindmap v3 via enqueue_job — daemon thread when
    QUEUE_ENABLED=false (default, unchanged), RQ 'mindmap' queue when true. FE polling
    (/mindmap-status) unchanged; result still in mindmap_store."""
    job_id = str(uuid.uuid4())
    from app.domains.jobs.jobs_store import create_job
    create_job(job_id, job_type="mindmap", status="pending", progress=0, current_node="Queued", user_id=_current_user_id())
    from app.jobs.queue import enqueue_job
    res = enqueue_job(run_mindmap_job,
                      args=(job_id, source_names, mm_input, content_hash),
                      queue="mindmap", job_id=job_id)
    _event = {"rq": "mindmap_enqueue_rq", "thread": "mindmap_enqueue_thread",
              "thread_fallback": "mindmap_queue_fallback_thread"}.get(res.get("mode"),
                                                                      f"mindmap_enqueue_{res.get('mode')}")
    print(f"{_event} job_id={job_id}", flush=True)
    return job_id


def run_mindmap_job(job_id: str, source_names: list[str], mm_input: dict, content_hash: str) -> None:
    """Mindmap v3 execution body. Runs in a daemon thread (QUEUE_ENABLED=false) OR an RQ
    worker process (QUEUE_ENABLED=true) — identical behaviour, no Flask request context
    needed. Enqueued by dotted path `app.main.run_mindmap_job`. The graph owns the
    done/result write (atomic); this wraps errors -> job error. Cancellation uses the
    existing cooperative flag (mindmap graph `_guard` checks jobs_store cancel_requested)."""
    print(f"mindmap_job_running job_id={job_id}", flush=True)
    try:
        from app.domains.jobs.jobs_store import update_job as _uj
        try:
            _uj(job_id, status="running", current_node="Mindmap")
        except Exception:
            pass
        if MINDMAP_GRAPH is None:
            raise RuntimeError("MINDMAP_GRAPH chưa khởi tạo — kiểm tra logs khởi động.")
        _langgraph_invoke(MINDMAP_GRAPH, {
            "job_id": job_id, "source_names": source_names, "mm_input": mm_input,
            "content_hash": content_hash, "progress": 0, "current_node": "", "error": None,
        }, thread_id=job_id)
        print(f"mindmap_job_done job_id={job_id}", flush=True)
    except Exception as e:
        from app.domains.jobs.jobs_store import update_job
        update_job(job_id, status="error", error_text=_job_error_text(e))
        print(f"mindmap_job_failed job_id={job_id} err={str(e)[:80]}", flush=True)


# -------------------------
@app.post("/generate-mindmap")
def generate_mindmap():
    data = request.json or {}
    raw_sources = data.get("sources") or []
    if not isinstance(raw_sources, list):
        return jsonify({"error": "Sources phải là list"}), 400

    source_names: list[str] = []
    for item in raw_sources:
        candidate = None
        if isinstance(item, str):
            candidate = item.strip()
        elif isinstance(item, dict):
            for key in ("video", "name", "id", "source", "title"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    candidate = value.strip()
                    break
        if candidate:
            if candidate not in source_names:
                source_names.append(candidate)

    if not source_names:
        return jsonify({"error": "No sources selected"}), 400

    force = bool(data.get("force"))
    try:
        mm_input, content_hash = _mindmap_input_and_hash(source_names)
    except Exception as e:
        return jsonify({"error": f"Không đọc được dữ liệu nguồn: {e}"}), 500
    if not mm_input.get("chunks"):
        return jsonify({"error": "Nguồn chưa có dữ liệu đã index"}), 400
    if not force:
        cached = mindmap_store.get_by_hash(content_hash)
        if cached:
            return jsonify({"status": "done", "result": cached, "cached": True}), 200
    job_id = _start_mindmap_job(source_names, mm_input, content_hash)
    return jsonify({"job_id": job_id, "status": "started"}), 202


@app.get("/mindmap-status/<job_id>")
def mindmap_status(job_id: str):
    from app.domains.jobs.jobs_store import get_job as _js_get
    j = _js_get(job_id)
    if not j or j.get("job_type") not in ("mindmap", None):
        return jsonify({"error": "Job not found"}), 404
    result = j.get("result")
    payload: Dict[str, Any] = {
        "status": j.get("status"),
        "progress": j.get("progress", 0),
        # current_node để FE hiện đúng giai đoạn + stall-fingerprint bắt được
        # chuyển node dù progress % đứng yên (codex #6).
        "current_node": j.get("current_node") or "",
        "result": result,
        "error": j.get("error"),
    }
    # Passthrough preview khi đang chạy (Skeleton node ghi result={"partial": {...}}).
    if j.get("status") == "running" and isinstance(result, dict) and "partial" in result:
        payload["partial"] = result["partial"]
    return jsonify(payload), 200


@app.post("/mindmap-cancel/<job_id>")
def mindmap_cancel(job_id: str):
    from app.domains.jobs.jobs_store import request_cancel
    request_cancel(job_id)
    print(f"mindmap_queue_cancel_requested job_id={job_id}", flush=True)
    return jsonify({"ok": True}), 200


@app.get("/chunk-text/<int:chunk_id>")
def get_chunk_text(chunk_id: int):
    from app.domains.vectorstore import chunk_text_store
    text = chunk_text_store.get_text(chunk_id)
    if text is None:
        return jsonify({"error": "Chunk not found"}), 404
    return jsonify({"chunk_id": chunk_id, "text": text}), 200


@app.get('/mindmaps')
def list_mindmaps():
    return jsonify({"mindmaps": mindmap_store.list_records()})


@app.route("/mindmaps/<mindmap_id>", methods=["PUT"])
def update_mindmap(mindmap_id: str):
    """Lưu bản chỉnh sửa tay từ viewer. Bảo vệ id/hash/created_at/sources gốc."""
    base = mindmap_store.get_record(mindmap_id)
    if not base:
        return jsonify({"error": "Mind map not found"}), 404

    body = request.get_json(silent=True) or {}
    from services.mindmap.pipeline.schema import sanitize_nodes, validate_relations

    nodes = sanitize_nodes(body.get("nodes") or [])
    if not nodes:
        return jsonify({"error": "nodes trống hoặc không hợp lệ"}), 400

    relations = validate_relations(body.get("relations") or [], nodes)
    record = {
        **base,
        "title": (str(body.get("title") or "").strip() or base.get("title") or ""),
        "nodes": nodes,
        "relations": relations,
    }
    for key in ("id", "content_hash", "created_at", "sources", "schema_version"):
        record[key] = base.get(key)

    record["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    generator = dict(base.get("generator") or {})
    generator["edited"] = True
    record["generator"] = generator

    mindmap_store.save_record(record)
    return jsonify(record)


@app.delete('/mindmaps/<string:mindmap_id>')
def delete_mindmap(mindmap_id: str):
    if not mindmap_store.delete_record(mindmap_id):
        return jsonify({"error": "Mind map not found"}), 404
    return jsonify({"message": "Deleted"})


# -------------------------
# 🔍 Memory Tree Status
# -------------------------
@app.get('/memory-tree-status')
def memory_tree_status():
    """
    Kiểm tra trạng thái Memory Tree cho các source.
    Trả về danh sách source với status chi tiết: "none" | "building" | "completed"
    """
    try:
        from app.domains.memory.tree import _load_memory_trees, _normalize_video_stem        
        trees = _load_memory_trees()
        tree_map = {}
        for t in trees:
            stem = _normalize_video_stem(t.get("source_stem", ""))
            if stem:
                tree_map[stem] = {
                    "status": t.get("status", "completed"),  # building | completed
                    "built_at": t.get("built_at"),
                    "num_nodes": len(t.get("nodes", [])),
                }
        
        # Lấy danh sách tất cả sources từ index
        with open(INDEX_META_JSON_PATH, encoding='utf-8') as f:
            meta = json.load(f)
        
        all_sources = set()
        for item in meta.values():
            video = item.get("video", "").strip()
            if video:
                stem = _normalize_video_stem(video)
                if stem:
                    all_sources.add(stem)
        
        status_list = []
        for source in sorted(all_sources):
            tree_info = tree_map.get(source)
            if tree_info:
                status_list.append({
                    "source": source,
                    "status": tree_info["status"],  # building | completed
                    "built_at": tree_info.get("built_at"),
                    "num_nodes": tree_info.get("num_nodes", 0),
                })
            else:
                status_list.append({
                    "source": source,
                    "status": "none",  # Chưa có tree
                    "built_at": None,
                    "num_nodes": 0,
                })
        
        return jsonify({
            "sources": status_list,
            "total_sources": len(all_sources),
            "sources_with_tree": len(tree_map),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# -------------------------
# 🗑️ Delete Source Helper Functions
# -------------------------

def _delete_input_file(source_id: str, source_info: Dict) -> bool:
    """
    Xóa file gốc trong input_docs/.
    Returns True nếu xóa thành công hoặc file không tồn tại.
    """
    # Ưu tiên input_path (đường lưu vật lý THẬT, đã sanitize/chống trùng); fallback
    # INPUT_DIR/filename cho entry cũ. Tránh sót file gốc khi tên bị sanitize/đổi.
    candidates = []
    ip = source_info.get("input_path")
    if ip:
        candidates.append(Path(ip))
    filename = source_info.get("filename")
    if filename:
        candidates.append(Path(INPUT_DIR) / filename)
    if not candidates:
        return True

    ok = True
    for input_file_path in candidates:
        try:
            if input_file_path.exists():
                input_file_path.unlink()
                print(f"🗑️ [Delete] Đã xóa input file: {input_file_path}")
        except Exception as e:
            print(f"⚠️ [Delete] Không thể xóa input file {input_file_path}: {e}")
            ok = False
    return ok


def _delete_videos(source_id: str, source_stem: str) -> int:
    """
    Xóa tất cả video liên quan trong videos/.
    Match theo source_stem hoặc source_id.
    Returns số lượng video đã xóa.
    """
    videos_root = Path(VIDEOS_DIR)
    if not videos_root.exists():
        return 0
    
    deleted_count = 0

    # Khớp CHÍNH XÁC theo canonical stem từng file (không glob '{stem}*' prefix —
    # tránh xóa nhầm "report" ↔ "report2", và miễn nhiễm hoa/thường giữa các OS).
    for video_file in videos_root.iterdir():
        try:
            if _normalize_video_stem(video_file.name) != source_stem:
                continue
            if video_file.is_file():
                video_file.unlink()
                deleted_count += 1
                print(f"🗑️ [Delete] Đã xóa video: {video_file}")
            elif video_file.is_dir():
                import shutil
                shutil.rmtree(video_file, ignore_errors=True)
                deleted_count += 1
                print(f"🗑️ [Delete] Đã xóa thư mục video: {video_file}")
        except Exception as e:
            print(f"⚠️ [Delete] Không thể xóa video {video_file}: {e}")

    return deleted_count


def _purge_chunk_index(source_stem: str) -> int:
    """
    Xóa tất cả chunks thuộc source từ index.
    Returns số lượng chunks đã xóa.
    """
    try:
        deleted_count = delete_chunks_by_source(source_stem)
        print(f"🗑️ [Delete] Đã xóa {deleted_count} chunks từ index")
        return deleted_count
    except Exception as e:
        print(f"⚠️ [Delete] Lỗi khi xóa chunks: {e}")
        raise


def _purge_memory_tree(source_stem: str) -> int:
    """
    Xóa toàn bộ memory nodes thuộc source.
    Returns số lượng nodes đã xóa.
    """
    try:
        deleted_nodes = delete_memory_tree_by_source(source_stem)
        print(f"🗑️ [Delete] Đã xóa {deleted_nodes} memory nodes")
        
        # Rebuild memory index sau khi xóa
        rebuild_memory_index()
        print(f"🔄 [Delete] Đã rebuild memory_index")
        
        return deleted_nodes
    except Exception as e:
        print(f"⚠️ [Delete] Lỗi khi xóa memory tree: {e}")
        raise


def _delete_registry_entry(source_id: str) -> bool:
    """
    Xóa entry khỏi source_registry.json.
    Returns True nếu thành công.
    """
    try:
        registry = _load_source_registry()
        if source_id in registry:
            del registry[source_id]
            _save_source_registry(registry)
            print(f"🗑️ [Delete] Đã xóa registry entry cho source_id: {source_id}")
            return True
        return True  # Không có entry, coi như OK
    except Exception as e:
        print(f"⚠️ [Delete] Lỗi khi xóa registry entry: {e}")
        raise


def _validate_source_exists(source_id: str, source_stem: str) -> Tuple[bool, Optional[Dict]]:
    """
    Validate source có tồn tại không.
    Returns (exists, source_info) từ registry.
    """
    # Kiểm tra trong registry trước
    registry = _load_source_registry()
    source_info = registry.get(source_id)
    
    if source_info:
        return True, source_info
    
    # Nếu không có trong registry, kiểm tra trong index
    index_path = INDEX_META_JSON_PATH
    if index_path.exists():
        try:
            with open(index_path, encoding="utf-8") as f:
                meta = json.load(f)
            for item in meta.values():
                video = (item.get("video") or "").strip()
                if _normalize_video_stem(video) == source_stem:
                    return True, None  # Tồn tại nhưng không có trong registry
        except Exception:
            pass
    
    # Kiểm tra trong memory_trees.json
    try:
        from app.domains.memory.tree import _load_memory_trees
        trees = _load_memory_trees()
        for t in trees:
            stem = _normalize_video_stem(t.get("source_stem", ""))
            if stem == source_stem:
                return True, None
    except Exception:
        pass
    
    return False, None


@app.delete('/sources/<source_id>')
def delete_source_v2(source_id: str):
    """
    Xóa toàn bộ dữ liệu liên quan tới một source (clean delete).
    
    Xóa:
    1. File gốc trong input_docs/
    2. Video QR trong videos/
    3. Chunk metadata và vectors trong index/
    4. Memory nodes và vectors trong memory/
    5. Registry entry trong data/source_registry.json
    
    Đảm bảo atomicity và rebuild indexes sau khi xóa.
    """
    uid, err = _require_app_user()
    if err:
        return err
    source_id = (source_id or "").strip()
    if not source_id:
        return jsonify({"error": "Missing source_id"}), 400

    source_stem = _normalize_video_stem(source_id)

    # 1️⃣ VALIDATE: Kiểm tra source có tồn tại không
    exists, source_info = _validate_source_exists(source_id, source_stem)
    if not exists:
        return jsonify({"error": "Source not found"}), 404
    # Owner scope: a foreign source is treated as not found (no oracle, no cross-user delete).
    if _auth_protect_enabled() and (not source_info or source_info.get("user_id") != uid):
        return jsonify({"error": "Source not found"}), 404

    # source_id là UUID, KHÔNG phải stem. Lấy stem THẬT từ registry entry để purge
    # chunk/video/memory đúng nguồn (nếu không sẽ xóa sót do stem sai từ UUID).
    if source_info:
        _real_stem = _normalize_video_stem(source_info.get("source_stem") or source_info.get("filename") or "")
        if _real_stem:
            source_stem = _real_stem

    # Kiểm tra nếu source đang processing (cho phép xóa nhưng log warning)
    if source_info and source_info.get("status") == "processing":
        print(f"⚠️ [Delete] Source {source_id} đang processing, vẫn tiếp tục xóa")
        # Background task sẽ fail gracefully khi cố update registry entry đã bị xóa
    
    # Chuẩn bị backup để rollback nếu lỗi
    backups = []
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    
    try:
        # Backup các file quan trọng
        critical_files = [
            INDEX_META_JSON_PATH,
            INDEX_FAISS_PATH,
            MEMORY_DIR / "memory_trees.json",
            MEMORY_DIR / "memory_index.faiss",
            MEMORY_DIR / "memory_index.json",
            SOURCE_REGISTRY_PATH,
        ]
        
        import shutil
        from datetime import datetime
        backup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        for file_path in critical_files:
            if file_path.exists():
                backup_path = backup_dir / f"{file_path.name}.{backup_timestamp}.bak"
                shutil.copy2(file_path, backup_path)
                backups.append((file_path, backup_path))
                print(f"💾 [Delete] Đã backup: {file_path.name}")
        
        # 2️⃣ DELETE FILE SYSTEM
        input_file_deleted = _delete_input_file(source_id, source_info or {})
        videos_deleted = _delete_videos(source_id, source_stem)
        
        # 3️⃣ DELETE INDEX (CHUNK LEVEL)
        chunks_removed = _purge_chunk_index(source_stem)
        
        # 4️⃣ DELETE MEMORY TREE
        memory_nodes_removed = _purge_memory_tree(source_stem)
        
        # 5️⃣ DELETE REGISTRY
        registry_deleted = _delete_registry_entry(source_id)

        # 6️⃣ DELETE MINDMAPS liên quan tới source (best-effort)
        try:
            mindmap_store.delete_by_source(source_stem)
        except Exception as e:
            print("mindmap store cleanup failed:", e)

        # Nếu mọi thứ OK -> cleanup backup
        for orig, bak in backups:
            try:
                if bak.exists():
                    bak.unlink()
            except Exception:
                pass  # Best effort cleanup
        
        print(f"✅ [Delete] Hoàn thành xóa source {source_id}")
        
        return jsonify({
            "status": "deleted",
            "source_id": source_id,
            "deleted_items": {
                "input_file": input_file_deleted,
                "videos": videos_deleted,
                "chunks_removed": chunks_removed,
                "memory_nodes_removed": memory_nodes_removed,
            }
        })
    
    except Exception as e:
        # Rollback best-effort
        import traceback
        traceback.print_exc()
        print(f"❌ [Delete] Lỗi khi xóa source {source_id}, đang rollback...")
        
        for orig, bak in backups:
            try:
                if bak.exists() and orig.exists() is False:
                    bak.replace(orig)
                    print(f"🔄 [Delete] Đã rollback: {orig.name}")
            except Exception as rb_err:
                print(f"⚠️ [Delete] Rollback error cho {orig}: {rb_err}")
        
        return jsonify({"error": f"Delete failed: {str(e)}"}), 500
@app.get('/memory-tree/<source_stem>')
def get_memory_tree(source_stem: str):
    """
    Lấy Memory Tree cho một source cụ thể.
    Trả về tree với nodes (có thể là partial nếu đang building).
    """
    try:
        from app.domains.memory.tree import _load_memory_trees, _normalize_video_stem        
        norm_stem = _normalize_video_stem(source_stem)
        trees = _load_memory_trees()
        
        for tree in trees:
            if _normalize_video_stem(tree.get("source_stem", "")) == norm_stem:
                # Trả về tree với nodes đã filter theo type nếu cần
                nodes = tree.get("nodes", [])
                doc_node = next((n for n in nodes if n.get("type") == "document"), None)
                section_nodes = [n for n in nodes if n.get("type") == "section"]
                
                return jsonify({
                    "source_stem": tree.get("source_stem"),
                    "status": tree.get("status", "completed"),
                    "built_at": tree.get("built_at"),
                    "document": doc_node,  # Document node (nếu có)
                    "sections": section_nodes,  # Danh sách section nodes
                    "total_nodes": len(nodes),
                })
        
        return jsonify({
            "source_stem": norm_stem,
            "status": "none",
            "document": None,
            "sections": [],
            "total_nodes": 0,
        }), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    debug_env = (os.environ.get("DEBUG", "0") or "").strip().lower()
    debug = debug_env in {"1", "true", "yes", "y", "on"}
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=debug)
