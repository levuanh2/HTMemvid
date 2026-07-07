"""Phase 5 — RQ job queue abstraction (Step 1: ingest only).

RQ is TRANSPORT only; job STATUS/result stays in jobs.sqlite so the FE polling
contract is unchanged. Everything is behind QUEUE_ENABLED (default OFF): flag off
reproduces today's daemon-thread behaviour exactly. All Redis/RQ access is lazy and
fail-open — a broken queue never blocks work (falls back to a thread).
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

# Active queues. Step 1: ingest. Step 2: + summary. (mindmap later.)
QUEUE_NAMES = ("ingest", "summary")


def queue_enabled() -> bool:
    return (os.getenv("QUEUE_ENABLED", "false") or "").strip().lower() in ("1", "true", "yes", "on")


def _qlog(event: str, **kv: object) -> None:
    parts = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"queue {event} {parts}".rstrip(), flush=True)


def _redis_url() -> str:
    return (os.getenv("REDIS_URL") or "").strip()


def _conn():
    """A redis.Redis connection for RQ (separate from the fail-open cache client)."""
    import redis  # lazy: only needed when the queue is used
    return redis.from_url(_redis_url() or "redis://redis:6379/0")


def get_queue(name: str = "ingest"):
    from rq import Queue  # lazy: rq only required in queue mode
    return Queue(name, connection=_conn())


def _job_timeout() -> int:
    try:
        return int(os.getenv("RQ_JOB_TIMEOUT_SEC", "1800"))
    except ValueError:
        return 1800


def enqueue_job(func, args=(), queue: str = "ingest", job_id: str | None = None) -> dict:
    """Single switch point for heavy background work.

    QUEUE_ENABLED=false -> run in a daemon thread (today's behaviour).
    QUEUE_ENABLED=true  -> enqueue on RQ; on ANY enqueue failure fall back to a
                           thread (fail-safe, so uploads keep working if Redis/RQ is down).
    """
    args = tuple(args)
    if not queue_enabled():
        threading.Thread(target=func, args=args, daemon=True).start()
        _qlog("enqueue_thread", queue=queue, mode="disabled", job_id=job_id)
        return {"mode": "thread"}
    try:
        q = get_queue(queue)
        q.enqueue(func, *args, job_timeout=_job_timeout(), job_id=job_id,
                  result_ttl=3600, failure_ttl=86400)
        _qlog("enqueue_rq", queue=queue, job_id=job_id)
        return {"mode": "rq"}
    except Exception as exc:  # noqa: BLE001 — never let a broken queue block work
        _qlog("enqueue_failed_fallback_thread", queue=queue, job_id=job_id, err=str(exc)[:80])
        threading.Thread(target=func, args=args, daemon=True).start()
        return {"mode": "thread_fallback", "error": str(exc)[:120]}


def queue_stats() -> dict:
    """Queue depth view for /stats and /ready. Per-queue breakdown + aggregate totals.
    Fail-open (Redis down -> zeros + error). Aggregate keys (queued_count/started_count/
    failed_count/worker_count) are kept for /ready and backward compatibility."""
    stats: dict = {
        "enabled": queue_enabled(), "queued_count": 0, "started_count": 0,
        "failed_count": 0, "worker_count": 0, "oldest_queued_age_sec": None,
    }
    for name in QUEUE_NAMES:
        stats[name] = {"queued": 0, "started": 0, "failed": 0}
    if not queue_enabled():
        return stats
    try:
        from rq import Queue, Worker
        from rq.registry import StartedJobRegistry, FailedJobRegistry
        conn = _conn()
        oldest = None
        for name in QUEUE_NAMES:
            q = Queue(name, connection=conn)
            qn = int(q.count)
            sn = int(StartedJobRegistry(name, connection=conn).count)
            fn = int(FailedJobRegistry(name, connection=conn).count)
            stats[name] = {"queued": qn, "started": sn, "failed": fn}
            stats["queued_count"] += qn
            stats["started_count"] += sn
            stats["failed_count"] += fn
            ids = q.job_ids
            if ids:
                j = q.fetch_job(ids[0])
                if j is not None and getattr(j, "enqueued_at", None):
                    enq = j.enqueued_at
                    if enq.tzinfo is None:
                        enq = enq.replace(tzinfo=timezone.utc)
                    age = round((datetime.now(timezone.utc) - enq).total_seconds(), 1)
                    oldest = age if oldest is None else max(oldest, age)
        stats["oldest_queued_age_sec"] = oldest
        try:
            stats["worker_count"] = int(Worker.count(connection=conn))
        except Exception:
            pass
    except Exception as exc:  # noqa: BLE001
        stats["error"] = str(exc)[:80]
    return stats


def _live_job_ids() -> "set[str] | None":
    """Job ids visible in RQ registries (queued + started + deferred).
    Returns None if RQ/Redis is unreachable (caller must then fail safe)."""
    try:
        from rq import Queue
        from rq.registry import StartedJobRegistry, DeferredJobRegistry
        conn = _conn()
        live: set[str] = set()
        for name in QUEUE_NAMES:
            live.update(Queue(name, connection=conn).job_ids)
            live.update(StartedJobRegistry(name, connection=conn).get_job_ids())
            live.update(DeferredJobRegistry(name, connection=conn).get_job_ids())
        return live
    except Exception as exc:  # noqa: BLE001
        _qlog("reconcile_rq_unavailable", err=str(exc)[:80])
        return None


def reconcile_interrupted() -> dict:
    """Mark orphaned active jobs interrupted on restart/shutdown — queue-aware.

    QUEUE_ENABLED=false -> today's behaviour: mark ALL active jobs interrupted
                           (single process; its threads all died).
    QUEUE_ENABLED=true  -> registry-aware: only mark jobs that are NOT visible in
                           RQ's queued/started/deferred registries. A live worker's
                           job (in StartedJobRegistry) and queued jobs are preserved.
                           If RQ is unreachable -> FAIL SAFE: touch nothing.
    """
    from app.domains.jobs import jobs_store
    if not queue_enabled():
        jobs_store.mark_interrupted_jobs()
        _qlog("reconcile_mark_all", mode="disabled")
        return {"mode": "mark_all"}
    live = _live_job_ids()
    if live is None:
        return {"mode": "skipped"}  # RQ down -> don't corrupt live state
    active = jobs_store.list_active_jobs()
    stale = [jid for (jid, _t) in active if jid not in live]
    for jid in stale:
        try:
            jobs_store.update_job(jid, status="interrupted", current_node="Reconciled")
        except Exception:
            pass
    _qlog("reconcile_registry_aware", live=len(live), active=len(active), interrupted=len(stale))
    return {"mode": "registry", "interrupted": stale, "live_count": len(live)}
