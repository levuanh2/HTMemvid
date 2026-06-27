from __future__ import annotations

import json
import logging
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
from app.domains.retrieval.ensemble_retriever import hybrid_retrieve_with_ensemble
from app.domains.retrieval.hybrid import HybridRetriever
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
    make_cache_key: Callable[[str, list, bool], str],
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
            # Multi-turn: không cache vì phụ thuộc history
            if state.get("conversation_history"):
                return {**state, "cache_key": None, "progress": 5, "current_node": "CacheLookup", "error": None}

            cache_key = make_cache_key(
                state["q"],
                state.get("selected_sources") or [],
                bool(state.get("use_memory_tree")),
                {"category": state.get("category"), "language": state.get("language")},
            )
            cached = get_cached(cache_key)
            if cached and isinstance(cached, dict) and cached.get("payload"):
                payload = cached["payload"]
                status = int(cached.get("status", 200))
                log_node_event(state["job_id"], "CacheLookup", "ok", t.ms(), {"hit": True})
                return {**state, "payload": payload, "status_code": status, "cache_key": cache_key, "done": True, "progress": 5, "current_node": "CacheLookup"}

            log_node_event(state["job_id"], "CacheLookup", "ok", t.ms(), {"hit": False})
            return {**state, "cache_key": cache_key, "progress": 5, "current_node": "CacheLookup", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "CacheLookup", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "CacheLookup"}

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

            def _do_hybrid_retrieve():
                if USE_LC_ENSEMBLE:
                    return hybrid_retrieve_with_ensemble(
                        retriever,
                        state["q"],
                        selected_sources=selected_sources,
                        top_k=HYBRID_TOP_K,
                        category=f_category,
                        language=f_language,
                    )
                return retriever.retrieve(
                    state["q"],
                    selected_sources=selected_sources,
                    top_k=HYBRID_TOP_K,
                    category=f_category,
                    language=f_language,
                )

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
            sources_seen: list[str] = []
            for item in retrieved:
                txt = item.text or ""
                if _INCLUDE_CHUNK_SOURCE_TAGS:
                    chunks_with_citation.append(f"[Nguồn: {item.video_stem}, đoạn {item.chunk_id}]\n{txt}")
                else:
                    chunks_with_citation.append(txt)
                stem = (item.video_stem or "").strip()
                if stem and stem not in sources_seen:
                    sources_seen.append(stem)

            log_node_event(state["job_id"], "RetrieveFAISS", "ok", t.ms(), {"chunks": len(chunks_with_citation)})
            return {
                **state,
                **hist_patch,
                "retrieved_chunks": chunks_with_citation,
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
                        feature="chat",
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
                            feature="chat",
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

            if not (answer or "").strip():
                answer = (
                    "Không nhận được phản hồi từ model (nội dung rỗng). "
                    "Thử: `ollama pull` đúng tag SLM_MODEL_CHAT; đặt OLLAMA_REASONING=0 trong .env nếu dùng Qwen think; "
                    "kiểm tra Ollama/logs. (Backend đã invoke với stream=False + đọc reasoning nếu có.)"
                )

            payload = {"answer": answer}
            if state.get("processing_message"):
                payload["processing_message"] = state["processing_message"]

            log_node_event(state["job_id"], "GenerateAnswer", "ok", t.ms())
            return {**state, "payload": payload, "status_code": 200, "answer": answer, "progress": 75, "current_node": "GenerateAnswer", "error": None}
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
        _set_job(state["job_id"], status="done", progress=100, current_node="Finalize")
        cache_key = state.get("cache_key")
        payload = state.get("payload")
        status_code = int(state.get("status_code") or 200)
        if cache_key and isinstance(payload, dict) and payload.get("answer"):
            try:
                set_cached(cache_key, {"payload": payload, "status": status_code})
            except Exception:
                pass
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

    g.set_entry_point("CheckSources")

    for node_name, next_name in (
        ("CheckSources", "CacheLookup"),
        ("CacheLookup", "RetrieveMemory"),
        ("RetrieveMemory", "RetrieveFAISS"),
        ("RetrieveFAISS", "ContextBuilder"),
    ):
        g.add_conditional_edges(
            node_name,
            _route_pre_retrieval,
            {"Finalize": "Finalize", "ErrorHandler": "ErrorHandler", "Continue": next_name},
        )

    g.add_conditional_edges(
        "ContextBuilder",
        _route_err_or_continue,
        {"ErrorHandler": "ErrorHandler", "Continue": "GenerateAnswer"},
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
    g.add_conditional_edges(
        "FeedbackLoop",
        _route_feedback_loop,
        {"GenerateAnswer": "GenerateAnswer", "Finalize": "Finalize", "ErrorHandler": "ErrorHandler"},
    )

    g.add_edge("Finalize", END)
    g.add_edge("ErrorHandler", END)

    ck_path = data_dir / "checkpoints.sqlite"
    try:
        checkpointer = sqlite_saver_from_path(ck_path)
        return g.compile(checkpointer=checkpointer)
    except Exception as exc:
        _log.warning(
            "Query graph: checkpoint SqliteSaver failed (%s); compiling without checkpointer.",
            exc,
        )
        return g.compile()

