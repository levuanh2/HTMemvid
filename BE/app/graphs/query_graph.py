from __future__ import annotations

import json
import logging
import math
import os
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from langgraph.graph import END, StateGraph

from app.graphs.logger import _Timer, log_node_event
from app.graphs.sqlite_checkpointer import sqlite_saver_from_path
from app.graphs.state import QueryState
from app.domains.cache import llm_cache
from app.domains.retrieval import grading, nli, query_rewrite, rerank
from app.domains.retrieval.ensemble_retriever import hybrid_retrieve_with_ensemble
from app.domains.retrieval.hybrid import HybridRetriever
from shared.config import get_settings
_log = logging.getLogger(__name__)

_INCLUDE_CHUNK_SOURCE_TAGS = (os.getenv("INCLUDE_CHUNK_SOURCE_TAGS", "0") or "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def build_query_graph(
    *,
    data_dir: Path,
    index_meta_path: Path,
    jobs_update: Callable[..., None] | None,
    # cache helpers
    make_cache_key: Callable[..., str],
    get_cached: Callable[[str], Optional[dict]],
    set_cached: Callable[[str, dict], None],
    # source status helpers
    check_sources_status: Callable[[list], dict],
    get_source_status_by_stem: Callable[[str], Optional[dict]],
    # retrieval + generation
    search_index: Callable[[str], list[str]],
    summarize_results: Callable[..., str],
    query_with_memory_tree: Callable[..., Any],
    get_session_history: Callable[[str, int], List[dict]] | None = None,
    retriever: Any | None = None,
) -> Any:
    """
    LangGraph Query pipeline:
    CacheLookup -> RetrieveMemory(optional) -> RetrieveFAISS -> ContextBuilder -> GenerateAnswer
    -> Evaluate(optional) -> FeedbackLoop(max_retry=2) -> Finalize

    Giữ nguyên API contract bằng cách trả về (payload, status_code) qua state["payload"]/state["status_code"].
    """

    AI_TIMEOUT = int(os.getenv("AI_TIMEOUT_SEC", "180"))
    EVAL_ENABLED = (os.getenv("EVAL_ENABLED", "false") or "").strip().lower() == "true"
    EVAL_THRESHOLD = float(os.getenv("EVAL_THRESHOLD", "0.6"))
    HYBRID_TOP_K = int(os.getenv("HYBRID_TOP_K", "4"))
    USE_LC_ENSEMBLE = (os.getenv("USE_LC_ENSEMBLE", "1") or "").strip().lower() not in ("0", "false", "no", "off")
    USE_LC_QA_CHAIN = (os.getenv("USE_LC_QA_CHAIN", "1") or "").strip().lower() in ("1", "true", "yes", "on")
    QUERY_STREAM_TOKENS = (os.getenv("QUERY_STREAM_TOKENS", "1") or "").strip().lower() in ("1", "true", "yes", "on")
    # Timeout ri\u00eang cho Memory Tree \u2014 fallback FAISS n\u1ebfu v\u01b0\u1ee3t
    MEMORY_TREE_TIMEOUT = int(os.getenv("MEMORY_TREE_TIMEOUT_SEC", "15"))

    # C\u1edd t\u00ednh n\u0103ng m\u1edbi (CRAG / Supervisor / HITL) \u0111\u1ecdc qua get_settings() \u2014 \u0111\u1ecdc 1 l\u1ea7n
    # t\u1ea1i build time, c\u00f9ng v\u00f2ng \u0111\u1eddi v\u1edbi c\u00e1c os.getenv \u1edf tr\u00ean. M\u1eb7c \u0111\u1ecbnh t\u1eaft \u2192 graph y h\u1ec7t c\u0169.
    _s = get_settings()
    CRAG_ENABLED = _s.crag_enabled
    CRAG_RELEVANCE_THRESHOLD = _s.crag_relevance_threshold
    CRAG_WRONG_FLOOR = _s.crag_wrong_floor
    CRAG_REWRITE_MAX = _s.crag_rewrite_max
    SUPERVISOR_ENABLED = _s.supervisor_enabled
    HITL_ENABLED = _s.hitl_enabled
    # Rerank (Two-Stage Retrieval). Mặc định tắt → topology graph y hệt cũ.
    RERANK_ENABLED = _s.rerank_enabled
    RERANK_CANDIDATE_K = max(_s.rerank_candidate_k, HYBRID_TOP_K)
    RERANK_TOP_N = _s.rerank_top_n if _s.rerank_top_n > 0 else HYBRID_TOP_K
    RERANK_TIMEOUT = _s.rerank_timeout_sec
    # Khi bật rerank: Stage 1 (RetrieveFAISS) lấy candidate pool rộng, Stage 2
    # (RerankDocuments) lọc xuống RERANK_TOP_N.
    RETRIEVE_TOP_K = RERANK_CANDIDATE_K if RERANK_ENABLED else HYBRID_TOP_K
    # NLI (contradiction-check). Mặc định tắt → topology graph y hệt cũ.
    NLI_ENABLED = _s.nli_enabled
    NLI_CONTRADICTION_THRESHOLD = _s.nli_contradiction_threshold
    NLI_TIMEOUT = _s.nli_timeout_sec
    NLI_MAX_PAIRS = _s.nli_max_pairs
    # HITL hi\u1ec3n th\u1ecb c\u00e2u tr\u1ea3 l\u1eddi sau khi duy\u1ec7t \u2192 t\u1eaft stream token \u0111\u1ec3 kh\u00f4ng l\u1ed9 b\u1ea3n nh\u00e1p ch\u01b0a duy\u1ec7t.
    if HITL_ENABLED:
        QUERY_STREAM_TOKENS = False

    # Retriever được INJECT (seam shared.interfaces.Retriever). Mặc định dựng
    # HybridRetriever như cũ -> main.py không phải đổi (back-compat Phase 1).
    if retriever is None:
        retriever = HybridRetriever(
            index_path=Path(index_meta_path).with_name("index.faiss"),
            meta_path=Path(index_meta_path),
        )

    def _set_job(job_id: str, **kw: Any) -> None:
        if jobs_update is None:
            return
        try:
            jobs_update(job_id, **kw)
        except Exception:
            pass

    def check_sources_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], status="running", progress=1, current_node="CheckSources")
            selected_sources = state.get("selected_sources") or []
            processing_message = None

            if selected_sources:
                sources_status = check_sources_status(selected_sources)
                error_sources = [s for s, st in sources_status.items() if st == "error"]
                if error_sources:
                    error_info = get_source_status_by_stem(error_sources[0])
                    error_msg = error_info.get("error", "Source processing error") if error_info else "Source processing error"
                    payload = {"error": f"Một hoặc nhiều tài liệu đã gặp lỗi: {error_msg}", "answer": None}
                    log_node_event(state["job_id"], "CheckSources", "ok", t.ms(), {"status": "error_sources"})
                    return {**state, "payload": payload, "status_code": 400, "done": True, "progress": 2, "current_node": "CheckSources"}

                processing_sources = [s for s, st in sources_status.items() if st == "processing"]
                if processing_sources:
                    processing_message = "Một số tài liệu đang được xử lý, mình sẽ trả lời đầy đủ hơn khi xong."

            log_node_event(state["job_id"], "CheckSources", "ok", t.ms())
            return {**state, "processing_message": processing_message, "progress": 2, "current_node": "CheckSources", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "CheckSources", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "CheckSources"}

    def cache_lookup_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=5, current_node="CacheLookup")
            if state.get("processing_message"):
                return {**state, "cache_key": None, "progress": 5, "current_node": "CacheLookup", "error": None}
            # Auth Hardening Phase E: no blanket bypass under the flag anymore — the cache is
            # keyed by cache_scope (user id under enforcement) so cross-user reuse is
            # impossible while a user still benefits from their own cache.
            # Multi-turn: chỉ bypass câu FOLLOW-UP (phụ thuộc history). Câu standalone
            # vẫn cache — generate_answer_node sẽ bỏ history khỏi prompt cho các câu
            # có cache_key để answer context-free (lookup/store nhất quán).
            if state.get("conversation_history"):
                if not llm_cache.is_standalone_question(state["q"]):
                    llm_cache.METRICS["bypass_history"] += 1
                    return {**state, "cache_key": None, "progress": 5, "current_node": "CacheLookup", "error": None}
                llm_cache.METRICS["standalone_with_history"] += 1

            cache_key = make_cache_key(
                state["q"],
                state.get("selected_sources") or [],
                bool(state.get("use_memory_tree")),
                {"category": state.get("category"), "language": state.get("language")},
                state.get("cache_scope") or "public",  # Phase E: user-scoped bucket
            )
            cached = get_cached(cache_key)
            payload = cached.get("payload") if isinstance(cached, dict) else None
            if isinstance(payload, dict) and str(payload.get("answer") or "").strip():
                status = int(cached.get("status", 200))
                log_node_event(state["job_id"], "CacheLookup", "ok", t.ms(), {"hit": True})
                return {**state, "payload": payload, "status_code": status, "cache_key": cache_key, "done": True, "progress": 5, "current_node": "CacheLookup"}
            if payload is not None:
                # Hit nhưng answer rỗng (entry độc/di sản) → coi là MISS, đi tiếp pipeline.
                llm_cache.METRICS["empty_cached_answer"] += 1
                llm_cache.logger.info("[cache] event=cache_miss_empty_cached_answer q=%r", str(state["q"])[:80])

            log_node_event(state["job_id"], "CacheLookup", "ok", t.ms(), {"hit": False})
            return {**state, "cache_key": cache_key, "progress": 5, "current_node": "CacheLookup", "error": None}
        except Exception as e:
            # INVARIANT: cache là tối ưu — lỗi lookup KHÔNG được chặn đường trả lời.
            # Không set state["error"] (router sẽ đẩy vào ErrorHandler); đi tiếp như miss.
            log_node_event(state["job_id"], "CacheLookup", "error", t.ms(), {"error": str(e)})
            llm_cache.METRICS["errors"] += 1
            llm_cache.logger.info("[cache] event=cache_error_fallback_to_llm err=%s", e)
            return {**state, "cache_key": None, "progress": 5, "current_node": "CacheLookup", "error": None}

    def retrieve_memory_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=15, current_node="RetrieveMemory")
            if not state.get("use_memory_tree", True):
                return {**state, "progress": 15, "current_node": "RetrieveMemory", "error": None}
            # Có filter metadata (category/language) -> bỏ qua memory tree, ép truy hồi chunk
            # ở RetrieveFAISS (nơi áp được filter); memory tree chỉ lọc theo nguồn.
            if state.get("category") or state.get("language"):
                return {**state, "progress": 15, "current_node": "RetrieveMemory", "error": None}

            # Ch\u1ea1y memory tree v\u1edbi timeout ri\u00eang \u2014 n\u1ebfu v\u01b0\u1ee3t th\u00ec fallback FAISS
            def _query_mem():
                return query_with_memory_tree(state["q"], selected_sources=state.get("selected_sources") or [])

            mem_result = None
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    fut = ex.submit(_query_mem)
                    mem_result = fut.result(timeout=MEMORY_TREE_TIMEOUT)
            except TimeoutError:
                log_node_event(state["job_id"], "RetrieveMemory", "timeout", t.ms(), {"timeout_sec": MEMORY_TREE_TIMEOUT})
                return {**state, "progress": 15, "current_node": "RetrieveMemory", "error": None}

            if mem_result and isinstance(mem_result, dict) and (mem_result.get("answer") or "").strip():
                if state.get("processing_message"):
                    mem_result["processing_message"] = state["processing_message"]
                log_node_event(state["job_id"], "RetrieveMemory", "ok", t.ms(), {"hit": True})
                return {**state, "payload": mem_result, "status_code": 200, "done": True, "progress": 15, "current_node": "RetrieveMemory"}

            log_node_event(state["job_id"], "RetrieveMemory", "ok", t.ms(), {"hit": False})
            return {**state, "progress": 15, "current_node": "RetrieveMemory", "error": None}
        except Exception as e:
            # memory tree l\u1ed7i th\u00ec fallback FAISS, kh\u00f4ng fail to\u00e0n pipeline
            log_node_event(state["job_id"], "RetrieveMemory", "error", t.ms(), {"error": str(e)})
            return {**state, "progress": 15, "current_node": "RetrieveMemory", "error": None}

    def retrieve_faiss_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=20, current_node="RetrieveFAISS")
            selected_sources = state.get("selected_sources") or []
            f_category = state.get("category") or None
            f_language = state.get("language") or None
            # Conversation Context Layer: retrieve with the rewritten standalone question
            # when present (resolves "nó"/"phần đó" for better recall); the original q is
            # kept in state for answer generation. Retrieval cache keys on this same query.
            retrieve_q = (state.get("standalone_question") or "").strip() or state["q"]
            cache_scope = state.get("cache_scope") or "public"  # Phase E: user-scoped retrieval cache

            def _do_hybrid_retrieve():
                # Tier 3: retrieval cache (Redis, fail-open) — key theo retrieve_q tại
                # thời điểm gọi nên đúng cả khi CRAG/rewrite đổi câu hỏi. Xem docs/SEMANTIC_CACHE_SPEC.md.
                cached = llm_cache.retrieval_get(
                    retrieve_q, selected_sources, RETRIEVE_TOP_K, f_category, f_language, cache_scope
                )
                if cached is not None:
                    return cached
                if USE_LC_ENSEMBLE:
                    out = hybrid_retrieve_with_ensemble(
                        retriever,
                        retrieve_q,
                        selected_sources=selected_sources,
                        top_k=RETRIEVE_TOP_K,
                        category=f_category,
                        language=f_language,
                    )
                else:
                    out = retriever.retrieve(
                        retrieve_q,
                        selected_sources=selected_sources,
                        top_k=RETRIEVE_TOP_K,
                        category=f_category,
                        language=f_language,
                    )
                llm_cache.retrieval_put(
                    retrieve_q, selected_sources, RETRIEVE_TOP_K, f_category, f_language, out, cache_scope
                )
                return out

            hist_patch: dict = {}
            retrieved = None
            sid = (state.get("session_id") or "").strip()
            if get_session_history and sid:
                try:
                    with ThreadPoolExecutor(max_workers=2) as ex:
                        hf = ex.submit(get_session_history, sid, 8)
                        rf = ex.submit(_do_hybrid_retrieve)
                        try:
                            fresh = hf.result(timeout=5)
                            if isinstance(fresh, list):
                                hist_patch["conversation_history"] = fresh
                        except Exception:
                            pass
                        retrieved = rf.result(timeout=60)
                except Exception:
                    retrieved = _do_hybrid_retrieve()
            else:
                retrieved = _do_hybrid_retrieve()

            if not retrieved:
                payload = {"answer": "Không tìm thấy dữ liệu phù hợp trong file đã chọn."}
                log_node_event(state["job_id"], "RetrieveFAISS", "ok", t.ms(), {"chunks": 0})
                return {**state, **hist_patch, "payload": payload, "status_code": 200, "done": True, "progress": 20, "current_node": "RetrieveFAISS"}

            chunks_with_citation: list[str] = []
            chunk_stems: list[str] = []
            sources_seen: list[str] = []
            for item in retrieved:
                txt = item.text or ""
                if _INCLUDE_CHUNK_SOURCE_TAGS:
                    chunks_with_citation.append(f"[Nguồn: {item.video_stem}, đoạn {item.chunk_id}]\n{txt}")
                else:
                    chunks_with_citation.append(txt)
                stem = (item.video_stem or "").strip()
                chunk_stems.append(stem)
                if stem and stem not in sources_seen:
                    sources_seen.append(stem)

            log_node_event(state["job_id"], "RetrieveFAISS", "ok", t.ms(), {"chunks": len(chunks_with_citation)})
            return {
                **state,
                **hist_patch,
                "retrieved_chunks": chunks_with_citation,
                "retrieved_stems": chunk_stems,
                "retrieved_sources": sources_seen,
                "progress": 20,
                "current_node": "RetrieveFAISS",
                "error": None,
            }
        except ValueError as ve:
            err_str = str(ve)
            _log.error("[RetrieveFAISS] ValueError: %s", err_str)
            log_node_event(state["job_id"], "RetrieveFAISS", "error", t.ms(), {"error": err_str})
            if "dim mismatch" in err_str.lower() or "embedding" in err_str.lower():
                return {
                    **state,
                    "error": "Chỉ mục tài liệu đang không tương thích với embedding model hiện tại. Vui lòng rebuild index hoặc upload lại tài liệu.",
                    "current_node": "RetrieveFAISS",
                }
            return {**state, "error": err_str, "current_node": "RetrieveFAISS"}
        except Exception as e:
            err_str = str(e)
            _log.error("[RetrieveFAISS] Exception: %s", err_str)
            log_node_event(state["job_id"], "RetrieveFAISS", "error", t.ms(), {"error": err_str})
            if "shape" in err_str.lower() or "dimension" in err_str.lower():
                return {
                    **state,
                    "error": "Chỉ mục tài liệu đang không tương thích với embedding model hiện tại. Vui lòng rebuild index hoặc upload lại tài liệu.",
                    "current_node": "RetrieveFAISS",
                }
            return {**state, "error": err_str, "current_node": "RetrieveFAISS"}

    def rerank_documents_node(state: dict) -> dict:
        """Two-Stage Retrieval — Stage 2: cross-encoder lọc candidate pool xuống top_n."""
        t = _Timer()
        chunks = state.get("retrieved_chunks") or []
        stems = state.get("retrieved_stems") or []
        try:
            _set_job(state["job_id"], progress=35, current_node="RerankDocuments")
            if not chunks:
                return {**state, "progress": 35, "current_node": "RerankDocuments", "error": None}

            # Nạp model NGOÀI vùng timeout: nếu để lazy-load chạy trong block
            # result(timeout=RERANK_TIMEOUT), lần đầu (cache nguội) tải model >
            # timeout → rerank âm thầm fallback identity ở query đầu tiên.
            rerank.warmup()

            def _do_rerank():
                return rerank.rerank_texts(state["q"], chunks, top_n=RERANK_TOP_N)

            scored_ok = True
            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    ranked = ex.submit(_do_rerank).result(timeout=RERANK_TIMEOUT)
            except TimeoutError:
                # Quá hạn → giữ nguyên thứ tự, chỉ cắt top_n (không làm hỏng câu trả lời).
                ranked = [(i, 0.0) for i in range(min(RERANK_TOP_N, len(chunks)))]
                scored_ok = False  # điểm 0.0 giả → KHÔNG dùng cho CRAG grade

            idxs = [i for i, _ in ranked if 0 <= i < len(chunks)]
            kept = [chunks[i] for i in idxs]
            kept_stems = [stems[i] for i in idxs if i < len(stems)] if stems else []

            patch: dict = {
                "retrieved_chunks": kept,
                "progress": 35,
                "current_node": "RerankDocuments",
                "error": None,
            }
            # Điểm cross-encoder (logit) → sigmoid về (0,1) cho CRAG grade dùng lại
            # (sau rerank chunk là str nên grade mất vector/bm25 score). Bỏ qua khi timeout.
            if scored_ok:
                patch["rerank_scores"] = [
                    1.0 / (1.0 + math.exp(-float(s)))
                    for i, s in ranked if 0 <= i < len(chunks)
                ]
            # Sau khi lọc, nguồn có thể thu hẹp → cập nhật lại danh sách hiển thị.
            if kept_stems:
                sources: list[str] = []
                for s in kept_stems:
                    s = (s or "").strip()
                    if s and s not in sources:
                        sources.append(s)
                patch["retrieved_sources"] = sources

            log_node_event(
                state["job_id"], "RerankDocuments", "ok", t.ms(),
                {"candidates": len(chunks), "kept": len(kept), "backend": _s.rerank_backend},
            )
            return {**state, **patch}
        except Exception as e:
            # Rerank lỗi KHÔNG fail pipeline — giữ ứng viên đầu, cắt top_n.
            log_node_event(state["job_id"], "RerankDocuments", "error", t.ms(), {"error": str(e)})
            return {
                **state,
                "retrieved_chunks": chunks[:RERANK_TOP_N],
                "progress": 35,
                "current_node": "RerankDocuments",
                "error": None,
            }

    def verify_context_node(state: dict) -> dict:
        """NLI contradiction-check: phát hiện cặp chunk mâu thuẫn (phủ định/thời gian/
        con số) rồi loại chunk hạng thấp, giữ chunk hạng cao. Lỗi/timeout → giữ nguyên."""
        t = _Timer()
        chunks = state.get("retrieved_chunks") or []
        stems = state.get("retrieved_stems") or []
        try:
            _set_job(state["job_id"], progress=42, current_node="VerifyContext")
            texts = [str(c) for c in chunks if c]
            if len(texts) < 2:
                return {**state, "progress": 42, "current_node": "VerifyContext", "error": None}

            # Nạp model NGOÀI vùng timeout: nếu để lazy-load chạy trong block
            # result(timeout=NLI_TIMEOUT), lần đầu (cache nguội) tải model > timeout
            # → detect_conflicts âm thầm trả [] (không khử mâu thuẫn) ở query đầu.
            nli.warmup()

            def _do_detect():
                return nli.detect_conflicts(
                    texts, max_pairs=NLI_MAX_PAIRS, threshold=NLI_CONTRADICTION_THRESHOLD
                )

            try:
                with ThreadPoolExecutor(max_workers=1) as ex:
                    conflicts = ex.submit(_do_detect).result(timeout=NLI_TIMEOUT)
            except TimeoutError:
                conflicts = []

            if not conflicts:
                log_node_event(state["job_id"], "VerifyContext", "ok", t.ms(), {"conflicts": 0})
                return {**state, "context_conflicts": [], "progress": 42, "current_node": "VerifyContext", "error": None}

            keep = nli.resolve_conflicts(len(texts), conflicts)
            kept = [texts[i] for i in keep]
            kept_stems = [stems[i] for i in keep if i < len(stems)] if stems else []

            patch: dict = {
                "retrieved_chunks": kept,
                "context_conflicts": conflicts,
                "progress": 42,
                "current_node": "VerifyContext",
                "error": None,
            }
            # Giữ rerank_scores khớp 1-1 với chunk còn lại (CRAG grade dựa vào độ dài khớp).
            prev_scores = state.get("rerank_scores")
            if isinstance(prev_scores, list) and len(prev_scores) == len(texts):
                patch["rerank_scores"] = [prev_scores[i] for i in keep]
            if kept_stems:
                sources: list[str] = []
                for s in kept_stems:
                    s = (s or "").strip()
                    if s and s not in sources:
                        sources.append(s)
                patch["retrieved_stems"] = kept_stems
                patch["retrieved_sources"] = sources

            log_node_event(
                state["job_id"], "VerifyContext", "ok", t.ms(),
                {"conflicts": len(conflicts), "dropped": len(texts) - len(kept)},
            )
            return {**state, **patch}
        except Exception as e:
            # NLI lỗi KHÔNG fail pipeline — giữ nguyên toàn bộ chunk.
            log_node_event(state["job_id"], "VerifyContext", "error", t.ms(), {"error": str(e)})
            return {**state, "progress": 42, "current_node": "VerifyContext", "error": None}

    def context_builder_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=50, current_node="ContextBuilder")
            chunks = state.get("retrieved_chunks") or []
            # Truncate nhẹ để tránh prompt quá dài (giữ nguyên nội dung chunk-level)
            max_chunks = int(os.getenv("CTX_MAX_CHUNKS", "18"))
            max_chars = int(os.getenv("MAX_CONTEXT_CHARS", os.getenv("CTX_MAX_CHARS", "5000")))
            parts: list[str] = []
            total = 0
            for c in chunks[:max_chunks]:
                c = (c or "").strip()
                if not c:
                    continue
                if total + len(c) > max_chars:
                    break
                parts.append(c)
                total += len(c)
            context = "\n\n---\n\n".join(parts)
            log_node_event(state["job_id"], "ContextBuilder", "ok", t.ms(), {"chunks": len(parts), "chars": total})
            return {**state, "context": context, "progress": 50, "current_node": "ContextBuilder", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "ContextBuilder", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "ContextBuilder"}

    def _call_llm_with_timeout(fn: Callable[[], str]) -> str:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(fn)
            return fut.result(timeout=AI_TIMEOUT)

    def generate_answer_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=75, current_node="GenerateAnswer")

            # Giữ behavior cũ: dùng summarize_results(q, chunks_with_file)
            q = state["q"]
            chunks = state.get("retrieved_chunks") or []
            hist = state.get("conversation_history") or []
            # cache_key được set = câu standalone (hoặc phiên chưa có history):
            # bỏ history khỏi prompt để answer context-free — điều kiện để Finalize
            # được phép ghi answer này vào semantic cache mà không poisoning.
            if hist and state.get("cache_key"):
                hist = []

            q_effective = q
            if not USE_LC_QA_CHAIN and isinstance(hist, list) and hist:
                lines: list[str] = []
                for m in hist[-8:]:
                    if not isinstance(m, dict):
                        continue
                    role = str(m.get("role") or "").strip().lower()
                    content = str(m.get("content") or "").strip()
                    if not role or not content:
                        continue
                    if role == "assistant":
                        lines.append(f"ASSISTANT: {content}")
                    else:
                        lines.append(f"USER: {content}")
                if lines:
                    q_effective = "Lịch sử trò chuyện gần đây:\n" + "\n".join(lines) + f"\n\nCâu hỏi hiện tại: {q}"

            def _gen() -> str:
                if USE_LC_QA_CHAIN:
                    from app.domains.summary.qa_chain import answer_with_document_context
                    ctx = (state.get("context") or "").strip()
                    if not ctx and chunks:
                        ctx = "\n\n---\n\n".join(str(c) for c in chunks if c)
                    return answer_with_document_context(
                        q,
                        ctx,
                        history=hist if isinstance(hist, list) else None,
                        feature="answer",  # factual temp (≈0): bám context, giảm bịa
                    )
                return summarize_results(q_effective, chunks, model=state.get("model"))

            answer = ""
            if QUERY_STREAM_TOKENS and USE_LC_QA_CHAIN:
                try:
                    from app.domains.jobs.jobs_store import append_token as _append_token
                    from app.domains.summary.qa_chain import answer_with_document_context_stream
                    ctx = (state.get("context") or "").strip()
                    if not ctx and chunks:
                        ctx = "\n\n---\n\n".join(str(c) for c in chunks if c)

                    def _stream_answer() -> str:
                        parts: list[str] = []
                        for piece in answer_with_document_context_stream(
                            q,
                            ctx,
                            history=hist if isinstance(hist, list) else None,
                            feature="answer",  # factual temp (≈0): bám context, giảm bịa
                        ):
                            parts.append(piece)
                            try:
                                _append_token(state["job_id"], piece)
                            except Exception:
                                pass
                        return "".join(parts)

                    answer = _call_llm_with_timeout(_stream_answer)
                except TimeoutError:
                    raise RuntimeError(f"AI timeout sau {AI_TIMEOUT}s")
                except Exception:
                    try:
                        answer = _call_llm_with_timeout(_gen)
                    except TimeoutError:
                        raise RuntimeError(f"AI timeout sau {AI_TIMEOUT}s")
            else:
                try:
                    answer = _call_llm_with_timeout(_gen)
                except TimeoutError:
                    raise RuntimeError(f"AI timeout sau {AI_TIMEOUT}s")

            # Stream đôi khi không yield text (chunk content list/khác định dạng) trong khi invoke vẫn có nội dung.
            if not (answer or "").strip():
                try:
                    answer = _call_llm_with_timeout(_gen)
                except TimeoutError:
                    raise RuntimeError(f"AI timeout sau {AI_TIMEOUT}s")

            gen_fallback = False
            if not (answer or "").strip():
                # Message chẩn đoán — KHÔNG phải answer thật → gen_fallback chặn cache ở Finalize
                # (đã từng bị cache → mọi câu hỏi tương đương sau đó trả message lỗi/rỗng).
                gen_fallback = True
                answer = (
                    "Không nhận được phản hồi từ model (nội dung rỗng). "
                    "Thử: `ollama pull` đúng tag SLM_MODEL_CHAT; đặt OLLAMA_REASONING=0 trong .env nếu dùng Qwen think; "
                    "kiểm tra Ollama/logs. (Backend đã invoke với stream=False + đọc reasoning nếu có.)"
                )

            payload = {"answer": answer}
            if state.get("processing_message"):
                payload["processing_message"] = state["processing_message"]

            log_node_event(state["job_id"], "GenerateAnswer", "ok", t.ms())
            return {**state, "payload": payload, "status_code": 200, "answer": answer, "gen_fallback": gen_fallback, "progress": 75, "current_node": "GenerateAnswer", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "GenerateAnswer", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "GenerateAnswer"}

    def evaluate_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=85, current_node="Evaluate")
            if not EVAL_ENABLED:
                return {**state, "eval_score": 1.0, "progress": 85, "current_node": "Evaluate", "error": None}
            # Minimal heuristic: answer rỗng -> score thấp
            ans = (state.get("answer") or "").strip()
            score = 0.2 if not ans else 0.9
            log_node_event(state["job_id"], "Evaluate", "ok", t.ms(), {"score": score})
            return {**state, "eval_score": score, "progress": 85, "current_node": "Evaluate", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "Evaluate", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "Evaluate"}

    def feedback_loop_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=90, current_node="FeedbackLoop")
            if not EVAL_ENABLED:
                return {**state, "progress": 90, "current_node": "FeedbackLoop", "error": None}

            retry = int(state.get("retry_count") or 0)
            score = float(state.get("eval_score") or 1.0)

            if score < EVAL_THRESHOLD and retry < 2:
                log_node_event(state["job_id"], "FeedbackLoop", "ok", t.ms(), {"retry": retry + 1})
                return {**state, "retry_count": retry + 1, "answer": "", "progress": 90, "current_node": "FeedbackLoop", "error": None}

            low_conf = score < EVAL_THRESHOLD
            log_node_event(state["job_id"], "FeedbackLoop", "ok", t.ms(), {"low_confidence": low_conf})
            return {**state, "low_confidence": bool(low_conf), "progress": 90, "current_node": "FeedbackLoop", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "FeedbackLoop", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "FeedbackLoop"}

    def finalize_node(state: dict) -> dict:
        # KHÔNG set status="done" ở đây: result được _finalize_query_job (main.py) gắn SAU
        # khi graph.invoke trả về. Set done sớm → cửa sổ race "done nhưng result=None" —
        # job nhanh (cache hit <1s) bị FE poll trúng → hiện "Không có phản hồi." dù answer có.
        # "done" phải đi CÙNG result trong một lần update (_finalize_query_job lo).
        _set_job(state["job_id"], progress=100, current_node="Finalize")
        cache_key = state.get("cache_key")
        payload = state.get("payload")
        status_code = int(state.get("status_code") or 200)
        # Không cache câu trả lời fallback "không tìm thấy" — re-index sau có thể truy hồi được.
        # Answer rỗng/whitespace KHÔNG BAO GIỜ được ghi (poisoning → hit sau trả rỗng).
        _ans = str(payload.get("answer") or "").strip() if isinstance(payload, dict) else ""
        if state.get("gen_fallback"):
            _ans = ""  # message chẩn đoán không được cache — coi như rỗng ở tầng ghi
        if cache_key and not state.get("crag_fallback"):
            if _ans:
                try:
                    set_cached(cache_key, {"payload": payload, "status": status_code})
                except Exception:
                    llm_cache.METRICS["write_failed"] += 1
                    llm_cache.logger.info("[cache] event=cache_write_failed stage=finalize")
            elif isinstance(payload, dict):
                llm_cache.METRICS["write_skipped_empty"] += 1
                llm_cache.logger.info("[cache] event=cache_write_skipped_empty_answer q=%r", str(state.get("q", ""))[:80])
        log_node_event(state["job_id"], "Finalize", "ok", 0.0)
        return {**state, "progress": 100, "current_node": "Finalize"}

    def error_handler_node(state: dict) -> dict:
        raw = state.get("error")
        err = (str(raw).strip() if raw is not None else "") or "unknown error"
        
        # Check for dimension/embedding mismatch errors
        err_lower = err.lower()
        if any(kw in err_lower for kw in ["dim mismatch", "shape", "embedding", "dimension"]):
            user_msg = "Chỉ mục tài liệu đang không tương thích với embedding model hiện tại. Vui lòng rebuild index hoặc upload lại tài liệu."
            _set_job(state["job_id"], status="error", progress=0, current_node="ErrorHandler", error_text=err)
            log_node_event(state["job_id"], "ErrorHandler", "error", 0.0, {"error": err, "user_message": user_msg})
            return {**state, "current_node": "ErrorHandler", "payload": {"error": user_msg}, "status_code": 500}
        
        _set_job(state["job_id"], status="error", progress=0, current_node="ErrorHandler", error_text=err)
        log_node_event(state["job_id"], "ErrorHandler", "error", 0.0, {"error": err})
        return {**state, "current_node": "ErrorHandler", "payload": {"error": err}, "status_code": 500}

    # ---- CRAG nodes (chỉ wire khi CRAG_ENABLED) ----
    def grade_documents_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=55, current_node="GradeDocuments")
            grade = grading.grade_documents(
                state["q"],
                state.get("retrieved_chunks") or [],
                relevance_threshold=CRAG_RELEVANCE_THRESHOLD,
                wrong_floor=CRAG_WRONG_FLOOR,
                rerank_scores=state.get("rerank_scores"),
            )
            log_node_event(
                state["job_id"], "GradeDocuments", "ok", t.ms(),
                {"grade": grade, "rewrite_count": int(state.get("rewrite_count") or 0)},
            )
            return {**state, "doc_grade": grade, "progress": 55, "current_node": "GradeDocuments", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "GradeDocuments", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "GradeDocuments"}

    def rewrite_query_node(state: dict) -> dict:
        t = _Timer()
        rc = int(state.get("rewrite_count") or 0)
        original_q = state["q"]
        new_q = original_q
        try:
            _set_job(state["job_id"], progress=58, current_node="RewriteQuery")
            try:
                new_q = _call_llm_with_timeout(lambda: query_rewrite.rewrite_query(original_q))
            except TimeoutError:
                # LLM rewrite quá hạn → giữ câu gốc nhưng vẫn tăng budget để chặn vòng lặp.
                new_q = original_q
            log_node_event(
                state["job_id"], "RewriteQuery", "ok", t.ms(),
                {"changed": bool((new_q or "").strip() and new_q != original_q), "rewrite_count": rc + 1},
            )
            # Clear output truy hồi cũ để RetrieveFAISS chạy lại sạch.
            return {
                **state,
                "q": new_q or original_q,
                "rewrite_count": rc + 1,
                "retrieved_chunks": [],
                "retrieved_sources": [],
                "context": "",
                "progress": 58,
                "current_node": "RewriteQuery",
                "error": None,
            }
        except Exception as e:
            # LLM lỗi cũng không fail pipeline — tăng budget rồi để vòng tiếp quyết định.
            log_node_event(state["job_id"], "RewriteQuery", "error", t.ms(), {"error": str(e)})
            return {
                **state,
                "rewrite_count": rc + 1,
                "retrieved_chunks": [],
                "retrieved_sources": [],
                "context": "",
                "progress": 58,
                "current_node": "RewriteQuery",
                "error": None,
            }

    def crag_fallback_node(state: dict) -> dict:
        t = _Timer()
        answer = "Xin lỗi, mình không tìm thấy thông tin phù hợp trong tài liệu đã chọn để trả lời câu hỏi này."
        payload = {"answer": answer}
        if state.get("processing_message"):
            payload["processing_message"] = state["processing_message"]
        log_node_event(state["job_id"], "CRAGFallback", "ok", t.ms())
        return {
            **state,
            "payload": payload,
            "status_code": 200,
            "answer": answer,
            "crag_fallback": True,
            "done": True,
            "current_node": "CRAGFallback",
            "error": None,
        }

    # ---- Supervisor node (chỉ entry khi SUPERVISOR_ENABLED) — Option A: set route + lever sẵn có ----
    def supervisor_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=1, current_node="Supervisor")
            q = (state.get("q") or "").strip().lower()
            mem_kw = ("tóm tắt", "tom tat", "tổng quan", "tong quan", "overview", "summary", "ý chính", "y chinh")
            route = "memory" if any(k in q for k in mem_kw) else "retrieval"
            patch: dict = {"route": route, "current_node": "Supervisor", "error": None}
            if route == "retrieval":
                patch["use_memory_tree"] = False
            log_node_event(state["job_id"], "Supervisor", "ok", t.ms(), {"route": route})
            return {**state, **patch}
        except Exception as e:
            log_node_event(state["job_id"], "Supervisor", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "Supervisor"}

    # ---- HITL review gate (chỉ wire khi HITL_ENABLED và có checkpointer) ----
    def review_gate_node(state: dict) -> dict:
        decision = state.get("review_decision")
        if not decision:
            # Chưa duyệt → tạm dừng chờ người (side-effect-free trước interrupt để idempotent khi re-run).
            # Khi resume bằng Command(resume=...), interrupt() trả về giá trị resume tại đúng đây.
            from langgraph.types import interrupt

            payload = state.get("payload") or {}
            decision = interrupt({"type": "review", "answer": payload.get("answer"), "job_id": state.get("job_id")})

        # Áp dụng quyết định của người duyệt (dùng chung cho cả resume lẫn review_decision pre-set).
        decision = decision or {}
        payload = dict(state.get("payload") or {})
        action = str(decision.get("action") or "approve").lower()
        answer = state.get("answer") or payload.get("answer") or ""
        if action == "edit" and (decision.get("answer") or "").strip():
            answer = str(decision["answer"]).strip()
        elif action == "reject":
            answer = "Câu trả lời đã bị người duyệt từ chối."
        payload["answer"] = answer
        log_node_event(state["job_id"], "ReviewGate", "ok", 0.0, {"action": action})
        return {**state, "payload": payload, "answer": answer, "review_decision": decision, "awaiting_review": False, "current_node": "ReviewGate"}

    def _route_after_grade(s: dict) -> str:
        if s.get("error"):
            return "ErrorHandler"
        grade = s.get("doc_grade") or "correct"
        rc = int(s.get("rewrite_count") or 0)
        if grade == "correct":
            return "Generate"
        if rc >= CRAG_REWRITE_MAX:
            return "Fallback" if grade == "wrong" else "Generate"
        return "Rewrite"

    # LangGraph không cho router trả về '' — phải là một key trong mapping conditional_edges.
    def _route_pre_retrieval(s: dict) -> str:
        if s.get("error"):
            return "ErrorHandler"
        if s.get("done"):
            return "Finalize"
        return "Continue"

    def _route_err_or_continue(s: dict) -> str:
        if s.get("error"):
            return "ErrorHandler"
        return "Continue"

    def _route_feedback_loop(s: dict) -> str:
        if s.get("error"):
            return "ErrorHandler"
        if EVAL_ENABLED and int(s.get("retry_count") or 0) > 0 and not (s.get("answer") or "").strip():
            return "GenerateAnswer"
        return "Finalize"

    # Dựng checkpointer TRƯỚC khi wiring — HITL (interrupt) bắt buộc có checkpointer.
    ck_path = data_dir / "checkpoints.sqlite"
    checkpointer = None
    try:
        checkpointer = sqlite_saver_from_path(ck_path)
    except Exception as exc:
        _log.warning(
            "Query graph: checkpoint SqliteSaver failed (%s); compiling without checkpointer.",
            exc,
        )
    hitl_on = HITL_ENABLED and checkpointer is not None
    if HITL_ENABLED and checkpointer is None:
        _log.warning("HITL_ENABLED nhưng không có checkpointer — bỏ qua review gate.")

    g = StateGraph(QueryState)
    g.add_node("CheckSources", check_sources_node)
    g.add_node("CacheLookup", cache_lookup_node)
    g.add_node("RetrieveMemory", retrieve_memory_node)
    g.add_node("RetrieveFAISS", retrieve_faiss_node)
    g.add_node("ContextBuilder", context_builder_node)
    g.add_node("GenerateAnswer", generate_answer_node)
    g.add_node("Evaluate", evaluate_node)
    g.add_node("FeedbackLoop", feedback_loop_node)
    g.add_node("Finalize", finalize_node)
    g.add_node("ErrorHandler", error_handler_node)

    # Supervisor: entry point khi bật, fall-through về CheckSources.
    if SUPERVISOR_ENABLED:
        g.add_node("Supervisor", supervisor_node)
        g.set_entry_point("Supervisor")
        g.add_edge("Supervisor", "CheckSources")
    else:
        g.set_entry_point("CheckSources")

    # Chuỗi sau retrieve: RetrieveFAISS → [RerankDocuments] → [VerifyContext] → ContextBuilder.
    # Mỗi node giữa chỉ chèn khi cờ bật; tắt hết → topology y hệt cũ.
    post_retrieve_chain = []
    if RERANK_ENABLED:
        post_retrieve_chain.append("RerankDocuments")
    if NLI_ENABLED:
        post_retrieve_chain.append("VerifyContext")
    post_retrieve_chain.append("ContextBuilder")
    retrieve_faiss_next = post_retrieve_chain[0]

    for node_name, next_name in (
        ("CheckSources", "CacheLookup"),
        ("CacheLookup", "RetrieveMemory"),
        ("RetrieveMemory", "RetrieveFAISS"),
        ("RetrieveFAISS", retrieve_faiss_next),
    ):
        g.add_conditional_edges(
            node_name,
            _route_pre_retrieval,
            {"Finalize": "Finalize", "ErrorHandler": "ErrorHandler", "Continue": next_name},
        )

    if RERANK_ENABLED:
        g.add_node("RerankDocuments", rerank_documents_node)
        nxt = post_retrieve_chain[post_retrieve_chain.index("RerankDocuments") + 1]
        g.add_conditional_edges(
            "RerankDocuments",
            _route_err_or_continue,
            {"ErrorHandler": "ErrorHandler", "Continue": nxt},
        )
    if NLI_ENABLED:
        g.add_node("VerifyContext", verify_context_node)
        nxt = post_retrieve_chain[post_retrieve_chain.index("VerifyContext") + 1]
        g.add_conditional_edges(
            "VerifyContext",
            _route_err_or_continue,
            {"ErrorHandler": "ErrorHandler", "Continue": nxt},
        )

    # CRAG: ContextBuilder → GradeDocuments (bật) hoặc → GenerateAnswer (tắt).
    ctx_target = "GradeDocuments" if CRAG_ENABLED else "GenerateAnswer"
    g.add_conditional_edges(
        "ContextBuilder",
        _route_err_or_continue,
        {"ErrorHandler": "ErrorHandler", "Continue": ctx_target},
    )
    if CRAG_ENABLED:
        g.add_node("GradeDocuments", grade_documents_node)
        g.add_node("RewriteQuery", rewrite_query_node)
        g.add_node("CRAGFallback", crag_fallback_node)
        g.add_conditional_edges(
            "GradeDocuments",
            _route_after_grade,
            {"Generate": "GenerateAnswer", "Rewrite": "RewriteQuery", "Fallback": "CRAGFallback", "ErrorHandler": "ErrorHandler"},
        )
        g.add_conditional_edges(
            "RewriteQuery",
            _route_err_or_continue,
            {"ErrorHandler": "ErrorHandler", "Continue": "RetrieveFAISS"},
        )
        g.add_conditional_edges(
            "CRAGFallback",
            _route_err_or_continue,
            {"ErrorHandler": "ErrorHandler", "Continue": "Finalize"},
        )

    g.add_conditional_edges(
        "GenerateAnswer",
        _route_err_or_continue,
        {"ErrorHandler": "ErrorHandler", "Continue": "Evaluate"},
    )
    g.add_conditional_edges(
        "Evaluate",
        _route_err_or_continue,
        {"ErrorHandler": "ErrorHandler", "Continue": "FeedbackLoop"},
    )
    # HITL: nhánh sinh-mới đi qua ReviewGate trước Finalize (cache/memory/fallback vẫn vào thẳng Finalize).
    fb_finalize_target = "ReviewGate" if hitl_on else "Finalize"
    g.add_conditional_edges(
        "FeedbackLoop",
        _route_feedback_loop,
        {"GenerateAnswer": "GenerateAnswer", "Finalize": fb_finalize_target, "ErrorHandler": "ErrorHandler"},
    )
    if hitl_on:
        g.add_node("ReviewGate", review_gate_node)
        g.add_conditional_edges(
            "ReviewGate",
            _route_err_or_continue,
            {"ErrorHandler": "ErrorHandler", "Continue": "Finalize"},
        )

    g.add_edge("Finalize", END)
    g.add_edge("ErrorHandler", END)

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()

