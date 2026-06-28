"""Supervisor router: phân loại câu hỏi → set route + lever use_memory_tree."""

from __future__ import annotations

from tests._qg_build import base_env, build, init_state, run


def test_supervisor_off_no_route(monkeypatch):
    base_env(monkeypatch, SUPERVISOR_ENABLED="0")
    g, _ = build()
    out = run(g, init_state("giá trị X là gì"))
    assert out.get("route") in (None, "")


def test_supervisor_routes_summary_to_memory(monkeypatch):
    base_env(monkeypatch, SUPERVISOR_ENABLED="1")
    g, _ = build()
    out = run(g, init_state("tóm tắt tài liệu này"))
    assert out["route"] == "memory"


def test_supervisor_routes_factual_to_retrieval(monkeypatch):
    base_env(monkeypatch, SUPERVISOR_ENABLED="1")
    g, _ = build()
    out = run(g, init_state("giá trị X trong báo cáo là bao nhiêu"))
    assert out["route"] == "retrieval"
    assert out["use_memory_tree"] is False
