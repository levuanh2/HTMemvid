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
import unicodedata
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

# Root logger của app không config level/handler (repo log vận hành bằng print) →
# event cache sẽ vô hình trong Docker. Gắn handler riêng CHỈ cho logger này.
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(getattr(logging, (os.getenv("CACHE_LOG_LEVEL", "INFO") or "INFO").upper(), logging.INFO))
    logger.propagate = False

# Bump khi đổi system prompt của qa_chain._qa_messages (hoặc logic sinh answer)
# để tự vô hiệu mọi answer đã cache — cùng nguyên tắc PIPELINE_VERSION của mindmap.
PROMPT_VERSION = "qa-v1"

_NS = (os.getenv("CACHE_NAMESPACE", "memvid") or "memvid").strip()
_ENV = (os.getenv("CACHE_ENV", "dev") or "dev").strip()

_SEMANTIC_ENABLED = (os.getenv("SEMANTIC_CACHE_ENABLED", "1") or "").strip().lower() not in ("0", "false", "no", "off")
_RETRIEVAL_ENABLED = (os.getenv("RETRIEVAL_CACHE_ENABLED", "1") or "").strip().lower() not in ("0", "false", "no", "off")
_SEMANTIC_TTL = int(os.getenv("SEMANTIC_CACHE_TTL_SECONDS", "172800"))   # 48h — answer từ tài liệu tĩnh
_RETRIEVAL_TTL = int(os.getenv("RETRIEVAL_CACHE_TTL_SECONDS", "3600"))   # 1h
_BUCKET_SCAN_CAP = 200            # trần số entry so cosine mỗi lookup (bucket vốn nhỏ)
_NEAR_THRESHOLD_MARGIN = 0.03     # log các cú suýt-hit để tune threshold
_LLM_AVG_MS = int(os.getenv("SEMANTIC_CACHE_LLM_AVG_MS", "20000"))  # ước lượng latency tiết kiệm/hit
_COST_PER_CALL_USD = float(os.getenv("SEMANTIC_CACHE_COST_PER_CALL_USD", "0") or "0")

# Borderline judge: sim trong [_JUDGE_LOW, _JUDGE_HIGH) → hỏi LLM "cùng intent + reuse an toàn?"
# (strict JSON). Cho phép hit DƯỚI threshold config (tới 0.80) nhưng có người gác — calibration
# bge-m3 (docs/SEMANTIC_CACHE_SPEC.md) cho thấy vùng 0.80-0.88 chồng lấn positive/negative.
_JUDGE_ENABLED = (os.getenv("SEMANTIC_CACHE_JUDGE_ENABLED", "1") or "").strip().lower() not in ("0", "false", "no", "off")
_JUDGE_TIMEOUT_SEC = float(os.getenv("SEMANTIC_CACHE_JUDGE_TIMEOUT_SEC", "15"))
_JUDGE_LOW = 0.80    # = threshold floor; KHÔNG hạ (poisoning)
_JUDGE_HIGH = 0.88   # trên mức này khỏi cần judge

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
    # tiếng Việt + biến thể không dấu (classify chạy trên cả 2 form) + EN.
    r"tài khoản|tai khoan|mật khẩu|mat khau|số dư|so du|của tôi|của em|của mình|"
    r"đăng nhập|dang nhap|phiên làm việc|\botp\b|mã xác thực|ma xac thuc|"
    r"thanh toán|thanh toan|hóa đơn|hoa don|chuyển khoản|chuyen khoan|"
    r"thẻ tín dụng|the tin dung|credit card|\binvoice\b|\bpayment\b|\blogin\b|\bsession\b|"
    r"my account|password|balance|my grade|api[ _-]?key|secret|token|credential",
    re.IGNORECASE,
)
_REALTIME_RE = re.compile(
    r"hôm nay|bây giờ|hiện tại|lúc này|mới nhất|"
    r"\btoday\b|\bnow\b|\bcurrent\b|\blatest\b|weather|thời tiết|\bgiá\b|\bprice\b",
    re.IGNORECASE,
)
# Câu RA LỆNH hành động (xóa/hủy/sửa) không phải câu hỏi tri thức — answer không tái sử dụng
# được và cache nhầm lệnh là nguy hiểm. Conservative: dính từ là deny (chỉ mất cache).
_ACTION_RE = re.compile(
    r"\b(xóa|xoá|xoa|hủy|huỷ|huy)\b|\bdelete\b|\bremove\b|\bdrop\b|ghi đè|ghi de|overwrite",
    re.IGNORECASE,
)


def classify_risk(question: str) -> tuple:
    """(cacheable, risk_class). Deny = không ghi cache (đọc cũng không bao giờ hit vì chưa từng ghi).
    Check cả form bỏ dấu — user gõ 'so du tai khoan' vẫn phải bị chặn."""
    q = question or ""
    probe = f"{q}\n{strip_diacritics(q)}"
    if _PERSONAL_RE.search(probe):
        return (False, "personal")
    if _REALTIME_RE.search(probe):
        return (False, "realtime")
    if _ACTION_RE.search(probe):
        return (False, "action")
    return (True, "low")


# --- Standalone vs follow-up ------------------------------------------------
# Câu standalone (tự đứng, không phụ thuộc ngữ cảnh hội thoại) vẫn được cache
# dù đang multi-turn — generate sẽ BỎ history khỏi prompt cho các câu này để
# answer context-free (lookup/store nhất quán). Heuristic CONSERVATIVE:
# nghi ngờ → follow-up (chỉ mất cache); nhận nhầm follow-up thành standalone
# mới nguy hiểm (answer thiếu ngữ cảnh + poisoning cache).
# ponytail: regex heuristic, nâng lên LLM-classify nếu đo thấy hit-rate quá thấp.

_FOLLOWUP_START_RE = re.compile(
    r"^\s*(còn|thế|vậy|tiếp|rồi|nữa|và|what about|how about|and|so|then|also)\b",
    re.IGNORECASE,
)
_FOLLOWUP_BODY_RE = re.compile(
    r"\b(nó|này|đó|kia|lúc nãy|ban nãy|khi nãy|vừa rồi|vừa nói|đã nói|"
    r"it|its|this|that|these|those|above|previous|previously|earlier|again|aforementioned)\b"
    r"|ở trên|như trên|bên trên|nói trên|phía trên|as mentioned",
    re.IGNORECASE,
)


def is_standalone_question(question: str) -> bool:
    """True nếu câu hỏi tự đứng được (cache an toàn trong multi-turn)."""
    q = (question or "").strip()
    if len(q.split()) < 4:  # câu cụt ("tại sao?", "nói rõ hơn") = follow-up
        return False
    if _FOLLOWUP_START_RE.search(q) or _FOLLOWUP_BODY_RE.search(q):
        return False
    return True


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


# --- Vietnamese-aware normalization ------------------------------------------

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[?!.,;:…\"'“”‘’()\[\]{}<>~`*_|\\/]+")


def normalize_question(q: str) -> str:
    """Chuẩn hoá cho exact-match tier: NFC unicode, lowercase, bỏ punctuation phổ biến,
    gộp khoảng trắng. GIỮ NGUYÊN dấu tiếng Việt — dấu mang nghĩa ('bán'≠'bàn').
    Câu gốc luôn được lưu nguyên trong entry (field q)."""
    s = unicodedata.normalize("NFC", (q or "")).lower()
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def strip_diacritics(s: str) -> str:
    """Bỏ dấu (NFD → drop combining marks) + đ→d. CHỈ dùng làm khoá PHỤ (alias) —
    hit qua alias bắt buộc verify cosine ≥ threshold vì đồng tự khác nghĩa."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.replace("đ", "d").replace("Đ", "D")


# --- key helpers ------------------------------------------------------------

def _norm_q(q: str) -> str:
    return normalize_question(q)


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


def _alias_key(bucket: str, eid_nd: str) -> str:
    """Khoá phụ theo form bỏ dấu → trỏ về eid chính (value = eid)."""
    return f"{_NS}:{_ENV}:sc:{bucket}:a:{eid_nd}"


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


# --- Borderline judge ---------------------------------------------------------

_JUDGE_PROMPT = """You are a cache safety judge. Decide whether a cached answer can be reused for a new user question.

Cached question: {cached_q}
New question: {new_q}

Return only valid JSON:
{{"same_intent": true/false, "safe_to_reuse": true/false, "risk_class": "low|medium|high", "reason": "short reason under 30 words"}}

Rules:
- Reuse only if both questions ask for the same information with the same intent.
- Do not reuse for private, account-specific, financial, authentication, destructive, or real-time data.
- If meaning is ambiguous, safe_to_reuse must be false.
"""


def judge_reuse(new_q: str, cached_q: str) -> bool:
    """LLM gác cổng cho hit vùng borderline. Mọi lỗi/timeout/JSON hỏng → False (miss, fail-safe).
    Monkeypatch điểm này trong test."""
    if os.getenv("SKIP_MODEL_LOAD") == "1":
        return False
    METRICS["judge_calls"] += 1
    try:
        from concurrent.futures import ThreadPoolExecutor

        from app.clients.llm_factory import get_llm

        def _ask() -> str:
            resp = get_llm("chat").invoke(_JUDGE_PROMPT.format(cached_q=cached_q[:300], new_q=new_q[:300]))
            return str(getattr(resp, "content", None) or resp)

        with ThreadPoolExecutor(max_workers=1) as ex:
            raw = ex.submit(_ask).result(timeout=_JUDGE_TIMEOUT_SEC)
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1])
        ok = bool(data.get("same_intent")) and bool(data.get("safe_to_reuse"))
        if not ok:
            METRICS["judge_denied"] += 1
            logger.info("[cache] event=cache_borderline_judge verdict=deny reason=%s",
                        str(data.get("reason", ""))[:80])
        return ok
    except Exception as e:
        METRICS["judge_errors"] += 1
        logger.info("[cache] event=cache_borderline_judge verdict=deny reason=judge_error err=%s", e)
        return False


# --- Tier 2: semantic response cache ----------------------------------------

def _answer_ok(entry: dict) -> bool:
    """Entry chỉ được phép serve khi answer non-empty — entry rỗng coi như không tồn tại."""
    p = entry.get("payload")
    ok = isinstance(p, dict) and bool(str(p.get("answer") or "").strip())
    if not ok:
        METRICS["empty_cached_answer"] += 1
        logger.info("[cache] event=cache_miss_empty_cached_answer cached_q=%r", str(entry.get("q", ""))[:80])
    return ok


def _hit(entry: dict, kind: str, bucket: str, sim: Optional[float] = None) -> dict:
    METRICS[f"hits_{kind}"] += 1
    METRICS["saved_llm_calls"] += 1
    METRICS["latency_saved_ms"] += _LLM_AVG_MS
    logger.info("[cache] event=cache_hit kind=%s sim=%s bucket=%s cached_q=%r",
                kind, f"{sim:.4f}" if sim is not None else "exact", bucket,
                str(entry.get("q", ""))[:80])
    return {"payload": entry.get("payload"), "status": int(entry.get("status", 200))}


def semantic_lookup(cache_key: str) -> Optional[dict]:
    """Trả {'payload':..., 'status':...} nếu hit, None nếu miss/bypass/lỗi.
    Thứ tự: exact (O(1)) → alias không-dấu (verify cosine) → semantic scan (direct ≥0.88,
    borderline [0.80, 0.88) qua judge nếu bật, else threshold thường)."""
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
            if _answer_ok(entry):
                return _hit(entry, "exact", bucket)

        judge_active = _JUDGE_ENABLED and THRESHOLD >= _JUDGE_LOW

        # 2) alias không-dấu: 'noi dung la gi' ↔ 'nội dung là gì'. KHÔNG verify bằng cosine —
        # đo thật (2026-07-06): cặp có-dấu/không-dấu CÙNG nghĩa sim chỉ 0.558 (bge-m3 embed
        # 2 form rất khác), còn cặp homograph KHÁC nghĩa ('bán'/'bàn', lệch 1 ký tự) sim ~cao
        # → cosine gác NGƯỢC chiều. Gác đúng = judge (so intent 2 câu chữ). Judge tắt →
        # trả thẳng: toàn bộ câu normalized trùng modulo dấu là tín hiệu rất mạnh.
        q_nd = strip_diacritics(q_norm)
        # Query không dấu (q_nd == q_norm): entry chính đã miss ở bước 1 → chỉ còn alias
        # (trỏ tới entry CÓ dấu). Query có dấu: thử alias, rồi entry chính keyed theo form
        # không dấu (trường hợp entry gốc KHÔNG dấu — phía store không ghi alias).
        target_eid = r.get(_alias_key(bucket, _eid(q_nd)))
        if not target_eid and q_nd != q_norm:
            target_eid = _eid(q_nd)
        if target_eid:
            raw = r.get(_entry_key(bucket, str(target_eid)))
            if raw:
                entry = json.loads(raw)
                if _answer_ok(entry):
                    if not judge_active or judge_reuse(q, str(entry.get("q", ""))):
                        return _hit(entry, "exact_nodia", bucket)
                    METRICS["misses"] += 1
                    logger.info("[cache] event=cache_miss reason=nodia_judge_denied q=%r", q[:80])
                    return None

        # 3) semantic scan: embed rồi so cosine trong bucket
        vec = encode_query_cached(q)
        if vec is None:  # SKIP_MODEL_LOAD / CI / lỗi model
            METRICS["bypass_no_embedding"] += 1
            logger.info("[cache] event=cache_bypass reason=no_embedding")
            return None
        qv = np.asarray(vec, dtype=np.float32).reshape(-1)

        ids = list(r.smembers(_ids_key(bucket)))[:_BUCKET_SCAN_CAP]
        if not ids:
            METRICS["misses"] += 1
            logger.info("[cache] event=cache_miss reason=empty_bucket bucket=%s", bucket)
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
                if not _answer_ok(entry):  # entry rỗng không được thắng scan
                    continue
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
                METRICS["expired_cleaned"] += len(dead)
                logger.info("[cache] event=cache_expired cleaned=%d bucket=%s", len(dead), bucket)
            except Exception:
                pass

        # Judge chỉ gác khi threshold ở vùng bình thường (≥ floor). Admin override
        # floor (<0.80) = tự chịu trách nhiệm → giữ nguyên rule threshold thuần.
        if best_entry is not None:
            if judge_active:
                if best_sim >= max(THRESHOLD, _JUDGE_HIGH):
                    return _hit(best_entry, "semantic", bucket, best_sim)
                if best_sim >= _JUDGE_LOW:
                    if judge_reuse(q, str(best_entry.get("q", ""))):
                        return _hit(best_entry, "semantic_judged", bucket, best_sim)
                    METRICS["misses"] += 1
                    logger.info("[cache] event=cache_miss reason=judge_denied sim=%.4f q=%r",
                                best_sim, q[:80])
                    return None
            elif best_sim >= THRESHOLD:
                return _hit(best_entry, "semantic", bucket, best_sim)

        if best_entry is not None and best_sim >= THRESHOLD - _NEAR_THRESHOLD_MARGIN:
            logger.info("[cache] semantic near-threshold sim=%.4f (threshold=%.2f) q=%r",
                        best_sim, THRESHOLD, q[:80])
        METRICS["misses"] += 1
        logger.info("[cache] event=cache_miss reason=below_threshold best_sim=%.4f q=%r",
                    best_sim, q[:80])
        return None
    except Exception as e:
        redis_client.mark_unavailable()
        METRICS["errors"] += 1
        logger.info("[cache] event=redis_error op=semantic_lookup fail-open err=%s", e)
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

    # INVARIANT: answer rỗng/whitespace không bao giờ được ghi.
    _p = value.get("payload")
    if not (isinstance(_p, dict) and str(_p.get("answer") or "").strip()):
        METRICS["write_skipped_empty"] += 1
        logger.info("[cache] event=cache_write_skipped_empty_answer q=%r", q[:80])
        return

    cacheable, risk_class = classify_risk(q)
    if not cacheable:
        METRICS["bypass_risk"] += 1
        logger.info("[cache] event=cache_unsafe reason=risk_%s q=%r", risk_class, q[:80])
        return

    vec = encode_query_cached(q)
    if vec is None:
        METRICS["bypass_no_embedding"] += 1
        logger.info("[cache] event=cache_bypass reason=no_embedding_on_store")
        return
    qv = np.asarray(vec, dtype=np.float32).reshape(-1)

    bucket = _bucket_id(d.get("sources") or [], d.get("language"), d.get("category"), bool(d.get("use_memory_tree")))
    q_nd = strip_diacritics(q_norm)
    now = time.time()
    entry = {
        "q": q,                                  # original_question
        "q_norm": q_norm,                        # normalized_question
        "q_norm_no_diacritics": q_nd,            # form bỏ dấu (alias key)
        "payload": value.get("payload"),
        "status": int(value.get("status", 200)),
        "created_at": now,
        "expires_at": now + _SEMANTIC_TTL,
        "ttl_seconds": _SEMANTIC_TTL,
        "model": os.getenv("SLM_MODEL_CHAT") or os.getenv("SLM_MODEL") or "",
        "embedding_model": os.getenv("EMBEDDING_MODEL_NAME") or "",
        "prompt_version": PROMPT_VERSION,
        "index_version": index_version(),
        "context_hash": bucket,                  # bucket = hash MỌI điều kiện match
        "risk_class": risk_class,
        "cache_policy": f"low-risk-static-{_SEMANTIC_TTL}s",
        "vec_b64": _vec_to_b64(qv),
        "dim": int(qv.size),
    }
    try:
        eid = _eid(q_norm)
        r.setex(_entry_key(bucket, eid), _SEMANTIC_TTL, json.dumps(entry, ensure_ascii=False))
        r.sadd(_ids_key(bucket), eid)
        r.expire(_ids_key(bucket), _SEMANTIC_TTL)
        if q_nd != q_norm:  # alias chỉ khi form bỏ dấu khác form chuẩn
            r.setex(_alias_key(bucket, _eid(q_nd)), _SEMANTIC_TTL, eid)
        METRICS["writes"] += 1
        logger.info("[cache] event=cache_write_success bucket=%s risk=%s ttl=%ds", bucket, risk_class, _SEMANTIC_TTL)
    except Exception as e:
        redis_client.mark_unavailable()
        METRICS["errors"] += 1
        METRICS["write_failed"] += 1
        logger.info("[cache] event=redis_error op=semantic_store fail-open err=%s", e)


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
    hits = (METRICS["hits_exact"] + METRICS["hits_exact_nodia"]
            + METRICS["hits_semantic"] + METRICS["hits_semantic_judged"])
    bypasses = METRICS["bypass_risk"] + METRICS["bypass_history"] + METRICS["bypass_no_embedding"]
    lookups = hits + METRICS["misses"]
    return {
        **dict(METRICS),
        "hit_rate": round(hits / lookups, 4) if lookups else 0.0,
        "miss_rate": round(METRICS["misses"] / lookups, 4) if lookups else 0.0,
        "bypass_rate": round(bypasses / (lookups + bypasses), 4) if (lookups + bypasses) else 0.0,
        "estimated_cost_saved_usd": round(METRICS["saved_llm_calls"] * _COST_PER_CALL_USD, 4),
        "semantic_cache_judge_enabled": _JUDGE_ENABLED,
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
