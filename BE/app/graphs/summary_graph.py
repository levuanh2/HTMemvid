# BE/app/graphs/summary_graph.py — Summary v2 section-first (mirror mindmap_graph)
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from app.graphs.logger import _Timer, log_node_event
from app.graphs.sqlite_checkpointer import sqlite_saver_from_path
from app.graphs.state import SummaryState
from services.summary.pipeline import schema as sm_schema


def build_summary_graph(*, data_dir: Path, index_meta_path: Path,
                        jobs_update: Callable[..., None] | None,
                        collect_input: Callable[..., dict],
                        pipeline: Any,
                        persist_record: Callable[[dict], None]) -> Any:
    def _set_job(job_id: str, **kw: Any) -> None:
        if jobs_update is None:
            return
        try:
            jobs_update(job_id, **kw)
        except Exception:
            pass

    def _cancelled(job_id: str) -> bool:
        try:
            from app.domains.jobs.jobs_store import is_cancel_requested
            return is_cancel_requested(job_id)
        except Exception:
            return False

    def _guard(node_name: str):
        def wrap(fn):
            def inner(state: dict) -> dict:
                if _cancelled(state["job_id"]):
                    return {**state, "cancelled": True, "current_node": node_name}
                t = _Timer()
                try:
                    out = fn(state)
                    log_node_event(state["job_id"], node_name, "ok", t.ms())
                    return out
                except Exception as e:
                    log_node_event(state["job_id"], node_name, "error", t.ms(), {"error": str(e)})
                    return {**state, "error": str(e), "current_node": node_name}
            return inner
        return wrap

    @_guard("CollectInput")
    def collect_node(state: dict) -> dict:
        _set_job(state["job_id"], status="running", progress=5, current_node="CollectInput")
        mm = state.get("mm_input") or collect_input(index_meta_path, state.get("source_names") or [])
        mode = state.get("length_mode") or "medium"
        ch = state.get("content_hash") or sm_schema.content_hash(
            mm.get("sources") or [], [c["text"] for c in mm.get("chunks") or []],
            [c.get("heading_path", "") for c in mm.get("chunks") or []], mode)
        if not mm.get("chunks"):
            raise ValueError("Không có chunk nào cho các nguồn đã chọn.")
        return {**state, "mm_input": mm, "content_hash": ch, "length_mode": mode,
                "progress": 10, "current_node": "CollectInput", "_t0": time.time(),
                "error": None}

    @_guard("Sections")
    def sections_node(state: dict) -> dict:
        _set_job(state["job_id"], progress=15, current_node="Sections")
        sections, method = pipeline.sections(state["mm_input"])
        missing = list(state.get("degraded_missing") or [])
        if method == "single":
            # Không dựng được mục lục (deterministic + LLM outline đều bó tay) —
            # tóm tắt vẫn chạy trên 1 section toàn doc nhưng phải khai degraded.
            missing.append("skeleton")
        _set_job(state["job_id"], progress=20, current_node="Sections")
        return {**state, "sections": sections, "skeleton_method": method,
                "degraded_missing": missing, "progress": 20, "current_node": "Sections"}

    @_guard("SummarizeSections")
    def summarize_node(state: dict) -> dict:
        _set_job(state["job_id"], progress=30, current_node="SummarizeSections")
        def _prog(p: int, msg: str) -> None:
            _set_job(state["job_id"], progress=p, current_node=msg)
        summaries, missing_sections = pipeline.summarize(
            state["mm_input"], state["sections"], length_mode=state.get("length_mode") or "medium",
            progress_cb=_prog, cancel_cb=lambda: _cancelled(state["job_id"]))
        missing = list(state.get("degraded_missing") or []) + list(missing_sections or [])
        return {**state, "section_summaries": summaries, "degraded_missing": missing,
                "progress": 70, "current_node": "SummarizeSections"}

    @_guard("Synthesize")
    def synthesize_node(state: dict) -> dict:
        _set_job(state["job_id"], progress=75, current_node="Synthesize")
        meta, degraded = pipeline.synthesize(
            state["section_summaries"], doc_title=state["mm_input"]["title"],
            length_mode=state.get("length_mode") or "medium")
        missing = list(state.get("degraded_missing") or [])
        if degraded:
            missing.append("synthesize")
        return {**state, "overview_meta": meta, "degraded_missing": missing,
                "progress": 85, "current_node": "Synthesize"}

    @_guard("AssemblePersist")
    def assemble_node(state: dict) -> dict:
        import os
        elapsed = time.time() - (state.get("_t0") or time.time())
        mm = state["mm_input"]
        meta = state.get("overview_meta") or {}
        valid_ids = {str(k) for c in mm.get("chunks") or [] for k in (c.get("chunk_keys") or [])}
        record = sm_schema.build_record(
            title=(meta.get("title") or mm["title"]),
            sources=mm["sources"],
            length_mode=state.get("length_mode") or "medium",
            overview=meta.get("overview") or "",
            sections=sm_schema.sanitize_sections(state.get("section_summaries") or [], valid_ids),
            entities=meta.get("entities") or [],
            content_hash_value=state["content_hash"],
            model=os.getenv("SLM_MODEL_SUMMARY", "qwen2.5:14b"),
            elapsed_sec=elapsed,
            degraded_missing=state.get("degraded_missing") or [],
            skeleton_method=state.get("skeleton_method") or "")
        persist_record(record)
        # done PHẢI đi cùng result trong MỘT update (bài học race 2026-07-06)
        _set_job(state["job_id"], status="done", progress=100,
                 current_node="AssemblePersist", result=record)
        return {**state, "result": record, "progress": 100, "current_node": "AssemblePersist"}

    def cancelled_node(state: dict) -> dict:
        _set_job(state["job_id"], status="cancelled", progress=0, current_node="Cancelled")
        return {**state, "cancelled": True, "current_node": "Cancelled"}

    def error_node(state: dict) -> dict:
        err = (str(state.get("error") or "").strip()) or "unknown error"
        _set_job(state["job_id"], status="error", progress=0,
                 current_node="ErrorHandler", error_text=err)
        return {**state, "current_node": "ErrorHandler"}

    def _route(s: dict) -> str:
        if s.get("cancelled"):
            return "Cancelled"
        if s.get("error"):
            return "ErrorHandler"
        return "Continue"

    g = StateGraph(SummaryState)
    g.add_node("CollectInput", collect_node)
    g.add_node("Sections", sections_node)
    g.add_node("SummarizeSections", summarize_node)
    g.add_node("Synthesize", synthesize_node)
    g.add_node("AssemblePersist", assemble_node)
    g.add_node("Cancelled", cancelled_node)
    g.add_node("ErrorHandler", error_node)
    g.set_entry_point("CollectInput")
    routes = {"Cancelled": "Cancelled", "ErrorHandler": "ErrorHandler"}
    g.add_conditional_edges("CollectInput", _route, {**routes, "Continue": "Sections"})
    g.add_conditional_edges("Sections", _route, {**routes, "Continue": "SummarizeSections"})
    g.add_conditional_edges("SummarizeSections", _route, {**routes, "Continue": "Synthesize"})
    g.add_conditional_edges("Synthesize", _route, {**routes, "Continue": "AssemblePersist"})
    g.add_conditional_edges("AssemblePersist", _route, {**routes, "Continue": END})
    g.add_edge("Cancelled", END)
    g.add_edge("ErrorHandler", END)
    return g.compile(checkpointer=sqlite_saver_from_path(data_dir / "checkpoints.sqlite"))
