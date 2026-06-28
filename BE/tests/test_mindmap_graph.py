"""Dựng build_mindmap_graph() THẬT (StateGraph(MindmapState)) với callable stub.

Đóng gap conftest-mock (MINDMAP_GRAPH bị mock → không bao giờ dựng graph thật):
bắt lỗi pydantic/langgraph khi schema MindmapState hoặc topology thay đổi.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("SKIP_MODEL_LOAD", "1")

from app.graphs.mindmap_graph import build_mindmap_graph


def _build(*, stub_record=None, raises=False, captured=None):
    d = Path(tempfile.mkdtemp())

    def run_gen(index_meta_path, source_names, strategy_requested, append_mindmap, progress_cb=None, generation_mode="balanced"):
        if captured is not None:
            captured["strategy_requested"] = strategy_requested
            captured["generation_mode"] = generation_mode
        if raises:
            raise RuntimeError("boom")
        if progress_cb:
            progress_cb(50)
        return stub_record or {
            "id": "m", "title": "t",
            "nodes": [{"id": "root", "parent": None, "title": "t"}],
            "sources": source_names, "strategy": strategy_requested, "mode": generation_mode,
        }

    return build_mindmap_graph(
        data_dir=d,
        index_meta_path=d / "index.json",
        jobs_update=None,
        run_mindmap_generation=run_gen,
        append_mindmap=lambda r: None,
    )


def _init(**over):
    s = {
        "job_id": "j1",
        "source_names": ["My Report.pdf"],
        "strategy": "auto",
        "generation_mode": "balanced",
        "strategy_requested": "auto",
        "result": {},
        "progress": 0,
        "current_node": "Queued",
        "error": None,
    }
    s.update(over)
    return s


def _run(g, state, thread_id="t"):
    return g.invoke(state, config={"configurable": {"thread_id": thread_id}})


def test_build_and_invoke_returns_result():
    g = _build()
    out = _run(g, _init())
    assert out["result"]["id"] == "m"
    assert out["current_node"] == "Finalize"
    assert not out.get("error")


def test_error_routes_to_error_handler():
    g = _build(raises=True)
    out = _run(g, _init(), thread_id="t2")
    assert out.get("error")
    assert out["current_node"] == "ErrorHandler"


def test_mode_and_strategy_propagated_to_worker():
    # Regression: trước đây generation_mode bị bỏ qua (luôn balanced) + strategy nhét nhầm field.
    cap = {}
    g = _build(captured=cap)
    _run(g, _init(generation_mode="quality", strategy_requested="cmgn"), thread_id="t3")
    assert cap["generation_mode"] == "quality"
    assert cap["strategy_requested"] == "cmgn"
