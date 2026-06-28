"""HITL: interrupt() trước Finalize + resume bằng Command(resume=...)."""

from __future__ import annotations

from langgraph.types import Command

from tests._qg_build import base_env, build, init_state, run


def _interrupt_value(g, cfg):
    """langgraph 0.2.x: interrupt phát hiện qua get_state().tasks[].interrupts."""
    st = g.get_state(cfg)
    assert st.next, f"expected paused graph, next={st.next}"
    for task in st.tasks:
        if getattr(task, "interrupts", None):
            return task.interrupts[0].value
    raise AssertionError("no interrupt found in tasks")


def test_hitl_off_no_interrupt(monkeypatch):
    base_env(monkeypatch, HITL_ENABLED="0")
    g, _ = build()
    out = run(g, init_state("q"))
    assert "__interrupt__" not in out
    assert out["payload"]["answer"] == "generated answer"


def test_hitl_interrupts_then_approves(monkeypatch):
    base_env(monkeypatch, HITL_ENABLED="1")
    g, _ = build()
    cfg = {"configurable": {"thread_id": "hitl-1"}}
    g.invoke(init_state("q"), config=cfg)
    val = _interrupt_value(g, cfg)
    assert val["type"] == "review"
    assert val["answer"] == "generated answer"
    final = g.invoke(Command(resume={"action": "approve"}), config=cfg)
    assert final["payload"]["answer"] == "generated answer"
    assert final.get("awaiting_review") is False


def test_hitl_edit_overrides_answer(monkeypatch):
    base_env(monkeypatch, HITL_ENABLED="1")
    g, _ = build()
    cfg = {"configurable": {"thread_id": "hitl-2"}}
    g.invoke(init_state("q"), config=cfg)
    final = g.invoke(Command(resume={"action": "edit", "answer": "đã sửa"}), config=cfg)
    assert final["payload"]["answer"] == "đã sửa"


def test_hitl_without_checkpointer_degrades(monkeypatch):
    base_env(monkeypatch, HITL_ENABLED="1")
    # Ép checkpointer dựng thất bại → graph không có ReviewGate.
    import app.graphs.query_graph as qg
    monkeypatch.setattr(qg, "sqlite_saver_from_path", lambda p: (_ for _ in ()).throw(RuntimeError("no ck")))
    g, _ = build()
    nodes = set(g.get_graph().nodes)
    assert "ReviewGate" not in nodes
    out = g.invoke(init_state("q"))  # không checkpointer → không cần thread_id
    assert out["payload"]["answer"] == "generated answer"  # degrade về no-review, chạy thẳng
