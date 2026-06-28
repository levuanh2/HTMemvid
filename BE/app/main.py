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
from app.clients.llm_factory import ask_ai, summarize_whole_document, summarize_results
# Chỉ dùng cho local Ollama (Gemini sẽ bỏ qua model).
SLM_MODEL = os.environ.get("SLM_MODEL_CHAT", os.environ.get("SLM_MODEL", "qwen3.6:35b-a3b"))
SLM_MODEL_SUMMARY = os.environ.get("SLM_MODEL_SUMMARY", "gemma2:2b")
from services.mindmap.worker import (
    attach_mindmap_job_context,
    run_mindmap_generation,
    MODE_FAST,
    MODE_BALANCED,
    MODE_QUALITY,
    VALID_MODES,
    DEFAULT_MODE,
    get_llm_timeout_for_mode,
    get_job_timeout_for_mode,
    get_llm_call_budget_for_mode,
    get_mindmap_model_for_mode,
)

# Valid strategies for mindmap generation
VALID_STRATEGIES = {
    "auto",
    "single_call_schema",
    "mindmap_v2",
    "cmgn_light",
    "cmgn",
    "multilevel_fast",
    "multilevel",
    "iterative",
}
from app.domains.ingest.chunk_processor import process_and_store_chunks
from app.domains.summary.summarize_advanced import advanced_summarize
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
    allow_headers=["Content-Type"],
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
MINDMAP_GRAPH = None
QUERY_GRAPH_BUILD_ERROR: Optional[str] = None

_jobs_update_job = None
_jobs_create_job = None
try:
    from app.domains.jobs.jobs_store import update_job as _jobs_update_job, create_job as _jobs_create_job
except Exception:
    pass


def _handle_sigterm(*_args):
    # best-effort: mark running jobs interrupted để tránh trạng thái mồ côi
    try:
        if _jobs_mark_interrupted is not None:
            _jobs_mark_interrupted()
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

# Mindmap async jobs (tách biệt `jobs` dùng cho rebuild-index)
mindmap_jobs: Dict[str, Dict[str, Any]] = {}
mindmap_jobs_lock = threading.Lock()
MINDMAP_JOB_TTL_MINUTES = int(os.environ.get("MINDMAP_JOB_TTL_MINUTES", "30"))

# Migrate legacy in-memory mindmap_jobs dict sang SQLite (idempotent)
try:
    if _jobs_migrate_from_dict is not None:
        _jobs_migrate_from_dict(mindmap_jobs, job_type="mindmap")
except Exception:
    pass


def _cleanup_old_mindmap_jobs() -> None:
    if MINDMAP_JOB_TTL_MINUTES <= 0:
        return
    cutoff = time.time() - (MINDMAP_JOB_TTL_MINUTES * 60)
    with mindmap_jobs_lock:
        expired = [
            jid for jid, j in mindmap_jobs.items()
            if isinstance(j.get("created_at"), (int, float)) and j["created_at"] < cutoff
        ]
        for jid in expired:
            mindmap_jobs.pop(jid, None)


def _make_query_cache_key(q: str, selected_sources: list, use_memory_tree: bool, filters: dict | None = None) -> str:
    # Normalize list để key ổn định theo thứ tự chọn
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
        if not entry:
            return None
        if now - entry["ts"] > QUERY_CACHE_TTL_SEC:
            _query_cache.pop(cache_key, None)
            return None
        _query_cache.move_to_end(cache_key)
        return entry["value"]

def _set_cached_query(cache_key: str, value: dict) -> None:
    with _query_cache_lock:
        if cache_key in _query_cache:
            _query_cache.move_to_end(cache_key)
        _query_cache[cache_key] = {"ts": time.time(), "value": value}
        while len(_query_cache) > QUERY_CACHE_MAX_SIZE:
            _query_cache.popitem(last=False)


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

    def rebuild_task(jid: str) -> None:
        try:
            with jobs_lock:
                if jid not in jobs:
                    return
                jobs[jid]["status"] = "running"
                jobs[jid]["progress"] = 0

            def progress_cb(progress: int, extra: Optional[Dict[str, Any]] = None) -> None:
                with jobs_lock:
                    if jid not in jobs:
                        return
                    jobs[jid]["progress"] = progress
                    if extra:
                        if "num_videos" in extra and extra["num_videos"] is not None:
                            jobs[jid]["num_videos"] = int(extra["num_videos"])
                        if "num_chunks" in extra and extra["num_chunks"] is not None:
                            jobs[jid]["num_chunks"] = int(extra["num_chunks"])

            from app.scripts.rebuild_index_from_video import rebuild_faiss_index_from_videos
            result = rebuild_faiss_index_from_videos(progress_cb=progress_cb)
            with jobs_lock:
                if jid in jobs:
                    jobs[jid]["status"] = "done"
                    jobs[jid]["progress"] = 100
                    jobs[jid]["num_chunks"] = int(result.get("num_chunks") or 0)
                    jobs[jid]["num_videos"] = int(result.get("num_videos") or jobs[jid].get("num_videos") or 0)
        except Exception as exc:
            with jobs_lock:
                if jid in jobs:
                    jobs[jid]["status"] = "error"
                    jobs[jid]["error"] = str(exc)
            print(f"[REBUILD] job_id={jid} failed: {exc}")
        finally:
            # Release file lock best-effort
            try:
                if REBUILD_LOCK_PATH.exists():
                    REBUILD_LOCK_PATH.unlink()
            except Exception:
                pass

    thread = threading.Thread(target=rebuild_task, args=(job_id,), daemon=True)
    try:
        thread.start()
    except Exception as exc:
        # If thread failed to start, cleanup lock and job
        try:
            if REBUILD_LOCK_PATH.exists():
                REBUILD_LOCK_PATH.unlink()
        except Exception:
            pass
        with jobs_lock:
            jobs.pop(job_id, None)
        return jsonify({"error": f"Failed to start rebuild job: {str(exc)}"}), 500

    return jsonify({"status": "started", "job_id": job_id}), 202


@app.get('/rebuild-status/<job_id>')
def rebuild_status(job_id: str):
    _cleanup_old_jobs()
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
    for source_id, info in registry.items():
        stored_stem = info.get("source_stem")
        if stored_stem:
            if stored_stem == source_stem:
                return info
        else:
            filename = info.get("filename", "")
            normalized = _normalize_video_stem(filename)
            if normalized == source_stem:
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
# (sau khi mọi callback/helper như _append_mindmap đã sẵn sàng).

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


def _load_mindmaps() -> list[dict]:
    if not MINDMAPS_PATH.exists():
        return []
    try:
        with open(MINDMAPS_PATH, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception as exc:
        print(f"⚠️ Không thể đọc mindmaps.json: {exc}")
    return []


def _save_mindmaps(records: list[dict]) -> None:
    try:
        tmp_path = MINDMAPS_PATH.with_suffix('.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        tmp_path.replace(MINDMAPS_PATH)
    except Exception as exc:
        print(f"⚠️ Không thể lưu mindmaps.json: {exc}")


def _append_mindmap(record: dict) -> None:
    records = _load_mindmaps()
    records.insert(0, record)
    _save_mindmaps(records)


def _mindmap_response(record: dict) -> dict:
    nodes = record.get("nodes")
    if not isinstance(nodes, list):
        nodes = []
    diagram = record.get("diagram")
    print(f"[mindmap response] nodes={len(nodes)}, diagram_nodes={len((diagram or {}).get('nodes') or [])}")
    response = {
        "id": record.get("id"),
        "title": record.get("title"),
        "nodes": nodes,
        "diagram": diagram,  # Include visual diagram for Napkin AI
        "sources": record.get("sources", []),
        "createdAt": record.get("createdAt"),
        "strategy": record.get("strategy") or "iterative",
        "mode": record.get("mode") or DEFAULT_MODE,
    }
    # Thêm visualDiagramMode nếu có
    if "visualDiagramMode" in record:
        response["visualDiagramMode"] = record["visualDiagramMode"]
    return response


# === Dựng toàn bộ LangGraph pipeline qua wiring tập trung (T4) ===
from app.wiring import build_graphs as _build_graphs
from app.clients.mindmap_factory import get_mindmap_runner as _get_mindmap_runner

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
    run_mindmap_generation=_get_mindmap_runner(),
    append_mindmap=_append_mindmap,
)
INGEST_GRAPH = _graphs.ingest
QUERY_GRAPH = _graphs.query
QUERY_GRAPH_BUILD_ERROR = _graphs.query_build_error
MINDMAP_GRAPH = _graphs.mindmap


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


def _finalize_query_job(jid: str, session_id: str, question: str, out: dict) -> None:
    """Trích payload/status từ kết quả graph → cập nhật query_jobs/jobs_store + persist history.

    Dùng chung cho /query và /query-resume.
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


def _load_summaries() -> list[dict]:
    if not SUMMARIES_PATH.exists():
        return []
    try:
        with open(SUMMARIES_PATH, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception as exc:
        print(f"⚠️ Không thể đọc summaries.json: {exc}")
    return []


def _save_summaries(records: list[dict]) -> None:
    try:
        tmp_path = SUMMARIES_PATH.with_suffix('.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        tmp_path.replace(SUMMARIES_PATH)
    except Exception as exc:
        print(f"⚠️ Không thể lưu summaries.json: {exc}")


def _append_summary(record: dict) -> None:
    records = _load_summaries()
    records.insert(0, record)
    _save_summaries(records)


# -------------------------
# 🔄 Background tasks (non-blocking)
# -------------------------
def _build_memory_tree_background(source_stems: List[str]):
    """
    Background task: Build Memory Tree cho các source đã ingest.
    Chạy trong thread riêng, không block request.
    """
    try:
        print(f"🔄 [Background] Bắt đầu build Memory Tree cho {len(source_stems)} source(s)...")
        build_memory_tree_for_sources(source_stems)
        print(f"✅ [Background] Hoàn thành build Memory Tree cho {source_stems}")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"⚠️ [Background] Lỗi build Memory Tree: {exc}")


def _trigger_memory_tree_build(source_stems: List[str]):
    """
    Trigger background task để build Memory Tree (non-blocking).
    """
    if not source_stems:
        return
    thread = threading.Thread(
        target=_build_memory_tree_background,
        args=(source_stems,),
        daemon=True
    )
    thread.start()
    print(f"🚀 [Background] Đã trigger build Memory Tree cho: {source_stems}")


def _trigger_background_ingest(source_id: str, file_path: str, filename: str):
    """
    Trigger ingest qua LangGraph (Phase 5 — không còn pipeline thread legacy).
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
        _jobs_create_job(job_id, job_type="ingest", status="pending", progress=0, current_node="Queued")
    except Exception:
        pass

    def run_graph():
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

    thread = threading.Thread(target=run_graph, daemon=True)
    thread.start()
    print(f"🚀 [Background] (LangGraph) Đã trigger ingest cho source: {source_id}")


# -------------------------
# 📤 Process raw text
# -------------------------
@app.post('/process-doc')
def process_doc():
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
@app.post('/upload-file')
def upload_file():
    """
    Upload file và trả response ngay, xử lý ingest chạy background.
    """
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'Missing file'}), 400

    # Generate source_id (unique identifier cho source này)
    source_id = str(uuid.uuid4())
    filename = file.filename
    
    # Save file
    save_path = os.path.join(INPUT_DIR, filename)
    file.save(save_path)
    
    # Register source với status "processing"
    # Tính source_stem từ filename để dễ map sau này
    video_name = f"{filename.replace('.', '_')}"
    source_stem = Path(video_name).stem.lower()
    
    registry = _load_source_registry()
    registry[source_id] = {
        "filename": filename,
        "source_stem": source_stem,  # Lưu thêm để dễ map
        "status": "processing",
        "progress": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_source_registry(registry)
    
    # Trigger background ingest (non-blocking)
    _trigger_background_ingest(source_id, save_path, filename)
    
    # Trả response ngay
    return jsonify({
        'source_id': source_id,
        'filename': filename,
        'video_stem': source_stem,
        'status': 'processing',
        'progress': 0.0,
        'can_query': False,
    })


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
    status_info = _get_source_status(source_id)
    if not status_info:
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
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'Missing files'}), 400

    results = []
    for file in files:
        save_path = os.path.join(INPUT_DIR, file.filename)
        file.save(save_path)

        text = extract_text(save_path)
        if not text.strip():
            results.append({'file': file.filename, 'error': 'Cannot read content'})
            continue

        chunks = split_text(text)
        video_name = f"{file.filename.replace('.', '_')}"

        try:
            video_path, metadata_entries = process_and_store_chunks(
                chunks=chunks,
                video_name=video_name,
                timestamp=datetime.now().isoformat()
            )

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

            results.append({
                'file': file.filename,
                'video_path': video_path,
                'status': 'uploaded',
                'message': 'OK. Memory Tree is being built in background.'
            })
        except Exception as e:
            results.append({
                'file': file.filename,
                'error': f'Processing failed: {str(e)}'
            })

    return jsonify({'results': results})
@app.get('/list-indexed')
def list_indexed():
    """
    Lấy danh sách tất cả sources đã được index.
    Trả về format mà frontend expect: { video: stem, chunks: [...], num_chunks: N }
    """
    try:
        with open(INDEX_META_JSON_PATH, encoding='utf-8') as f:
            meta = json.load(f)

        video_map = {}
        for item in meta.values():
            video = item.get('video', '').strip()
            if not video or video.lower() == 'unknown':
                continue
            
            # Normalize video path: có thể là đường dẫn đầy đủ hoặc chỉ tên file
            # Ví dụ: "videos/file_pdf_20260109_001635.mp4" hoặc "file_pdf_20260109_001635.mp4"
            video_normalized = Path(video).name  # Lấy tên file (bỏ đường dẫn nếu có)
            video_stem = Path(video_normalized).stem.lower()  # Đồng bộ với hybrid._norm_stem (FAISS meta)
            
            text = item.get('text', '')
            video_map.setdefault(video_stem, []).append(text)

        sources = []
        for video_stem, chunks in video_map.items():
            sources.append({
                'video': video_stem,  # FE expects stem (không có extension, không có timestamp nếu đã normalize)
                'chunks': chunks,
                'num_chunks': len(chunks),
                'can_query': True,
            })

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

    if not (q or "").strip():
        return jsonify({'error': 'Missing query'}), 400

    # Limit concurrent query threads
    acquired = _query_semaphore.acquire(blocking=False)
    if not acquired:
        return jsonify({"error": "Too many concurrent queries, please retry."}), 429

    job_id = str(uuid.uuid4())
    if not session_id:
        session_id = str(uuid.uuid4())
    try:
        from app.domains.jobs.jobs_store import create_job as _js_create
        _js_create(job_id, job_type="query", status="pending", progress=0, current_node="Queued")
    except Exception:
        pass
    with query_jobs_lock:
        query_jobs[job_id] = {
            "status": "pending",
            "result": None,
            "error": None,
            "created_at": time.time(),
        }

    def process_query_job(jid: str, question: str, sources: list, use_mem: bool, category: str | None = None, language: str | None = None) -> None:
        start_ts = time.time()
        try:
            with query_jobs_lock:
                if jid in query_jobs:
                    query_jobs[jid]["status"] = "running"

            if QUERY_GRAPH is None:
                raise RuntimeError("QUERY_GRAPH chưa khởi tạo — kiểm tra logs khởi động.")

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
            init_state = {
                "job_id": jid,
                "session_id": session_id,
                "conversation_history": history,
                "q": question,
                "selected_sources": sources or [],
                "use_memory_tree": bool(use_mem),
                "category": category,
                "language": language,
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
            _finalize_query_job(jid, session_id, question, out)
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
        return jsonify({"error": "Too many concurrent queries, please retry."}), 429

    def resume_job() -> None:
        try:
            from langgraph.types import Command
            out = _langgraph_invoke(QUERY_GRAPH, None, thread_id=tid, command=Command(resume=decision))
            review = _detect_query_interrupt(QUERY_GRAPH, tid)
            if review is not None:
                _mark_query_interrupted(job_id, review)
                return
            _finalize_query_job(job_id, session_id, question, out)
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
# 📝 Summarize file
# -------------------------
@app.post('/summarize-file')
def summarize_file():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'Missing file'}), 400

    save_path = os.path.join(INPUT_DIR, file.filename)
    file.save(save_path)

    text = extract_text(save_path)
    if not text.strip():
        return jsonify({'error': 'Cannot read file content'}), 400

    summary = summarize_whole_document(text)
    return jsonify({'summary': summary})


# -------------------------
# 📚 Advanced Summarize Documents
# -------------------------
@app.post('/summarize-documents')
def summarize_documents():
    """
    Tóm tắt tài liệu theo các công thức nâng cao:
    - ATS Process (D, M, G, E)
    - DANCER (Divide-and-Conquer)
    - Entity Chain Planning
    - Chain of Density
    - Structured Extraction
    - FactCC
    """
    try:
        data = request.json or {}
        sources = data.get('sources') or []
        
        if not sources or not isinstance(sources, list):
            return jsonify({'error': 'Missing or invalid sources'}), 400
        
        # Lấy text từ các sources đã index
        with open(INDEX_META_JSON_PATH, encoding='utf-8') as f:
            meta = json.load(f)
        
        # Normalize source names (giống logic trong generate-mindmap)
        def normalize_video_name(name: str) -> str:
            if not name:
                return ""
            name = Path(name).name if '/' in name or '\\' in name else name
            cleaned = unicodedata.normalize('NFKD', name.strip()).replace('\u00a0', ' ')
            cleaned = cleaned.replace('.mp4', '')
            cleaned = re.sub(r'_\d{8}_\d{6}$', '', cleaned)
            return cleaned.strip().lower()
        
        normalized_sources = set()
        for s in sources:
            normalized = normalize_video_name(s)
            if normalized:
                normalized_sources.add(normalized)
        
        # Lấy tất cả chunks từ các sources đã chọn
        all_texts = []
        for key, m in meta.items():
            video_raw = m.get("video", "").strip()
            if not video_raw:
                continue
            video_clean = normalize_video_name(video_raw)
            if video_clean in normalized_sources:
                text = m.get("text", "").strip()
                if text:
                    all_texts.append(text)
        
        if not all_texts:
            return jsonify({'error': 'No content found for selected sources'}), 404
        
        # Ghép tất cả text lại (đã là chunk từ ingest, không cần tách lại)
        combined_text = "\n\n".join(all_texts)
        
        # Cấu hình các phương pháp (có thể override từ request)
        use_dancer = data.get('use_dancer', True)
        use_entity_chain = data.get('use_entity_chain', True)
        use_cod = data.get('use_cod', True)
        use_structured = data.get('use_structured', True)
        use_fact_check = data.get('use_fact_check', True)
        
        # Gọi hàm tóm tắt nâng cao
        result = advanced_summarize(
            text=combined_text,
            pre_chunks=all_texts,  # dùng lại chunk đã có, tránh tách lại
            use_dancer=use_dancer,
            use_entity_chain=use_entity_chain,
            use_cod=use_cod,
            use_structured=use_structured,
            use_fact_check=use_fact_check,
            model=SLM_MODEL_SUMMARY
        )
        result["sources"] = list(normalized_sources)
        return jsonify(result)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# -------------------------
# 💾 Save & list summaries
# -------------------------
@app.route('/summaries', methods=['OPTIONS'])
def summaries_options():
    return jsonify({"ok": True}), 200


@app.post('/summaries')
def save_summary():
    try:
        payload = request.json or {}
        title = (payload.get("title") or "").strip() or "Tóm tắt"
        data = payload.get("data") or {}
        sources = payload.get("sources") or []

        # Chuẩn hóa tiêu đề để tránh trùng (case/space)
        def _norm(s: str) -> str:
            return unicodedata.normalize("NFKD", s or "").strip().lower()

        records = _load_summaries()
        norm_title = _norm(title)

        # Nếu đã tồn tại tiêu đề tương tự -> cập nhật thay vì thêm mới
        existing_idx = next((i for i, r in enumerate(records) if _norm(r.get("title")) == norm_title), None)

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if existing_idx is not None:
            # Cập nhật bản ghi cũ, giữ nguyên id/createdAt nếu có
            existing = records[existing_idx]
            updated = {
                "id": existing.get("id") or str(uuid.uuid4()),
                "title": title,
                "data": data,
                "sources": sources,
                "createdAt": existing.get("createdAt") or now_iso,
                "updatedAt": now_iso,
            }
            records[existing_idx] = updated
            _save_summaries(records)
            return jsonify({"message": "Updated", "summary": updated})

        # Nếu chưa có, thêm mới
        record = {
            "id": str(uuid.uuid4()),
            "title": title,
            "data": data,
            "sources": sources,
            "createdAt": now_iso,
        }
        _append_summary(record)
        return jsonify({"message": "Saved", "summary": record})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get('/summaries')
def list_summaries():
    records = _load_summaries()
    return jsonify({"summaries": records})


@app.delete('/summaries/<string:summary_id>')
def delete_summary(summary_id: str):
    try:
        records = _load_summaries()
        new_records = [
            r for r in records
            if str(r.get("id")) != str(summary_id)
            and str(r.get("data", {}).get("id")) != str(summary_id)
        ]
        removed = len(records) - len(new_records)
        if removed == 0:
            return jsonify({"error": "Summary not found"}), 404
        _save_summaries(new_records)
        return jsonify({"message": "Deleted", "removed": removed})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# -------------------------
# 🗑️ Delete source
# -------------------------
@app.post('/delete-source')
def delete_source():
    data = request.json or {}
    video_name = data.get('video', '')

    if not video_name:
        return jsonify({'error': 'Missing video name'}), 400

    # FE gửi stem -> normalize
    video_stem = unicodedata.normalize('NFKD', video_name.strip()).replace('\u00a0', ' ').replace('.mp4', '').lower()

    meta_path = INDEX_META_JSON_PATH
    if not meta_path.exists():
        return jsonify({'error': 'No index metadata found'}), 404

    try:
        with open(meta_path, encoding='utf-8') as f:
            meta = json.load(f)

        # Tìm danh sách stored video names có stem khớp
        stored_names = set()
        for v in meta.values():
            stored_video = unicodedata.normalize('NFKD', v.get('video', '').strip()).replace('\u00a0', ' ')
            if Path(stored_video).stem.lower() == video_stem:
                stored_names.add(stored_video)

        if not stored_names:
            return jsonify({'message': 'No matching source found', 'removed': 0})

        removed_total = 0
        # Gọi delete_source_from_index cho từng stored name (vector_store sẽ rebuild index)
        for stored in stored_names:
            delete_source_from_index(stored)
            # count removed in meta by checking previous entries (best-effort)
            removed_total += sum(1 for v in meta.values() if Path(unicodedata.normalize('NFKD', v.get('video', '').strip()).replace('\u00a0',' ')).stem.lower() == video_stem)

        # Xóa file video vật lý (match by stem)
        for f in Path(VIDEOS_DIR).glob(f"{video_stem}*"):
            try:
                f.unlink()
            except Exception as e:
                print("⚠️ Could not delete video file:", f, e)

        return jsonify({'message': 'Deleted', 'removed': removed_total})

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500

def run_mindmap_job(job_id: str, source_names: list[str], generation_mode: str) -> None:
    """Background: sinh mindmap + lưu file; cập nhật mindmap_jobs thread-safe."""
    attach_mindmap_job_context(job_id)
    try:
        with mindmap_jobs_lock:
            if job_id in mindmap_jobs:
                mindmap_jobs[job_id]["status"] = "running"
                mindmap_jobs[job_id]["progress"] = max(int(mindmap_jobs[job_id].get("progress") or 0), 5)
                mindmap_jobs[job_id]["current_node"] = f"Chế độ: {generation_mode}"

        if MINDMAP_GRAPH is None:
            raise RuntimeError("MINDMAP_GRAPH chưa khởi tạo — kiểm tra logs khởi động.")

        init_state = {
            "job_id": job_id,
            "source_names": source_names,
            "strategy": generation_mode,  # strategy = mode để worker hiểu
            "result": {},
            "progress": 0,
            "current_node": f"Chế độ: {generation_mode}",
            "error": None,
        }
        out = _langgraph_invoke(MINDMAP_GRAPH, init_state, thread_id=job_id)
        record = out.get("result") or {}

        with mindmap_jobs_lock:
            if job_id in mindmap_jobs:
                mindmap_jobs[job_id]["status"] = "done"
                mindmap_jobs[job_id]["progress"] = 100
                mindmap_jobs[job_id]["result"] = _mindmap_response(record)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        with mindmap_jobs_lock:
            if job_id in mindmap_jobs:
                mindmap_jobs[job_id]["status"] = "error"
                mindmap_jobs[job_id]["error"] = str(exc)
        print(f"[MINDMAP_JOB] job_id={job_id} failed: {exc}")
    finally:
        attach_mindmap_job_context(None)


# -------------------------
@app.post("/generate-mindmap")
def generate_mindmap():
    _cleanup_old_mindmap_jobs()
    try:
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

        # ========== PHASE 1: Parse mode and strategy SEPARATELY ==========
        # Parse generation_mode: fast | balanced | quality
        raw_mode = (data.get("mode") or data.get("generation_mode") or "").strip().lower()
        if raw_mode and raw_mode in VALID_MODES:
            generation_mode = raw_mode
        else:
            generation_mode = os.getenv("MINDMAP_GENERATION_MODE", "balanced")
            if generation_mode not in VALID_MODES:
                generation_mode = "balanced"

        # Parse strategy_requested: auto | single_call_schema | mindmap_v2 | ...
        raw_strategy = (data.get("strategy") or data.get("method") or "auto").strip().lower()
        if raw_strategy in VALID_STRATEGIES:
            strategy_requested = raw_strategy
        else:
            strategy_requested = "auto"

        # ========== PHASE 2: Guard iterative ==========
        # Block iterative for non-quality modes
        if strategy_requested == "iterative" and generation_mode != "quality":
            print(f"[MindMap Guard] iterative requested with mode={generation_mode}, downgrading to auto")
            strategy_requested = "auto"

        if generation_mode in {"fast", "balanced"} and strategy_requested in {"cmgn", "iterative"}:
            print(f"[MindMap Guard] strategy={strategy_requested} is too slow for mode={generation_mode}, downgrading to auto")
            strategy_requested = "auto"

        # ========== PHASE 3: Log request ==========
        job_timeout = get_job_timeout_for_mode(generation_mode)
        llm_timeout = get_llm_timeout_for_mode(generation_mode)
        print("[MindMap Request]", {
            "mode": generation_mode,
            "strategy_requested": strategy_requested,
            "jobTimeout": job_timeout,
            "llmTimeoutPerCall": llm_timeout,
            "sources_count": len(source_names),
        })

        job_id = str(uuid.uuid4())
        try:
            from app.domains.jobs.jobs_store import create_job as _js_create
            _js_create(job_id, job_type="mindmap", status="pending", progress=0, current_node="Queued")
        except Exception:
            pass
        with mindmap_jobs_lock:
            mindmap_jobs[job_id] = {
                "status": "pending",
                "progress": 0,
                "result": None,
                "error": None,
                "created_at": time.time(),
            }

        thread = threading.Thread(
            target=run_mindmap_job,
            args=(job_id, source_names, generation_mode),
            daemon=True,
        )
        thread.start()

        return jsonify({
            "job_id": job_id,
            "status": "started",
            "generation_mode": generation_mode,
        }), 202

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/mindmap-status/<job_id>")
def mindmap_status(job_id: str):
    _cleanup_old_mindmap_jobs()
    # Uu tien SQLite jobs store neu bat
    if (os.getenv("USE_SQLITE_JOBS", "1") or "").strip() not in ("0", "false", "False"):
        try:
            from app.domains.jobs.jobs_store import get_job as _js_get
            j = _js_get(job_id)
            if j and j.get("job_type") in ("mindmap", None):
                # HARD TIMEOUT: Neu job running qua jobTimeout + 10s, mark timeout
                if j.get("status") == "running":
                    started = j.get("started_at", 0)
                    timeout = j.get("jobTimeout", 180)
                    if started > 0:
                        elapsed = time_module.time() - started
                        if elapsed > timeout + 10:
                            # Mark as timeout
                            j["status"] = "timeout"
                            j["progress"] = 100
                            j["error"] = f"Mindmap job exceeded timeout: {elapsed:.1f}s > {timeout}s"
                            try:
                                from app.domains.jobs.jobs_store import update_job as _js_update
                                _js_update(job_id, j)
                            except Exception:
                                pass
                return jsonify({
                    "status": j.get("status"),
                    "progress": j.get("progress", 0),
                    "result": j.get("result"),
                    "error": j.get("error"),
                }), 200
        except Exception:
            pass
    with mindmap_jobs_lock:
        job = mindmap_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        # HARD TIMEOUT: Neu job running qua jobTimeout + 10s, mark timeout
        if job.get("status") == "running":
            started = job.get("started_at", 0)
            timeout = job.get("jobTimeout", 180)
            if started > 0:
                elapsed = time_module.time() - started
                if elapsed > timeout + 10:
                    job["status"] = "timeout"
                    job["progress"] = 100
                    job["error"] = f"Mindmap job exceeded timeout: {elapsed:.1f}s > {timeout}s"
                    print(f"[MindMap Status] Job {job_id} marked timeout: {elapsed:.1f}s > {timeout}s")
        return jsonify({
            "status": job.get("status"),
            "progress": job.get("progress", 0),
            "result": job.get("result"),
            "error": job.get("error"),
        }), 200


@app.get('/mindmaps')
def list_mindmaps():
    records = _load_mindmaps()
    return jsonify({"mindmaps": [_mindmap_response(r) for r in records]})


@app.delete('/mindmaps/<string:mindmap_id>')
def delete_mindmap(mindmap_id: str):
    records = _load_mindmaps()
    new_records = [r for r in records if r.get("id") != mindmap_id]
    if len(new_records) == len(records):
        return jsonify({"error": "Mind map not found"}), 404
    _save_mindmaps(new_records)
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
    filename = source_info.get("filename")
    if not filename:
        return True  # Không có filename, coi như đã xóa
    
    input_file_path = Path(INPUT_DIR) / filename
    if input_file_path.exists():
        try:
            input_file_path.unlink()
            print(f"🗑️ [Delete] Đã xóa input file: {input_file_path}")
            return True
        except Exception as e:
            print(f"⚠️ [Delete] Không thể xóa input file {input_file_path}: {e}")
            return False
    return True  # File không tồn tại, coi như OK


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
    
    # Tìm tất cả file video có thể liên quan
    # Pattern 1: videos/{source_stem}*.mp4
    # Pattern 2: videos/{source_id}*.mp4
    patterns = [f"{source_stem}*", f"{source_id}*"]
    
    for pattern in patterns:
        for video_file in videos_root.glob(pattern):
            try:
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
    source_id = (source_id or "").strip()
    if not source_id:
        return jsonify({"error": "Missing source_id"}), 400
    
    source_stem = _normalize_video_stem(source_id)
    
    # 1️⃣ VALIDATE: Kiểm tra source có tồn tại không
    exists, source_info = _validate_source_exists(source_id, source_stem)
    if not exists:
        return jsonify({"error": "Source not found"}), 404
    
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
