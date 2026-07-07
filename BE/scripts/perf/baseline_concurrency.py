#!/usr/bin/env python3
"""MemVid concurrency baseline — Phase 1, OBSERVATION ONLY.

Measures the current system under concurrent load BEFORE any semaphore /
single-flight change. Pure stdlib (urllib + threads), no new dependencies.

It does NOT touch production request logic. It only:
  - health-checks the stack,
  - fires N concurrent /query jobs and polls /query-status to completion,
  - records latency percentiles, empty/error/timeout/429/5xx counts,
  - snapshots /stats cache counters (delta) as a cache hit/miss clue,
  - snapshots the backend node_logs GenerateAnswer count (delta) as the
    ground-truth number of actual LLM generations (single-flight evidence).

Config via env (all optional):
  BASE_URL              default http://localhost:8080
  CONCURRENCY           default 10           (in-flight workers)
  TOTAL_REQUESTS        default = CONCURRENCY
  QUESTION_MODE         identical | distinct | vietnamese_paraphrase   (default identical)
  SCENARIO              all | health | <QUESTION_MODE>   (default: run the full matrix)
  POLL_INTERVAL_SECONDS default 1.0
  TIMEOUT_SECONDS       default 360          (per-request end-to-end deadline)
  SOURCES               comma list of source stems (default: empty = all docs)
  RETRY_ON_429          1 to retry admission 429 until deadline (default 1)
  COMPOSE               docker compose invocation (default "docker compose")
  REPORT_DIR            default <repo>/reports/performance

Run:
  python BE/scripts/perf/baseline_concurrency.py                # full matrix
  QUESTION_MODE=identical CONCURRENCY=10 SCENARIO=identical \
      python BE/scripts/perf/baseline_concurrency.py            # one scenario
  SCENARIO=health python BE/scripts/perf/baseline_concurrency.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

# ---------------------------------------------------------------- config
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SECONDS", "1.0"))
TIMEOUT_SECONDS = float(os.getenv("TIMEOUT_SECONDS", "360"))
SOURCES = [s.strip() for s in (os.getenv("SOURCES") or "").split(",") if s.strip()]
RETRY_ON_429 = (os.getenv("RETRY_ON_429", "1").strip() not in ("0", "false", "False"))
COMPOSE = os.getenv("COMPOSE", "docker compose")
_REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = Path(os.getenv("REPORT_DIR", str(_REPO_ROOT / "reports" / "performance")))

# Diagnostic fallback text the pipeline returns (NOT cached) when the model gives nothing.
_FALLBACK_MARKERS = ("Không nhận được phản hồi", "Không có phản hồi")

QUESTIONS = {
    "identical": ["noi dung la gi"],
    "vietnamese_paraphrase": [
        "noi dung la gi",
        "nội dung là gì",
        "nọi dung là gì",
        "tài liệu có nội dung gì",
        "file này nói về gì",
    ],
    "distinct": [
        "nội dung chính của tài liệu là gì",
        "tài liệu này nói về chủ đề gì",
        "tóm tắt tài liệu giúp tôi",
        "các ý chính trong tài liệu là gì",
        "tài liệu đề cập đến vấn đề gì",
        "mục đích của tài liệu là gì",
        "tài liệu này dành cho ai",
        "có những phần nào trong tài liệu",
        "kết luận của tài liệu là gì",
        "tài liệu trình bày những gì",
        "điểm quan trọng nhất của tài liệu là gì",
        "tài liệu giải thích khái niệm gì",
    ],
}


# ---------------------------------------------------------------- http helpers
def _http(method: str, path: str, body: dict | None = None, timeout: float = 30.0):
    """Return (status_code:int|None, parsed_json|None, error_str|None)."""
    url = path if path.startswith("http") else BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw), None
            except json.JSONDecodeError:
                return resp.status, {"_raw": raw}, None
    except urlerror.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            parsed = {"_raw": raw}
        return e.code, parsed, None
    except Exception as e:  # noqa: BLE001 — connection refused, timeout, etc.
        return None, None, f"{type(e).__name__}: {e}"


def _compose_exec(service: str, argv: list[str], timeout: float = 20.0):
    """Run `docker compose exec -T <service> <argv...>`; return stdout str or None."""
    cmd = COMPOSE.split() + ["exec", "-T", service] + argv
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def _gen_count() -> int | None:
    """Ground-truth count of GenerateAnswer node executions from backend logs.sqlite."""
    py = (
        "import sqlite3;"
        "c=sqlite3.connect('/app/logs.sqlite');"
        "print(c.execute(\"select count(*) from node_logs where node='GenerateAnswer'\")"
        ".fetchone()[0])"
    )
    out = _compose_exec("backend", ["python", "-c", py])
    if out is None:
        return None
    try:
        return int(out.split()[-1])
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------- one request
def _classify(answer: str, error: str, submit_status, job_status: str, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if submit_status == 429:
        return "rate_limited"
    if submit_status is not None and submit_status >= 500:
        return "http_5xx"
    if submit_status is None:
        return "error"
    if job_status == "error" or (error and not answer):
        return "error"
    a = (answer or "").strip()
    if not a:
        return "empty"
    if any(m in a for m in _FALLBACK_MARKERS):
        return "fallback"  # non-empty but the model produced nothing usable
    return "success"


def run_one(question: str) -> dict:
    """Submit one query, poll to terminal state, return a metrics record."""
    t0 = time.time()
    deadline = t0 + TIMEOUT_SECONDS
    session_id = str(uuid.uuid4())  # fresh session => standalone => cacheable
    body = {"q": question, "sources": SOURCES, "use_memory_tree": True, "session_id": session_id}

    rl_hits = 0
    submit_status = None
    job_id = None
    while time.time() < deadline:
        submit_status, payload, err = _http("POST", "/query", body, timeout=30.0)
        if submit_status == 202 and payload:
            job_id = payload.get("job_id")
            break
        if submit_status == 429:
            rl_hits += 1
            if not RETRY_ON_429:
                break
            time.sleep(0.5)
            continue
        break  # 4xx/5xx/connection error — stop, record it

    if not job_id:
        latency = (time.time() - t0) * 1000.0
        return {
            "question": question, "latency_ms": latency, "submit_status": submit_status,
            "job_status": None, "rate_limited_hits": rl_hits,
            "outcome": _classify("", "", submit_status, "", False),
        }

    # poll
    job_status, answer, error, timed_out = "pending", "", "", False
    while True:
        if time.time() >= deadline:
            timed_out = True
            break
        st, data, err = _http("GET", f"/query-status/{job_id}", timeout=30.0)
        if st == 200 and data:
            job_status = data.get("status") or "pending"
            if job_status in ("done", "error", "interrupted"):
                result = data.get("result") or {}
                payload = result.get("payload") if isinstance(result, dict) else {}
                payload = payload or {}
                answer = payload.get("answer") or ""
                error = (payload.get("error") or data.get("error") or "")
                break
        time.sleep(POLL_INTERVAL)

    latency = (time.time() - t0) * 1000.0
    return {
        "question": question, "latency_ms": round(latency, 1), "submit_status": submit_status,
        "job_status": job_status, "rate_limited_hits": rl_hits,
        "answer_len": len((answer or "").strip()),
        "outcome": _classify(answer, error, submit_status, job_status, timed_out),
    }


# ---------------------------------------------------------------- aggregation
def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 1)


def _cache_delta(before: dict, after: dict) -> dict:
    keys = ("hits_exact", "hits_exact_nodia", "hits_semantic", "hits_semantic_judged",
            "misses", "saved_llm_calls")
    b, a = before or {}, after or {}
    d = {k: (a.get(k, 0) - b.get(k, 0)) for k in keys}
    d["hits_total"] = d["hits_exact"] + d["hits_exact_nodia"] + d["hits_semantic"] + d["hits_semantic_judged"]
    return d


def run_scenario(name: str, mode: str, concurrency: int, total: int) -> dict:
    pool = QUESTIONS[mode]
    tasks = [pool[i % len(pool)] for i in range(total)]

    _, stats_before, _ = _http("GET", "/stats", timeout=15.0)
    gen_before = _gen_count()

    print(f"\n=== {name}: mode={mode} concurrency={concurrency} total={total} ===", flush=True)
    t0 = time.time()
    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for rec in ex.map(run_one, tasks):
            records.append(rec)
            print(f"  [{len(records):>3}/{total}] {rec['outcome']:<12} "
                  f"{rec['latency_ms']:>8.0f}ms  submit={rec['submit_status']} "
                  f"429x{rec['rate_limited_hits']}", flush=True)
    wall = time.time() - t0

    _, stats_after, _ = _http("GET", "/stats", timeout=15.0)
    gen_after = _gen_count()

    lat = sorted(r["latency_ms"] for r in records)
    outc: dict[str, int] = {}
    for r in records:
        outc[r["outcome"]] = outc.get(r["outcome"], 0) + 1
    rl_total = sum(r["rate_limited_hits"] for r in records)

    return {
        "scenario": name, "mode": mode, "concurrency": concurrency,
        "total_requests": total,
        "success_count": outc.get("success", 0),
        "empty_response_count": outc.get("empty", 0),
        "fallback_answer_count": outc.get("fallback", 0),
        "error_count": outc.get("error", 0) + outc.get("http_5xx", 0),
        "timeout_count": outc.get("timeout", 0),
        "rate_limited_count": outc.get("rate_limited", 0),
        "rate_limited_hits_total": rl_total,
        "http_5xx_count": outc.get("http_5xx", 0),
        "outcomes": outc,
        "p50_latency_ms": _pct(lat, 50), "p95_latency_ms": _pct(lat, 95),
        "p99_latency_ms": _pct(lat, 99),
        "min_latency_ms": lat[0] if lat else 0.0, "max_latency_ms": lat[-1] if lat else 0.0,
        "wall_seconds": round(wall, 1),
        "throughput_rps": round(total / wall, 3) if wall else 0.0,
        "cache_delta": _cache_delta(stats_before or {}, stats_after or {}),
        "llm_generations": (None if gen_before is None or gen_after is None
                            else gen_after - gen_before),
        "records": records,
    }


def health_check() -> dict:
    _, health, herr = _http("GET", "/health", timeout=15.0)
    _, stats, _ = _http("GET", "/stats", timeout=15.0)
    ollama_st, ollama, _ = _http("GET", "http://localhost:11434/api/tags", timeout=10.0)
    redis_ping = _compose_exec("redis", ["redis-cli", "ping"])
    gen = _gen_count()
    hc = {
        "backend_reachable": health is not None,
        "backend_health": health, "backend_error": herr,
        "query_graph_ready": bool((health or {}).get("query_graph_ready")),
        "num_documents": (stats or {}).get("num_documents"),
        "num_chunks": (stats or {}).get("num_chunks"),
        "ollama_reachable": ollama_st == 200,
        "ollama_models": [m.get("name") for m in (ollama or {}).get("models", [])] if ollama else None,
        "redis_reachable": (redis_ping or "").upper().endswith("PONG"),
        "logs_sqlite_readable": gen is not None,
        "generate_answer_count_so_far": gen,
    }
    print("\n=== health ===", flush=True)
    for k, v in hc.items():
        print(f"  {k}: {v}", flush=True)
    return hc


# ---------------------------------------------------------------- report
def write_reports(payload: dict) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = payload["timestamp"]
    jpath = REPORT_DIR / f"baseline-{ts}.json"
    mpath = REPORT_DIR / f"baseline-{ts}.md"
    jpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    hc = payload.get("health") or {}
    lines = [
        f"# MemVid concurrency baseline — {ts}", "",
        f"- BASE_URL: `{BASE_URL}`",
        f"- sources filter: `{SOURCES or 'ALL DOCS'}`",
        f"- retry_on_429: `{RETRY_ON_429}`  |  per-request timeout: `{TIMEOUT_SECONDS}s`", "",
        "## Health", "",
        f"- backend reachable: **{hc.get('backend_reachable')}**  |  query_graph_ready: **{hc.get('query_graph_ready')}**",
        f"- documents: {hc.get('num_documents')}  |  chunks: {hc.get('num_chunks')}",
        f"- ollama reachable: **{hc.get('ollama_reachable')}**  models: {hc.get('ollama_models')}",
        f"- redis reachable: **{hc.get('redis_reachable')}**",
        f"- logs.sqlite readable (LLM-gen counting): **{hc.get('logs_sqlite_readable')}**", "",
        "## Scenarios", "",
        "| scenario | conc | total | ok | empty | fallback | err | timeout | 429 | p50 ms | p95 ms | p99 ms | max ms | rps | LLM gens | cache hits |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for s in payload.get("scenarios", []):
        cd = s.get("cache_delta", {})
        lines.append(
            f"| {s['scenario']} | {s['concurrency']} | {s['total_requests']} | "
            f"{s['success_count']} | {s['empty_response_count']} | {s['fallback_answer_count']} | "
            f"{s['error_count']} | {s['timeout_count']} | {s['rate_limited_count']} | "
            f"{s['p50_latency_ms']:.0f} | {s['p95_latency_ms']:.0f} | {s['p99_latency_ms']:.0f} | "
            f"{s['max_latency_ms']:.0f} | {s['throughput_rps']} | "
            f"{s['llm_generations']} | {cd.get('hits_total')} |"
        )
    lines += ["", "## Notes", ""]
    for s in payload.get("scenarios", []):
        gens, total = s.get("llm_generations"), s["total_requests"]
        note = ""
        if gens is not None and s["mode"] in ("identical", "vietnamese_paraphrase") and total > 1:
            if gens > 1:
                note = (f"⚠️ {gens} LLM generations for {total} identical/paraphrase requests "
                        f"→ NO coalescing (single-flight would collapse this to ~1).")
            else:
                note = f"✓ {gens} generation for {total} identical requests → already coalesced."
        lines.append(f"- **{s['scenario']}**: {note or 'see table'}")
    mpath.write_text("\n".join(lines), encoding="utf-8")
    return jpath, mpath


def main() -> int:
    scenario = (os.getenv("SCENARIO") or "").strip().lower()
    conc = int(os.getenv("CONCURRENCY", "10"))
    total = int(os.getenv("TOTAL_REQUESTS", str(conc)))
    mode = (os.getenv("QUESTION_MODE") or "identical").strip().lower()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    payload = {"timestamp": ts, "base_url": BASE_URL, "scenarios": []}
    payload["health"] = health_check()
    if not payload["health"]["backend_reachable"]:
        print("\nBackend unreachable — aborting load scenarios.", file=sys.stderr)
        write_reports(payload)
        return 2

    if scenario == "health":
        pass
    elif scenario in ("identical", "distinct", "vietnamese_paraphrase"):
        payload["scenarios"].append(run_scenario(scenario, scenario, conc, total))
    else:  # "all" or unset -> full matrix
        matrix = [
            ("10-identical", "identical", 10, 10),
            ("50-identical", "identical", 50, 50),
            ("10-distinct", "distinct", 10, 10),
            ("50-distinct", "distinct", 50, 50),
            ("vietnamese-paraphrase", "vietnamese_paraphrase", 5, 5),
        ]
        for nm, md, c, t in matrix:
            payload["scenarios"].append(run_scenario(nm, md, c, t))

    jpath, mpath = write_reports(payload)
    print(f"\nJSON: {jpath}\nMD:   {mpath}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
