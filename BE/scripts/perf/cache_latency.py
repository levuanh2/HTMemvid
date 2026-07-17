#!/usr/bin/env python3
"""MemVid cache latency scenarios.

Perf helper focused on cache-miss and cache-hit latency using the same
conventions as baseline_concurrency.py:
  - stdlib only,
  - env-driven configuration,
  - health check + /query -> /query-status polling,
  - JSON + Markdown reports under reports/performance,
  - p50/p95/p99/min/max latency summaries,
  - /stats snapshots before/after each scenario,
  - backend GenerateAnswer count delta when available.

Scenarios:
  cache_miss
    Sends N unique questions by appending a UUID suffix to BASE_QUESTION.
    This is intended to defeat semantic cache reuse and measure miss latency.

  cache_hit
    Warms BASE_QUESTION once, then sends the exact same question N times.
    This is intended to measure hit latency and should usually show cache
    hits > 0 and roughly one LLM generation.

Config via env (all optional):
  BASE_URL              default http://localhost:8080
  CONCURRENCY           default 5            (bounded in-flight workers)
  TOTAL_REQUESTS        default = CONCURRENCY
  SCENARIO              all | health | cache_miss | cache_hit
                        (default: all)
  BASE_QUESTION         default "noi dung la gi"
  POLL_INTERVAL_SECONDS default 1.0
  TIMEOUT_SECONDS       default 360
  SOURCES               comma list of source stems (default: empty = all docs)
  RETRY_ON_429          1 to retry admission 429 until deadline (default 1)
  COMPOSE               docker compose invocation (default "docker compose")
  REPORT_DIR            default <repo>/reports/performance

Run:
  python BE/scripts/perf/cache_latency.py
  SCENARIO=cache_miss TOTAL_REQUESTS=20 CONCURRENCY=5 ^
      python BE/scripts/perf/cache_latency.py
  SCENARIO=cache_hit TOTAL_REQUESTS=20 CONCURRENCY=5 ^
      python BE/scripts/perf/cache_latency.py
  BASE_QUESTION="tai lieu nay noi ve gi" SCENARIO=cache_hit ^
      python BE/scripts/perf/cache_latency.py
  python BE/scripts/perf/cache_latency.py --help
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

BASE_URL = os.getenv("BASE_URL", "http://localhost:8080").rstrip("/")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SECONDS", "1.0"))
TIMEOUT_SECONDS = float(os.getenv("TIMEOUT_SECONDS", "360"))
SOURCES = [s.strip() for s in (os.getenv("SOURCES") or "").split(",") if s.strip()]
RETRY_ON_429 = (os.getenv("RETRY_ON_429", "1").strip() not in ("0", "false", "False"))
COMPOSE = os.getenv("COMPOSE", "docker compose")
BASE_QUESTION = os.getenv("BASE_QUESTION", "noi dung la gi").strip() or "noi dung la gi"
_REPO_ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = Path(os.getenv("REPORT_DIR", str(_REPO_ROOT / "reports" / "performance")))

_FALLBACK_MARKERS = (
    "Khong nhan duoc phan hoi",
    "Khong co phan hoi",
    "Không nhận được phản hồi",
    "Không có phản hồi",
)


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
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if exc.fp else ""
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            parsed = {"_raw": raw}
        return exc.code, parsed, None
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"


def _compose_exec(service: str, argv: list[str], timeout: float = 20.0):
    cmd = COMPOSE.split() + ["exec", "-T", service] + argv
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _gen_count() -> int | None:
    py = (
        "import os,sqlite3;"
        "p=os.environ.get('LOG_DB_PATH') or '/app/memory/logs.sqlite';"
        "c=sqlite3.connect(p);"
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


def _classify(answer: str, err_text: str, submit_status, job_status: str, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if submit_status == 429:
        return "rate_limited"
    if submit_status is not None and submit_status >= 500:
        return "http_5xx"
    if submit_status is None:
        return "error"
    if job_status == "error" or (err_text and not answer):
        return "error"
    cleaned = (answer or "").strip()
    if not cleaned:
        return "empty"
    if any(marker in cleaned for marker in _FALLBACK_MARKERS):
        return "fallback"
    return "success"


def run_one(question: str) -> dict:
    t0 = time.time()
    deadline = t0 + TIMEOUT_SECONDS
    session_id = str(uuid.uuid4())
    body = {"q": question, "sources": SOURCES, "use_memory_tree": True, "session_id": session_id}

    rate_limited_hits = 0
    submit_status = None
    job_id = None
    while time.time() < deadline:
        submit_status, payload, _ = _http("POST", "/query", body, timeout=30.0)
        if submit_status == 202 and payload:
            job_id = payload.get("job_id")
            break
        if submit_status == 429:
            rate_limited_hits += 1
            if not RETRY_ON_429:
                break
            time.sleep(0.5)
            continue
        break

    if not job_id:
        latency = (time.time() - t0) * 1000.0
        return {
            "question": question,
            "latency_ms": round(latency, 1),
            "submit_status": submit_status,
            "job_status": None,
            "rate_limited_hits": rate_limited_hits,
            "outcome": _classify("", "", submit_status, "", False),
        }

    job_status, answer, err_text, timed_out = "pending", "", "", False
    while True:
        if time.time() >= deadline:
            timed_out = True
            break
        status, data, _ = _http("GET", f"/query-status/{job_id}", timeout=30.0)
        if status == 200 and data:
            job_status = data.get("status") or "pending"
            if job_status in ("done", "error", "interrupted"):
                result = data.get("result") or {}
                payload = result.get("payload") if isinstance(result, dict) else {}
                payload = payload or {}
                answer = payload.get("answer") or ""
                err_text = payload.get("error") or data.get("error") or ""
                break
        time.sleep(POLL_INTERVAL)

    latency = (time.time() - t0) * 1000.0
    return {
        "question": question,
        "latency_ms": round(latency, 1),
        "submit_status": submit_status,
        "job_status": job_status,
        "rate_limited_hits": rate_limited_hits,
        "answer_len": len((answer or "").strip()),
        "outcome": _classify(answer, err_text, submit_status, job_status, timed_out),
    }


def _pct(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo), 1)


def _cache_delta(before: dict | None, after: dict | None) -> dict:
    keys = (
        "hits_exact",
        "hits_exact_nodia",
        "hits_semantic",
        "hits_semantic_judged",
        "misses",
        "saved_llm_calls",
    )
    before_stats = before or {}
    after_stats = after or {}
    delta = {key: (after_stats.get(key, 0) - before_stats.get(key, 0)) for key in keys}
    delta["hits_total"] = (
        delta["hits_exact"]
        + delta["hits_exact_nodia"]
        + delta["hits_semantic"]
        + delta["hits_semantic_judged"]
    )
    return delta


def _flatten_numeric_stats(data: dict | list | None, prefix: str = "") -> dict[str, float]:
    flat: dict[str, float] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flat.update(_flatten_numeric_stats(value, child_prefix))
        return flat
    if isinstance(data, list):
        for idx, value in enumerate(data):
            child_prefix = f"{prefix}[{idx}]"
            flat.update(_flatten_numeric_stats(value, child_prefix))
        return flat
    if isinstance(data, (int, float)) and not isinstance(data, bool) and prefix:
        flat[prefix] = float(data)
    return flat


def _extract_metric_family(deltas: dict[str, float], needles: tuple[str, ...]) -> dict[str, float]:
    family = {}
    for key, value in deltas.items():
        lowered = key.lower()
        if any(needle in lowered for needle in needles):
            family[key] = value
    return family


def _stats_metric_deltas(before: dict | None, after: dict | None) -> dict:
    flat_before = _flatten_numeric_stats(before or {})
    flat_after = _flatten_numeric_stats(after or {})
    deltas = {}
    for key in sorted(set(flat_before) | set(flat_after)):
        delta = round(flat_after.get(key, 0.0) - flat_before.get(key, 0.0), 3)
        if delta != 0:
            deltas[key] = delta
    return {
        "cache_metrics": _extract_metric_family(deltas, ("cache", "hit", "miss")),
        "single_flight_metrics": _extract_metric_family(
            deltas, ("single_flight", "singleflight", "coalesc", "dedup", "collapse")
        ),
        "llm_generation_metrics": _extract_metric_family(
            deltas, ("llm", "generation", "generateanswer")
        ),
        "all_numeric_deltas": deltas,
    }


def _build_cache_miss_tasks(total: int) -> list[str]:
    return [f"{BASE_QUESTION} [cache-miss:{uuid.uuid4()}]" for _ in range(total)]


def _build_cache_hit_tasks(total: int) -> list[str]:
    return [BASE_QUESTION for _ in range(total)]


def _run_records(tasks: list[str], concurrency: int, total: int) -> tuple[list[dict], float]:
    records: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for rec in executor.map(run_one, tasks):
            records.append(rec)
            print(
                f"  [{len(records):>3}/{total}] {rec['outcome']:<12} "
                f"{rec['latency_ms']:>8.0f}ms  submit={rec['submit_status']} "
                f"429x{rec['rate_limited_hits']}",
                flush=True,
            )
    return records, time.time() - t0


def run_scenario(name: str, concurrency: int, total: int, tasks: list[str], warmup_question: str | None = None) -> dict:
    _, stats_before, _ = _http("GET", "/stats", timeout=15.0)
    gen_before = _gen_count()
    warmup_record = None

    if warmup_question:
        print(f"\n--- warming cache for {name} ---", flush=True)
        warmup_record = run_one(warmup_question)
        print(
            f"  warmup {warmup_record['outcome']:<12} "
            f"{warmup_record['latency_ms']:>8.0f}ms  submit={warmup_record['submit_status']} "
            f"429x{warmup_record['rate_limited_hits']}",
            flush=True,
        )

    print(f"\n=== {name}: concurrency={concurrency} total={total} ===", flush=True)
    records, wall = _run_records(tasks, concurrency, total)

    _, stats_after, _ = _http("GET", "/stats", timeout=15.0)
    gen_after = _gen_count()
    stats_delta = _stats_metric_deltas(stats_before, stats_after)
    latencies = sorted(record["latency_ms"] for record in records)
    outcomes: dict[str, int] = {}
    for record in records:
        outcomes[record["outcome"]] = outcomes.get(record["outcome"], 0) + 1

    return {
        "scenario": name,
        "mode": name.replace("-", "_"),
        "concurrency": concurrency,
        "total_requests": total,
        "base_question": BASE_QUESTION,
        "success_count": outcomes.get("success", 0),
        "empty_response_count": outcomes.get("empty", 0),
        "fallback_answer_count": outcomes.get("fallback", 0),
        "error_count": outcomes.get("error", 0) + outcomes.get("http_5xx", 0),
        "timeout_count": outcomes.get("timeout", 0),
        "rate_limited_count": outcomes.get("rate_limited", 0),
        "rate_limited_hits_total": sum(record["rate_limited_hits"] for record in records),
        "http_5xx_count": outcomes.get("http_5xx", 0),
        "outcomes": outcomes,
        "p50_latency_ms": _pct(latencies, 50),
        "p95_latency_ms": _pct(latencies, 95),
        "p99_latency_ms": _pct(latencies, 99),
        "min_latency_ms": latencies[0] if latencies else 0.0,
        "max_latency_ms": latencies[-1] if latencies else 0.0,
        "wall_seconds": round(wall, 1),
        "throughput_rps": round(total / wall, 3) if wall else 0.0,
        "cache_delta": _cache_delta(stats_before, stats_after),
        "cache_metrics_delta": stats_delta["cache_metrics"],
        "single_flight_delta": stats_delta["single_flight_metrics"],
        "llm_generation_stats_delta": stats_delta["llm_generation_metrics"],
        "stats_delta": stats_delta,
        "llm_generations": None if gen_before is None or gen_after is None else gen_after - gen_before,
        "warmup": warmup_record,
        "records": records,
    }


def health_check() -> dict:
    _, health, health_err = _http("GET", "/health", timeout=15.0)
    _, stats, _ = _http("GET", "/stats", timeout=15.0)
    ollama_status, ollama, _ = _http("GET", "http://localhost:11434/api/tags", timeout=10.0)
    redis_ping = _compose_exec("redis", ["redis-cli", "ping"])
    gen_count = _gen_count()
    result = {
        "backend_reachable": health is not None,
        "backend_health": health,
        "backend_error": health_err,
        "query_graph_ready": bool((health or {}).get("query_graph_ready")),
        "num_documents": (stats or {}).get("num_documents"),
        "num_chunks": (stats or {}).get("num_chunks"),
        "ollama_reachable": ollama_status == 200,
        "ollama_models": [item.get("name") for item in (ollama or {}).get("models", [])] if ollama else None,
        "redis_reachable": (redis_ping or "").upper().endswith("PONG"),
        "logs_sqlite_readable": gen_count is not None,
        "generate_answer_count_so_far": gen_count,
    }
    print("\n=== health ===", flush=True)
    for key, value in result.items():
        print(f"  {key}: {value}", flush=True)
    return result


def write_reports(payload: dict) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = payload["timestamp"]
    json_path = REPORT_DIR / f"cache-latency-{timestamp}.json"
    md_path = REPORT_DIR / f"cache-latency-{timestamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    health = payload.get("health") or {}
    lines = [
        f"# MemVid cache latency baseline - {timestamp}",
        "",
        f"- BASE_URL: `{BASE_URL}`",
        f"- base question: `{BASE_QUESTION}`",
        f"- sources filter: `{SOURCES or 'ALL DOCS'}`",
        f"- retry_on_429: `{RETRY_ON_429}`  |  per-request timeout: `{TIMEOUT_SECONDS}s`",
        "",
        "## Health",
        "",
        f"- backend reachable: **{health.get('backend_reachable')}**  |  query_graph_ready: **{health.get('query_graph_ready')}**",
        f"- documents: {health.get('num_documents')}  |  chunks: {health.get('num_chunks')}",
        f"- ollama reachable: **{health.get('ollama_reachable')}**  models: {health.get('ollama_models')}",
        f"- redis reachable: **{health.get('redis_reachable')}**",
        f"- logs.sqlite readable (LLM-gen counting): **{health.get('logs_sqlite_readable')}**",
        "",
        "## Scenarios",
        "",
        "| scenario | conc | total | ok | empty | fallback | err | timeout | 429 | p50 ms | p95 ms | p99 ms | max ms | rps | LLM gens | cache hits |",
        "|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for scenario in payload.get("scenarios", []):
        cache_delta = scenario.get("cache_delta", {})
        lines.append(
            f"| {scenario['scenario']} | {scenario['concurrency']} | {scenario['total_requests']} | "
            f"{scenario['success_count']} | {scenario['empty_response_count']} | {scenario['fallback_answer_count']} | "
            f"{scenario['error_count']} | {scenario['timeout_count']} | {scenario['rate_limited_count']} | "
            f"{scenario['p50_latency_ms']:.0f} | {scenario['p95_latency_ms']:.0f} | {scenario['p99_latency_ms']:.0f} | "
            f"{scenario['max_latency_ms']:.0f} | {scenario['throughput_rps']} | "
            f"{scenario['llm_generations']} | {cache_delta.get('hits_total')} |"
        )

    lines.extend(["", "## Notes", ""])
    for scenario in payload.get("scenarios", []):
        if scenario["scenario"] == "cache-hit":
            note = f"warm once then repeat; observed LLM generations delta={scenario.get('llm_generations')}."
        else:
            note = f"unique-query miss run; observed LLM generations delta={scenario.get('llm_generations')}."
        lines.append(f"- **{scenario['scenario']}**: {note}")
        warmup = scenario.get("warmup")
        if warmup:
            lines.append(f"  warmup outcome={warmup.get('outcome')} latency_ms={warmup.get('latency_ms')}")
        if scenario.get("cache_metrics_delta"):
            lines.append(f"  cache metric deltas={scenario['cache_metrics_delta']}")
        if scenario.get("single_flight_delta"):
            lines.append(f"  single-flight deltas={scenario['single_flight_delta']}")
        if scenario.get("llm_generation_stats_delta"):
            lines.append(f"  llm-generation stat deltas={scenario['llm_generation_stats_delta']}")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def _run_named_scenario(scenario: str, concurrency: int, total: int) -> dict:
    normalized = scenario.strip().lower().replace("-", "_")
    if normalized == "cache_miss":
        return run_scenario("cache-miss", concurrency, total, _build_cache_miss_tasks(total))
    if normalized == "cache_hit":
        return run_scenario(
            "cache-hit",
            concurrency,
            total,
            _build_cache_hit_tasks(total),
            warmup_question=BASE_QUESTION,
        )
    raise ValueError(f"Unknown scenario: {scenario}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(arg in ("-h", "--help") for arg in argv):
        print(__doc__.strip())
        return 0

    scenario = (os.getenv("SCENARIO") or "all").strip().lower().replace("-", "_")
    concurrency = int(os.getenv("CONCURRENCY", "5"))
    total = int(os.getenv("TOTAL_REQUESTS", str(concurrency)))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    payload = {"timestamp": timestamp, "base_url": BASE_URL, "scenarios": []}
    payload["health"] = health_check()
    if not payload["health"]["backend_reachable"]:
        print("\nBackend unreachable - aborting load scenarios.", file=sys.stderr)
        write_reports(payload)
        return 2

    if scenario == "health":
        pass
    elif scenario == "all":
        payload["scenarios"].append(run_scenario("cache-miss", concurrency, total, _build_cache_miss_tasks(total)))
        payload["scenarios"].append(
            run_scenario(
                "cache-hit",
                concurrency,
                total,
                _build_cache_hit_tasks(total),
                warmup_question=BASE_QUESTION,
            )
        )
    else:
        payload["scenarios"].append(_run_named_scenario(scenario, concurrency, total))

    json_path, md_path = write_reports(payload)
    print(f"\nJSON: {json_path}\nMD:   {md_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
