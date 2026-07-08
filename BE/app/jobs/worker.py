"""Phase 5 — RQ worker entrypoint (Step 1: ingest; Step 2: + summary; Step 3: + mindmap).

Run:  python -m app.jobs.worker

Uses RQ SimpleWorker (no per-job fork) so heavy models (bge-m3, ~2GB) stay warm in
the process across jobs — a forking Worker would reload them every job. The job
functions themselves (e.g. app.main._run_ingest_job) build/reuse the LangGraph
pipelines on first import. Job status/results go to the shared jobs.sqlite
(JOBS_DB_PATH on the shared volume), so the web process and FE see them.
"""
from __future__ import annotations

import os


def _queue_names() -> list[str]:
    # Step 1: ingest. Step 2: + summary. Step 3: + mindmap.
    raw = (os.getenv("RQ_QUEUES") or "ingest,summary,mindmap").strip()
    return [n.strip() for n in raw.split(",") if n.strip()] or ["ingest", "summary", "mindmap"]


def main() -> None:
    from redis import from_url
    from rq import Queue, SimpleWorker

    url = (os.getenv("REDIS_URL") or "redis://redis:6379/0").strip()
    conn = from_url(url)
    queues = [Queue(n, connection=conn) for n in _queue_names()]
    print(f"[rq-worker] starting SimpleWorker queues={_queue_names()} redis={url}", flush=True)
    SimpleWorker(queues, connection=conn).work(with_scheduler=False)


if __name__ == "__main__":
    main()
