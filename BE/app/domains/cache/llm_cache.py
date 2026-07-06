"""
Cache 3 tầng cho pipeline LLM/RAG — tầng 2 (semantic response) + tầng 3 (retrieval).

Spec đầy đủ: docs/SEMANTIC_CACHE_SPEC.md. Tóm tắt thiết kế:

- Semantic cache cắm vào `_get_cached_query`/`_set_cached_query` (main.py) — nhận
  cache_key JSON do `main._make_query_cache_key` sinh (đổi format bên đó → bên này
  parse fail → miss im lặng, hướng fail-safe).
- Bucket key encode MỌI điều kiện match (prompt_version, embedding model, index_version,
  sources, language, category, use_memory_tree) → chống cache poisoning theo cấu trúc:
  khác điều kiện = khác bucket, không bao giờ so cosine với nhau. Ingest/delete đổi
  mtime index.json → index_version đổi → bucket mới (tự invalidate, không cần xoá).
- Fail-open tuyệt đối: mọi lỗi Redis chỉ là cache miss (mark_unavailable + đếm errors).
- Chỉ cache câu hỏi low-risk (classify_risk); multi-turn đã bị chặn từ cache_lookup_node.
"""

from __future__ import annotations

import base64
import collections
import dataclasses
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from app.clients import redis_client
from app.clients.llm_factory import encode_query_cached  # monkeypatch điểm này trong test

try:
    from shared.env_loader import load_project_env
    load_project_env(override=False)
except Exception:
    pass

logger = logging.getLogger(__name__)

# Bump khi đổi system prompt của qa_chain._qa_messages (hoặc logic sinh answer)
# để tự vô hiệu mọi answer đã cache — cùng nguyên tắc PIPELINE_VERSION của mindmap.
PROMPT_VERSION = "qa-v1"

_NS = (os.getenv("CACHE_NAMESPACE", "memvid") or "memvid").strip()
_ENV = (os.getenv("CACHE_ENV", "dev") or "dev").strip()

_SEMANTIC_ENABLED = (os.getenv("SEMANTIC_CACHE_ENABLED", "1") or "").strip().lower() not in ("0", "false", "no", "off")
_RETRIEVAL_ENABLED = (os.getenv("RETRIEVAL_CACHE_ENABLED", "1") or "").strip().lower() not in ("0", "false", "no", "off")
_SEMANTIC_TTL = int(os.getenv("SEMANTIC_CACHE_TTL_SECONDS", "172800"))   # 48h
_RETRIEVAL_TTL = int(os.getenv("RETRIEVAL_CACHE_TTL_SECONDS", "3600"))   # 1h
_BUCKET_SCAN_CAP = 200            # trần số entry so cosine mỗi lookup (bucket vốn nhỏ)
_NEAR_THRESHOLD_MARGIN = 0.03     # log các cú suýt-hit để tune threshold
_LLM_AVG_MS = int(os.getenv("SEMANTIC_CACHE_LLM_AVG_MS", "20000"))  # ước lượng latency tiết kiệm/hit

_THRESHOLD_FLOOR = 0.80


def _resolve_threshold() -> float:
    raw = float(os.getenv("SEMANTIC_CACHE_THRESHOLD", "0.85"))
    override = (os.getenv("SEMANTIC_CACHE_THRESHOLD_FLOOR_OVERRIDE", "0") or "").strip().lower() in ("1", "true", "yes", "on")
    if raw < _THRESHOLD_FLOOR and not override:
        logger.warning(
            "[cache] SEMANTIC_CACHE_THRESHOLD=%.2f dưới sàn %.2f — nguy cơ cache poisoning; "
            "đã clamp về %.2f. Muốn thấp hơn phải set SEMANTIC_CACHE_THRESHOLD_FLOOR_OVERRIDE=1.",
            raw, _THRESHOLD_FLOOR, _THRESHOLD_FLOOR,
        )
        return _THRESHOLD_FLOOR
    return raw


THRESHOLD = _resolve_threshold()

# Counter per-process (gunicorn nhiều worker → số liệu per-worker; aggregate xem redis INFO)
METRICS: collections.Counter = collections.Counter()

# --- Risk classifier -------------------------------------------------------
# Allowlist tinh thần: chỉ cache low-risk (FAQ / giải thích tài liệu đã ingest).
# Deny khi câu hỏi mang tính cá nhân/tài khoản/bí mật hoặc cần dữ liệu realtime.
import re

_PERSONAL_RE = re.compile(
    r"tài khoản|mật khẩu|số dư|của tôi|của em|của mình|"
    r"my account|password|balance|my grade|api[ _-]?key|secret|token|credential",
    re.IGNORECASE,
)
_REALTIME_RE = re.compile(
    r"hôm nay|bây giờ|hiện tại|lúc này|mới nhất|"
    r"\btoday\b|\bnow\b|\bcurrent\b|\blatest\b|weather|thời tiết|\bgiá\b|\bprice\b",
    re.IGNORECASE,
)


def classify_risk(question: str) -> tuple:
    """(cacheable, risk_class). Deny = không ghi cache (đọc cũng không bao giờ hit vì chưa từng ghi)."""
    q = question or ""
    if _PERSONAL_RE.search(q):
        return (False, "personal")
    if _REALTIME_RE.search(q):
        return (False, "realtime")
    return (True, "low")


# --- index_version ---------------------------------------------------------
# Mirror đường dẫn META_PATH của app/domains/vectorstore/store.py (INDEX_DIR/index.json)
# — KHÔNG import store (kéo faiss+langchain nặng). _save_meta bên đó ghi atomic
# tmp→replace nên mtime đổi mỗi lần ingest/delete → dùng làm phiên bản index toàn cục.
def _meta_path() -> Path:
    try:
        from shared.paths import BE_ROOT
        data_root = Path(os.environ.get("DATA_DIR", str(BE_ROOT)))
    except Exception:
        data_root = Path(os.environ.get("DATA_DIR", "."))
    return Path(os.environ.get("INDEX_DIR", str(data_root / "index"))) / "index.json"


def index_version() -> str:
    """Phiên bản index rẻ tiền: stat mtime+size của index.json. Không có file → '0'."""
    try:
        st = os.stat(_meta_path())
        return f"{st.st_mtime_ns}-{st.st_size}"
    except OSError:
        return "0"


# --- key helpers ------------------------------------------------------------

def _norm_q(q: str) -> str:
    return (q or "").strip().lower()


def _bucket_id(sources: List[str], language: Optional[str], category: Optional[str], use_memory_tree: bool) -> str:
    emb_model = os.getenv("EMBEDDING_MODEL_NAME", "") or ""
    late = os.getenv("LATE_CHUNKING", "1") or ""
    parts = [
        _NS, _ENV, PROMPT_VERSION, emb_model, late, index_version(),
        "|".join(sorted(str(s) for s in (sources or []))),
        str(language or ""), str(category or ""), "1" if use_memory_tree else "0",
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _entry_key(bucket: str, eid: str) -> str:
    return f"{_NS}:{_ENV}:sc:{bucket}:e:{eid}"


def _ids_key(bucket: str) -> str:
    return f"{_NS}:{_ENV}:sc:{bucket}:ids"


def _eid(q_norm: str) -> str:
    return hashlib.sha256(q_norm.encode("utf-8")).hexdigest()[:16]


def _vec_to_b64(vec: np.ndarray) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float32).tobytes()).decode("ascii")


def _vec_from_b64(b64: str, dim: int) -> Optional[np.ndarray]:
    try:
        arr = np.frombuffer(base64.b64decode(b64), dtype=np.float32)
        return arr if arr.size == dim else None
    except Exception:
        return None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    # cosine đầy đủ — encode_query_cached KHÔNG đảm bảo vector đã normalize (late-chunk path)
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _parse_cache_key(cache_key: str) -> Optional[Dict[str, Any]]:
    """cache_key = JSON của main._make_query_cache_key. Parse fail → None (miss im lặng)."""
    try:
        d = json.loads(cache_key)
        if not isinstance(d, dict) or "q" not in d:
            return None
        return d
    except Exception:
        return None


# --- Tier 2: semantic response cache ----------------------------------------

def semantic_lookup(cache_key: str) -> Optional[dict]:
    """Trả {'payload':..., 'status':...} nếu hit (exact hoặc semantic), None nếu miss/bypass/lỗi."""
    if not _SEMANTIC_ENABLED:
        return None
    r = redis_client.get_redis()
    if r is None:
        return None
    d = _parse_cache_key(cache_key)
    if d is None:
        return None
    q = str(d.get("q") or "")
    q_norm = _norm_q(q)
    if not q_norm:
        return None
    bucket = _bucket_id(d.get("sources") or [], d.get("language"), d.get("category"), bool(d.get("use_memory_tree")))

    try:
        # 1) exact repeat: O(1), không cần embed
        raw = r.get(_entry_key(bucket, _eid(q_norm)))
        if raw:
            entry = json.loads(raw)
            METRICS["hits_exact"] += 1
            METRICS["saved_llm_calls"] += 1
            METRICS["latency_saved_ms"] += _LLM_AVG_MS
            logger.info("[cache] semantic exact hit bucket=%s", bucket)
            return {"payload": entry.get("payload"), "status": int(entry.get("status", 200))}

        # 2) semantic: embed câu hỏi rồi so cosine trong bucket
        vec = encode_query_cached(q)
        if vec is None:  # SKIP_MODEL_LOAD / CI / lỗi model
            METRICS["bypass_no_embedding"] += 1
            return None
        qv = np.asarray(vec, dtype=np.float32).reshape(-1)

        ids = list(r.smembers(_ids_key(bucket)))[:_BUCKET_SCAN_CAP]
        if not ids:
            METRICS["misses"] += 1
            return None
        keys = [_entry_key(bucket, i) for i in ids]
        raws = r.mget(keys)

        best_sim, best_entry, dead = -1.0, None, []
        for i, rw in zip(ids, raws):
            if not rw:
                dead.append(i)  # entry hết TTL nhưng id còn trong SET → dọn
                continue
            try:
                entry = json.loads(rw)
                ev = _vec_from_b64(entry.get("vec_b64", ""), int(entry.get("dim", 0)))
                if ev is None or ev.size != qv.size:
                    continue
                sim = _cosine(qv, ev)
                if sim > best_sim:
                    best_sim, best_entry = sim, entry
            except Exception:
                continue
        if dead:
            try:
                r.srem(_ids_key(bucket), *dead)
            except Exception:
                pass

        if best_entry is not None and best_sim >= THRESHOLD:
            METRICS["hits_semantic"] += 1
            METRICS["saved_llm_calls"] += 1
            METRICS["latency_saved_ms"] += _LLM_AVG_MS
            logger.info("[cache] semantic hit sim=%.4f bucket=%s q=%r ~ cached=%r",
                        best_sim, bucket, q[:80], str(best_entry.get("q", ""))[:80])
            return {"payload": best_entry.get("payload"), "status": int(best_entry.get("status", 200))}

        if best_entry is not None and best_sim >= THRESHOLD - _NEAR_THRESHOLD_MARGIN:
            logger.info("[cache] semantic near-threshold sim=%.4f (threshold=%.2f) q=%r",
                        best_sim, THRESHOLD, q[:80])
        METRICS["misses"] += 1
        return None
    except Exception as e:
        redis_client.mark_unavailable()
        METRICS["errors"] += 1
        logger.debug("[cache] semantic_lookup fail-open: %s", e)
        return None


def semantic_store(cache_key: str, value: dict) -> None:
    """Ghi answer vào semantic cache — chỉ khi câu hỏi được classify_risk cho phép."""
    if not _SEMANTIC_ENABLED:
        return
    r = redis_client.get_redis()
    if r is None:
        return
    d = _parse_cache_key(cache_key)
    if d is None or not isinstance(value, dict):
        return
    q = str(d.get("q") or "")
    q_norm = _norm_q(q)
    if not q_norm:
        return

    cacheable, risk_class = classify_risk(q)
    if not cacheable:
        METRICS["bypass_risk"] += 1
        logger.info("[cache] semantic store denied risk_class=%s q=%r", risk_class, q[:80])
        return

    vec = encode_query_cached(q)
    if vec is None:
        METRICS["bypass_no_embedding"] += 1
        return
    qv = np.asarray(vec, dtype=np.float32).reshape(-1)

    bucket = _bucket_id(d.get("sources") or [], d.get("language"), d.get("category"), bool(d.get("use_memory_tree")))
    now = time.time()
    entry = {
        "q": q,
        "q_norm": q_norm,
        "payload": value.get("payload"),
        "status": int(value.get("status", 200)),
        "created_at": now,
        "expires_at": now + _SEMANTIC_TTL,
        "model": os.getenv("SLM_MODEL_CHAT") or os.getenv("SLM_MODEL") or "",
        "prompt_version": PROMPT_VERSION,
        "index_version": index_version(),
        "risk_class": risk_class,
        "vec_b64": _vec_to_b64(qv),
        "dim": int(qv.size),
    }
    try:
        eid = _eid(q_norm)
        r.setex(_entry_key(bucket, eid), _SEMANTIC_TTL, json.dumps(entry, ensure_ascii=False))
        r.sadd(_ids_key(bucket), eid)
        r.expire(_ids_key(bucket), _SEMANTIC_TTL)
        METRICS["writes"] += 1
    except Exception as e:
        redis_client.mark_unavailable()
        METRICS["errors"] += 1
        logger.debug("[cache] semantic_store fail-open: %s", e)


# --- Tier 3: retrieval result cache ------------------------------------------

def _retrieval_key(query: str, sources: List[str], top_k: int,
                   category: Optional[str], language: Optional[str]) -> str:
    emb_model = os.getenv("EMBEDDING_MODEL_NAME", "") or ""
    parts = [
        _norm_q(query), "|".join(sorted(str(s) for s in (sources or []))),
        str(top_k), index_version(), emb_model, str(category or ""), str(language or ""),
    ]
    h = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{_NS}:{_ENV}:ret:{h}"


def retrieval_get(query: str, sources: List[str], top_k: int,
                  category: Optional[str] = None, language: Optional[str] = None) -> Optional[list]:
    """Trả list[RetrievedChunk] nếu hit, None nếu miss/lỗi."""
    if not _RETRIEVAL_ENABLED:
        return None
    r = redis_client.get_redis()
    if r is None:
        return None
    try:
        raw = r.get(_retrieval_key(query, sources, top_k, category, language))
        if not raw:
            METRICS["ret_misses"] += 1
            return None
        from app.domains.retrieval.hybrid import RetrievedChunk  # lazy: tránh import cycle
        chunks = [RetrievedChunk(**c) for c in json.loads(raw)]
        METRICS["ret_hits"] += 1
        return chunks
    except Exception as e:
        redis_client.mark_unavailable()
        METRICS["errors"] += 1
        logger.debug("[cache] retrieval_get fail-open: %s", e)
        return None


def retrieval_put(query: str, sources: List[str], top_k: int,
                  category: Optional[str], language: Optional[str], chunks: list) -> None:
    if not _RETRIEVAL_ENABLED or not chunks:
        return
    r = redis_client.get_redis()
    if r is None:
        return
    try:
        payload = json.dumps([dataclasses.asdict(c) for c in chunks], ensure_ascii=False)
        r.setex(_retrieval_key(query, sources, top_k, category, language), _RETRIEVAL_TTL, payload)
    except Exception as e:
        redis_client.mark_unavailable()
        METRICS["errors"] += 1
        logger.debug("[cache] retrieval_put fail-open: %s", e)


# --- Invalidation + stats -----------------------------------------------------

def invalidate_all() -> int:
    """Xoá toàn bộ key cache của namespace/env hiện tại. Best-effort, trả số key đã xoá."""
    r = redis_client.get_redis()
    if r is None:
        return 0
    deleted = 0
    try:
        batch = []
        for k in r.scan_iter(match=f"{_NS}:{_ENV}:*", count=500):
            batch.append(k)
            if len(batch) >= 500:
                deleted += int(r.delete(*batch) or 0)
                batch = []
        if batch:
            deleted += int(r.delete(*batch) or 0)
        if deleted:
            METRICS["stale_cache_invalidations"] += deleted
            logger.info("[cache] invalidate_all: xoá %d key", deleted)
    except Exception as e:
        redis_client.mark_unavailable()
        METRICS["errors"] += 1
        logger.debug("[cache] invalidate_all fail-open: %s", e)
    return deleted


def stats() -> dict:
    hits = METRICS["hits_exact"] + METRICS["hits_semantic"]
    lookups = hits + METRICS["misses"]
    return {
        **dict(METRICS),
        "hit_rate": round(hits / lookups, 4) if lookups else 0.0,
        # echo config (flat — test_llm_cache.py assert các key này)
        "semantic_cache_enabled": _SEMANTIC_ENABLED,
        "retrieval_cache_enabled": _RETRIEVAL_ENABLED,
        "semantic_cache_threshold": THRESHOLD,
        "semantic_cache_ttl_seconds": _SEMANTIC_TTL,
        "retrieval_cache_ttl_seconds": _RETRIEVAL_TTL,
        "cache_namespace": _NS,
        "cache_env": _ENV,
        "prompt_version": PROMPT_VERSION,
        "redis_configured": bool((os.getenv("REDIS_URL") or "").strip()),
    }
