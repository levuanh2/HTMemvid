from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Optional

from langgraph.graph import END, StateGraph

from app.graphs.logger import _Timer, log_node_event
from app.graphs.sqlite_checkpointer import sqlite_saver_from_path
from app.graphs.state import MindmapState
try:
    from app.graphs.mindmap_schema import should_validate_schema, validate_mindmap_record
except Exception:
    should_validate_schema = lambda: False  # type: ignore

    def validate_mindmap_record(record):  # type: ignore
        return record


# Guard: block iterative for non-quality modes
SLOW_STRATEGIES = {"cmgn", "iterative"}
QUALITY_MODE = "quality"


def _normalize_mode(mode: Optional[str]) -> str:
    """Ensure mode is one of valid modes."""
    if mode and mode in {"fast", "balanced", "quality"}:
        return mode
    return "balanced"


def _normalize_strategy(strategy: Optional[str]) -> str:
    """Ensure strategy is valid."""
    valid = {"auto", "single_call_schema", "mindmap_v2", "cmgn_light", "cmgn", "multilevel_fast", "multilevel", "iterative"}
    if strategy and strategy in valid:
        return strategy
    return "auto"


def _apply_strategy_guard(strategy: str, mode: str) -> str:
    """Apply guards: block slow strategies for non-quality modes."""
    if mode != QUALITY_MODE and strategy in SLOW_STRATEGIES:
        print(f"[MindMap Guard] strategy={strategy} blocked for mode={mode}, using auto")
        return "auto"
    return strategy


def build_mindmap_graph(
    *,
    data_dir: Path,
    index_meta_path: Path,
    jobs_update: Callable[..., None] | None,
    run_mindmap_generation: Callable[..., dict],
    append_mindmap: Callable[[dict], None],
) -> Any:
    def _set_job(job_id: str, **kw: Any) -> None:
        if jobs_update is None:
            return
        try:
            jobs_update(job_id, **kw)
        except Exception:
            pass

    def generate_node(state: dict) -> dict:
        t = _Timer()

        def _progress(p: int) -> None:
            _set_job(state["job_id"], status="running", progress=int(p), current_node="GenerateMindmap")

        try:
            _set_job(state["job_id"], status="running", progress=5, current_node="GenerateMindmap")

            # Parse and normalize mode/strategy from state
            raw_mode = state.get("generation_mode") or state.get("mode") or "balanced"
            raw_strategy = state.get("strategy_requested") or state.get("strategy") or "auto"

            generation_mode = _normalize_mode(raw_mode)
            strategy_requested = _normalize_strategy(raw_strategy)
            strategy_requested = _apply_strategy_guard(strategy_requested, generation_mode)

            # Log the parsed values
            print("[MindMap Graph]", {
                "job_id": state["job_id"],
                "mode": generation_mode,
                "strategy_requested": strategy_requested,
                "raw_mode": raw_mode,
                "raw_strategy": raw_strategy,
            })

            record = run_mindmap_generation(
                index_meta_path,
                state.get("source_names") or [],
                strategy_requested,
                append_mindmap,
                progress_cb=_progress,
                generation_mode=generation_mode,
            )
            if should_validate_schema():
                try:
                    record = validate_mindmap_record(record if isinstance(record, dict) else {})
                except Exception as ve:
                    log_node_event(state["job_id"], "GenerateMindmap", "error", t.ms(), {"schema": str(ve)})
                    return {**state, "error": f"Mindmap schema: {ve}", "current_node": "GenerateMindmap"}
            log_node_event(state["job_id"], "GenerateMindmap", "ok", t.ms(), {"nodes": len(record.get("nodes") or [])})
            return {**state, "result": record, "progress": 80, "current_node": "GenerateMindmap", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "GenerateMindmap", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "GenerateMindmap"}

    def finalize_node(state: dict) -> dict:
        _set_job(state["job_id"], status="done", progress=100, current_node="Finalize", result=state.get("result"))
        log_node_event(state["job_id"], "Finalize", "ok", 0.0)
        return {**state, "progress": 100, "current_node": "Finalize"}

    def error_handler_node(state: dict) -> dict:
        raw = state.get("error")
        err = (str(raw).strip() if raw is not None else "") or "unknown error"
        _set_job(state["job_id"], status="error", progress=0, current_node="ErrorHandler", error_text=err)
        log_node_event(state["job_id"], "ErrorHandler", "error", 0.0, {"error": err})
        return {**state, "current_node": "ErrorHandler"}

    def _route_err_or_continue(s: dict) -> str:
        return "ErrorHandler" if s.get("error") else "Continue"

    g = StateGraph(MindmapState)
    g.add_node("GenerateMindmap", generate_node)
    g.add_node("Finalize", finalize_node)
    g.add_node("ErrorHandler", error_handler_node)

    g.set_entry_point("GenerateMindmap")
    g.add_conditional_edges(
        "GenerateMindmap",
        _route_err_or_continue,
        {"ErrorHandler": "ErrorHandler", "Continue": "Finalize"},
    )
    g.add_edge("Finalize", END)
    g.add_edge("ErrorHandler", END)

    checkpointer = sqlite_saver_from_path(data_dir / "checkpoints.sqlite")
    return g.compile(checkpointer=checkpointer)

