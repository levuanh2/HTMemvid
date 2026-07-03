# BE/app/graphs/mindmap_graph.py — 5 node skeleton-first (spec §4)
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from app.graphs.logger import _Timer, log_node_event
from app.graphs.sqlite_checkpointer import sqlite_saver_from_path
from app.graphs.state import MindmapState
from services.mindmap.pipeline import schema as mm_schema


def build_mindmap_graph(*, data_dir: Path, index_meta_path: Path,
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
        """Decorator-ish: check cancel trước node; lỗi hệ thống → error state."""
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
        ch = state.get("content_hash") or mm_schema.content_hash(
            mm.get("sources") or [], [c["text"] for c in mm.get("chunks") or []])
        if not mm.get("chunks"):
            raise ValueError("Không có chunk nào cho các nguồn đã chọn.")
        return {**state, "mm_input": mm, "content_hash": ch, "progress": 10,
                "current_node": "CollectInput", "_t0": time.time(), "error": None}

    @_guard("Skeleton")
    def skeleton_node(state: dict) -> dict:
        _set_job(state["job_id"], progress=15, current_node="Skeleton")
        nodes, method = pipeline.skeleton(state["mm_input"])
        # preview cho FE render ngay (spec §4.2.2)
        _set_job(state["job_id"], progress=20,
                 result={"partial": {"title": state["mm_input"]["title"], "nodes": nodes}})
        return {**state, "skeleton": nodes, "skeleton_method": method,
                "progress": 20, "current_node": "Skeleton"}

    @_guard("Enrich")
    def enrich_node(state: dict) -> dict:
        _set_job(state["job_id"], progress=30, current_node="Enrich")
        def _prog(p: int, msg: str) -> None:
            _set_job(state["job_id"], progress=p, current_node=msg)
        nodes, degraded = pipeline.enrich(state["mm_input"], state["skeleton"],
                                          progress_cb=_prog,
                                          cancel_cb=lambda: _cancelled(state["job_id"]))
        missing = list(state.get("degraded_missing") or [])
        if degraded:
            missing.append("enrich")
        return {**state, "nodes": nodes, "degraded_missing": missing,
                "progress": 70, "current_node": "Enrich"}

    @_guard("Relations")
    def relations_node(state: dict) -> dict:
        _set_job(state["job_id"], progress=75, current_node="Relations")
        rels, degraded = pipeline.relations(state["nodes"],
                                            cancel_cb=lambda: _cancelled(state["job_id"]))
        missing = list(state.get("degraded_missing") or [])
        if degraded:
            missing.append("relations")
        return {**state, "relations": rels, "degraded_missing": missing,
                "progress": 85, "current_node": "Relations"}

    @_guard("AssemblePersist")
    def assemble_node(state: dict) -> dict:
        import os
        elapsed = time.time() - (state.get("_t0") or time.time())
        record = mm_schema.build_record(
            title=state["mm_input"]["title"], sources=state["mm_input"]["sources"],
            nodes=mm_schema.sanitize_nodes(state["nodes"]),
            relations=mm_schema.validate_relations(state.get("relations") or [], state["nodes"]),
            content_hash_value=state["content_hash"],
            model=os.getenv("MINDMAP_MODEL", "qwen2.5:14b"),
            elapsed_sec=elapsed, degraded_missing=state.get("degraded_missing") or [])
        persist_record(record)
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

    g = StateGraph(MindmapState)
    g.add_node("CollectInput", collect_node)
    g.add_node("Skeleton", skeleton_node)
    g.add_node("Enrich", enrich_node)
    g.add_node("Relations", relations_node)
    g.add_node("AssemblePersist", assemble_node)
    g.add_node("Cancelled", cancelled_node)
    g.add_node("ErrorHandler", error_node)
    g.set_entry_point("CollectInput")
    routes = {"Cancelled": "Cancelled", "ErrorHandler": "ErrorHandler"}
    g.add_conditional_edges("CollectInput", _route, {**routes, "Continue": "Skeleton"})
    g.add_conditional_edges("Skeleton", _route, {**routes, "Continue": "Enrich"})
    g.add_conditional_edges("Enrich", _route, {**routes, "Continue": "Relations"})
    g.add_conditional_edges("Relations", _route, {**routes, "Continue": "AssemblePersist"})
    g.add_conditional_edges("AssemblePersist", _route, {**routes, "Continue": END})
    g.add_edge("Cancelled", END)
    g.add_edge("ErrorHandler", END)
    return g.compile(checkpointer=sqlite_saver_from_path(data_dir / "checkpoints.sqlite"))
