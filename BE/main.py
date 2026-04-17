import os
import unicodedata
import json
import re
import uuid
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any, Callable
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# File locking (Unix only, fallback on Windows)
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

from ingest_utils import extract_text, split_text
from video_utils import  save_qr_frames_to_video
from faiss_utils import (
    append_to_index,
    search_index,
    delete_source_from_index,
    delete_chunks_by_source,
    rebuild_chunk_index,
    MODEL_NAME,
)
from ollama_utils import summarize_whole_document, summarize_results, SLM_MODEL, SLM_MODEL_SUMMARY
from mindmap_generation_worker import run_mindmap_generation
from chunk_processor import process_and_store_chunks
from summarize_advanced import advanced_summarize
from memory_tree import (
    build_memory_tree_for_sources,
    query_with_memory_tree,
    delete_memory_tree_by_source,
    rebuild_memory_index,
    _normalize_video_stem,
)
app = Flask(__name__)

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


BASE_DIR = Path(__file__).resolve().parent

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

# Lightweight in-memory query cache (phù hợp offline, giảm gọi Ollama)
QUERY_CACHE_MAX_SIZE = int(os.environ.get("QUERY_CACHE_MAX_SIZE", "128"))
QUERY_CACHE_TTL_SEC = int(os.environ.get("QUERY_CACHE_TTL_SEC", "300"))
_query_cache: "OrderedDict[str, dict]" = OrderedDict()
_query_cache_lock = threading.Lock()

# File lock để chặn rebuild đồng thời giữa nhiều gunicorn workers
REBUILD_LOCK_PATH = INDEX_DIR / ".rebuild.lock"

# In-memory async job manager (giữ offline, nhẹ)
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()
JOB_TTL_MINUTES = int(os.environ.get("JOB_TTL_MINUTES", "30"))

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


def _make_query_cache_key(q: str, selected_sources: list, use_memory_tree: bool) -> str:
    # Normalize list để key ổn định theo thứ tự chọn
    sources_norm = selected_sources or []
    sources_norm = [str(s) for s in sources_norm if s is not None]
    sources_norm = sorted(sources_norm)
    return json.dumps(
        {"q": (q or "").strip(), "sources": sources_norm, "use_memory_tree": bool(use_memory_tree)},
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
    return jsonify({
        "status": "ok",
        "mode": "ci" if os.environ.get("SKIP_MODEL_LOAD") == "1" else "normal",
    }), 200

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

            from rebuild_index_from_video import rebuild_faiss_index_from_videos

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
        # Ưu tiên dùng source_stem đã lưu, fallback normalize filename
        stored_stem = info.get("source_stem")
        if stored_stem:
            if stored_stem == source_stem:
                return info
        else:
            # Fallback: normalize filename
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
            # Nếu không tìm thấy trong registry, coi như ready (legacy source)
            status_map[stem] = "ready"
    return status_map


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
    return {
        "id": record.get("id"),
        "title": record.get("title"),
        "nodes": nodes,
        "sources": record.get("sources", []),
        "createdAt": record.get("createdAt"),
        "strategy": record.get("strategy") or "iterative",
    }


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


def _background_process_source(source_id: str, file_path: str, filename: str):
    """
    Background worker: Xử lý toàn bộ ingest pipeline cho một source theo kiến trúc async 2-phase.
    
    Phase 1 (critical path - chạy trong thread này):
    - Extract → Chunking → Video → Embedding + FAISS
    - Sau khi xong: status = "index_ready", progress = 0.7, substatus = "faiss_ready"
    - capabilities: {chunk_query: true, memory_query: false}
    
    Phase 2 (background thread - không block):
    - Build Memory Tree (Document + Section nodes)
    - Trong quá trình build: status = "index_ready", progress = 0.8, substatus = "building_memory_tree"
    - Sau khi xong: status = "ready", progress = 1.0, substatus = "memory_tree_ready"
    - capabilities: {chunk_query: true, memory_query: true}
    
    Status lifecycle: processing → index_ready → ready
    (TUYỆT ĐỐI không quay lại processing sau index_ready)
    """
    try:
        print(f"🔄 [Background] Bắt đầu xử lý source: {source_id}")
        
        # ============================================
        # PHASE 1: CRITICAL PATH (Extract → FAISS)
        # ============================================
        
        # Step 1: Extract text (progress 0.1)
        _update_source_status(source_id, "processing", progress=0.1)
        text = extract_text(file_path)
        if not text.strip():
            raise ValueError("Cannot read file content")
        print(f"[INGEST] source={source_id} extracted_chars={len(text)}")
        
        # Step 2: Semantic chunking (progress 0.3)
        _update_source_status(source_id, "processing", progress=0.3)
        chunks = split_text(text)
        if not chunks:
            raise ValueError("No chunks generated")
        print(f"[INGEST] source={source_id} semantic_chunks={len(chunks)}")
        
        # Step 3: Process chunks và tạo video (progress 0.4)
        _update_source_status(source_id, "processing", progress=0.4)
        video_name = f"{filename.replace('.', '_')}"
        timestamp = datetime.now().isoformat()
        video_path, metadata_entries = process_and_store_chunks(
            chunks=chunks,
            video_name=video_name,
            timestamp=timestamp
        )
        print(f"[INGEST] source={source_id} video={video_path} frames={len(metadata_entries)}")
        
        # Step 4: Embedding + FAISS index (progress 0.5 → 0.7)
        _update_source_status(source_id, "processing", progress=0.5)
        
        # Batch append để tối ưu tốc độ
        all_chunks = [entry["text"] for entry in metadata_entries]
        all_metadata = [{
            "parent_id": entry.get("parent_id"),
            "sub_order": entry.get("sub_order"),
            "total_parts": entry.get("total_parts"),
            "is_subchunk": entry.get("is_subchunk", False)
        } for entry in metadata_entries]
        
        append_to_index(
            chunks=all_chunks,
            video_name=video_path,
            custom_metadata=all_metadata,
            batch_size=32
        )
        
        # Step 5: Phase 1 hoàn thành - Index ready
        # status = "index_ready", progress = 0.7, substatus = "faiss_ready"
        # capabilities: chunk_query = true, memory_query = false
        source_stem = Path(video_name).stem.lower()
        _update_source_status(
            source_id, 
            status="index_ready", 
            progress=0.7,
            substatus="faiss_ready",
            capabilities={"chunk_query": True, "memory_query": False}
        )
        print(f"✅ [Background] Phase 1 hoàn thành - FAISS index ready cho source: {source_id}")
        print(f"   → Có thể query chunk-level ngay bây giờ")
        
        # ============================================
        # PHASE 2: BACKGROUND THREAD (Memory Tree)
        # ============================================
        
        def build_memory_tree_async():
            """
            Phase 2: Build Memory Tree trong thread phụ.
            KHÔNG block _background_process_source, KHÔNG join thread.
            """
            try:
                # Bắt đầu build Memory Tree
                # Giữ status = "index_ready", chỉ update progress và substatus
                _update_source_status(
                    source_id,
                    status="index_ready",  # GIỮ NGUYÊN status, không quay lại processing
                    progress=0.8,
                    substatus="building_memory_tree"
                )
                print(f"🔄 [Background] Phase 2 bắt đầu - Build Memory Tree cho source: {source_id}")
                
                # Build Memory Tree (Document + Section nodes)
                build_memory_tree_for_sources([source_stem])
                
                # Phase 2 hoàn thành
                # status = "ready", progress = 1.0, substatus = "memory_tree_ready"
                # capabilities: chunk_query = true, memory_query = true
                _update_source_status(
                    source_id,
                    status="ready",
                    progress=1.0,
                    substatus="memory_tree_ready",
                    capabilities={"chunk_query": True, "memory_query": True}
                )
                print(f"✅ [Background] Phase 2 hoàn thành - Memory Tree ready cho source: {source_id}")
                print(f"   → Có thể query cả chunk-level và memory-level")
                
            except Exception as exc:
                import traceback
                traceback.print_exc()
                error_msg = f"Memory Tree build failed: {str(exc)}"
                # Nếu Memory Tree build fail, vẫn giữ index_ready để query chunk-level
                # Nhưng ghi error message vào registry
                _update_source_status(
                    source_id,
                    status="index_ready",  # Giữ nguyên status index_ready
                    progress=0.7,
                    substatus="faiss_ready",  # Quay lại substatus faiss_ready
                    error=error_msg
                )
                print(f"⚠️ [Background] Memory Tree build failed cho {source_id}, nhưng index_ready: {error_msg}")
        
        # Trigger Memory Tree build trong thread riêng (daemon=True, không join)
        memory_tree_thread = threading.Thread(target=build_memory_tree_async, daemon=True)
        memory_tree_thread.start()
        print(f"🚀 [Background] Đã trigger Phase 2 (Memory Tree build) cho source: {source_id}")
        
    except Exception as exc:
        import traceback
        traceback.print_exc()
        error_msg = str(exc)
        # Bất kỳ lỗi nào trong Phase 1 → status = "error"
        _update_source_status(source_id, "error", progress=0.0, error=error_msg)
        print(f"❌ [Background] Lỗi xử lý source {source_id}: {error_msg}")


def _trigger_background_ingest(source_id: str, file_path: str, filename: str):
    """
    Trigger background task để xử lý ingest (non-blocking).
    """
    thread = threading.Thread(
        target=_background_process_source,
        args=(source_id, file_path, filename),
        daemon=True
    )
    thread.start()
    print(f"🚀 [Background] Đã trigger ingest cho source: {source_id}")


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
            video_stem = Path(video_normalized).stem  # Bỏ extension
            
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


def _legacy_query_flat_chunks(q: str, selected_sources: list[str] | None = None):
    """
    Logic query cũ: search trực tiếp trên chunk-level, dùng tóm tắt kết quả.
    Giữ lại làm fallback khi chưa có Memory Tree.
    """
    selected_sources = selected_sources or []
    all_chunks = search_index(q)
    chunks_with_file: list[str] = []

    try:
        with open(INDEX_META_JSON_PATH, encoding='utf-8') as f:
            meta = json.load(f)
    except Exception as e:
        return {'error': 'No index metadata found', 'detail': str(e)}, 500

    meta_norm = {}
    for k, m in meta.items():
        if not isinstance(k, str) or not k.isdigit():
            continue
        if not isinstance(m, dict):
            continue
        video_raw = m.get('video', '').strip()
        video_name = Path(video_raw).name
        video_stem = unicodedata.normalize('NFKD', Path(video_name).stem).replace('\u00a0', ' ').lower()
        meta_norm[k] = {
            'text': m.get('text', ''),
            'video_stem': video_stem
        }

    selected_norm = set()
    for s in selected_sources:
        try:
            selected_norm.add(
                unicodedata.normalize('NFKD', Path(s).stem).replace('\u00a0', ' ').lower()
            )
        except Exception as e:
            print("⚠️ Lỗi normalize source:", s, e)

    for chunk in all_chunks:
        for k, m_norm in meta_norm.items():
            if m_norm['text'] == chunk:
                if not selected_sources or m_norm['video_stem'] in selected_norm:
                    chunks_with_file.append(f"[FILE: {m_norm['video_stem']}]\n{chunk}")
                break

    if not chunks_with_file:
        if selected_sources:
            for m_norm in meta_norm.values():
                if m_norm['video_stem'] in selected_norm:
                    chunks_with_file.append(f"[FILE: {m_norm['video_stem']}]\n{m_norm['text']}")
        else:
            for m_norm in meta_norm.values():
                chunks_with_file.append(f"[FILE: {m_norm['video_stem']}]\n{m_norm['text']}")

    if not chunks_with_file:
        return {'answer': "Không tìm thấy dữ liệu phù hợp trong file đã chọn."}, 200

    answer = summarize_results(q, chunks_with_file, model=SLM_MODEL)
    return {'answer': answer}, 200


def _run_query_pipeline(q: str, selected_sources: list[str] | None, use_memory_tree: bool) -> tuple[dict, int]:
    """
    Giữ nguyên logic /query hiện tại nhưng trả về (payload, status_code).
    Dùng cho async background job để tránh block request.
    """
    selected_sources = selected_sources or []
    q = (q or "").strip()
    if not q:
        return {"error": "Missing query"}, 400

    # Check source status nếu có selected_sources
    processing_message = None
    if selected_sources:
        sources_status = _check_sources_status(selected_sources)

        # Nếu có source đang error -> trả error message
        error_sources = [s for s, status in sources_status.items() if status == "error"]
        if error_sources:
            error_info = _get_source_status_by_stem(error_sources[0])
            error_msg = error_info.get("error", "Source processing error") if error_info else "Source processing error"
            return {
                "error": f"Một hoặc nhiều tài liệu đã gặp lỗi: {error_msg}",
                "answer": None
            }, 400

        # Nếu có source đang processing -> cho phép query nhưng thông báo
        processing_sources = [s for s, status in sources_status.items() if status == "processing"]
        if processing_sources:
            processing_message = "Một số tài liệu đang được xử lý, mình sẽ trả lời đầy đủ hơn khi xong."

    cache_key: Optional[str] = None
    if not processing_message:
        cache_key = _make_query_cache_key(q, selected_sources, use_memory_tree)
        cached = _get_cached_query(cache_key)
        if cached and isinstance(cached, dict) and cached.get("payload"):
            payload = cached["payload"]
            status = int(cached.get("status", 200))
            return payload, status

    # 1) Thử query qua Memory Tree trước
    if use_memory_tree:
        try:
            print(f"[QUERY] q={q!r} use_memory_tree={use_memory_tree} selected_sources={selected_sources}")
            mem_result = query_with_memory_tree(q, selected_sources=selected_sources)
        except Exception:
            import traceback
            traceback.print_exc()
            mem_result = None

        if mem_result and isinstance(mem_result, dict) and mem_result.get("answer"):
            if processing_message:
                mem_result["processing_message"] = processing_message
            if cache_key:
                _set_cached_query(cache_key, {"payload": mem_result, "status": 200})
            return mem_result, 200

    # 2) Fallback: logic chunk-level cũ
    payload, status = _legacy_query_flat_chunks(q, selected_sources)
    if processing_message and isinstance(payload, dict):
        payload["processing_message"] = processing_message
    if cache_key and isinstance(payload, dict) and payload.get("answer"):
        _set_cached_query(cache_key, {"payload": payload, "status": status})
    return payload, status


# -------------------------
# 🔍 Query (Memory Tree first, fallback chunk)
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

    if not (q or "").strip():
        return jsonify({'error': 'Missing query'}), 400

    # Limit concurrent query threads
    acquired = _query_semaphore.acquire(blocking=False)
    if not acquired:
        return jsonify({"error": "Too many concurrent queries, please retry."}), 429

    job_id = str(uuid.uuid4())
    with query_jobs_lock:
        query_jobs[job_id] = {
            "status": "pending",
            "result": None,
            "error": None,
            "created_at": time.time(),
        }

    def process_query_job(jid: str, question: str, sources: list, use_mem: bool) -> None:
        start_ts = time.time()
        try:
            with query_jobs_lock:
                if jid in query_jobs:
                    query_jobs[jid]["status"] = "running"

            payload, status = _run_query_pipeline(question, sources, use_mem)
            result_obj = {
                "payload": payload,
                "status": status,
            }

            with query_jobs_lock:
                if jid in query_jobs:
                    query_jobs[jid]["status"] = "done"
                    query_jobs[jid]["result"] = result_obj
        except Exception as exc:
            with query_jobs_lock:
                if jid in query_jobs:
                    query_jobs[jid]["status"] = "error"
                    query_jobs[jid]["error"] = str(exc)
            print(f"[QUERY_JOB] job_id={jid} failed: {exc}")
        finally:
            elapsed = time.time() - start_ts
            if elapsed > QUERY_JOB_TIMEOUT_SEC:
                print(f"[QUERY_JOB] job_id={jid} exceeded timeout={QUERY_JOB_TIMEOUT_SEC}s (elapsed={elapsed:.1f}s)")
            _query_semaphore.release()

    thread = threading.Thread(
        target=process_query_job,
        args=(job_id, q, selected_sources, use_memory_tree),
        daemon=True
    )
    thread.start()

    # Return immediately (no blocking)
    return jsonify({"job_id": job_id, "status": "pending"}), 202


@app.get('/query-status/<job_id>')
def query_status(job_id: str):
    _cleanup_old_query_jobs()
    with query_jobs_lock:
        job = query_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({
            "status": job.get("status"),
            "result": job.get("result"),
            "error": job.get("error"),
        }), 200

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
        # Gọi delete_source_from_index cho từng stored name (faiss_utils sẽ rebuild index)
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

def run_mindmap_job(job_id: str, source_names: list[str], strategy_requested: str) -> None:
    """Background: sinh mindmap + lưu file; cập nhật mindmap_jobs thread-safe."""

    def set_progress(p: int) -> None:
        with mindmap_jobs_lock:
            j = mindmap_jobs.get(job_id)
            if j is not None:
                j["progress"] = min(100, max(0, int(p)))

    try:
        with mindmap_jobs_lock:
            if job_id in mindmap_jobs:
                mindmap_jobs[job_id]["status"] = "running"
                mindmap_jobs[job_id]["progress"] = max(int(mindmap_jobs[job_id].get("progress") or 0), 5)

        record = run_mindmap_generation(
            INDEX_META_JSON_PATH,
            source_names,
            strategy_requested,
            _append_mindmap,
            progress_cb=set_progress,
        )

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

        strategy_requested = (
            data.get("strategy") or data.get("mode") or data.get("method") or "iterative"
        ).strip().lower()

        job_id = str(uuid.uuid4())
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
            args=(job_id, source_names, strategy_requested),
            daemon=True,
        )
        thread.start()

        return jsonify({"job_id": job_id, "status": "started"}), 202

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/mindmap-status/<job_id>")
def mindmap_status(job_id: str):
    _cleanup_old_mindmap_jobs()
    with mindmap_jobs_lock:
        job = mindmap_jobs.get(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
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
        from memory_tree import _load_memory_trees, _normalize_video_stem
        
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
        from memory_tree import _load_memory_trees
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
        from memory_tree import _load_memory_trees, _normalize_video_stem
        
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
    app.run(host='0.0.0.0', port=5000, debug=debug, use_reloader=debug)
