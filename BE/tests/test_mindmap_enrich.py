import json
import pytest
from services.mindmap.pipeline import enrich as en
from services.mindmap.pipeline.skeleton import build_skeleton


def _input_and_skeleton():
    chunks = [
        {"key": "0", "text": "định nghĩa khái niệm A rất dài", "heading_path": "1. Khái niệm", "chunk_keys": ["0"]},
        {"key": "1", "text": "các bước của phương pháp B", "heading_path": "2. Phương pháp", "chunk_keys": ["1"]},
    ]
    mm = {"title": "Doc", "sources": ["d"], "chunks": chunks, "tree_sections": []}
    nodes, _ = build_skeleton(mm)
    return mm, nodes


def test_enrich_merges_llm_children_with_valid_chunk_refs(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    def fake_ask(prompt, system_prompt=None, model=None, feature=None, options=None, **kw):
        return json.dumps({"title": "Khái niệm A", "note": "Tóm 1 câu.",
                           "children": [{"title": "Định nghĩa", "note": "n", "chunk_keys": ["0", "BỊA"]}]})
    monkeypatch.setattr(en, "ask_ai", fake_ask)
    mm, skeleton = _input_and_skeleton()
    nodes, degraded = en.enrich_branches(mm, skeleton, model="m", timeout_sec=5)
    assert degraded is False
    enriched_branch = next(n for n in nodes if n["title"] == "Khái niệm A")
    assert enriched_branch["note"] == "Tóm 1 câu."
    kid = next(n for n in nodes if n["title"] == "Định nghĩa")
    assert kid["parent"] == enriched_branch["id"]
    assert kid["chunk_refs"] == ["0"]           # "BỊA" bị lọc — chỉ giữ key thuộc nhánh


def test_enrich_branch_failure_keeps_skeleton_sets_degraded(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    def boom(*a, **kw):
        raise RuntimeError("llm chết")
    monkeypatch.setattr(en, "ask_ai", boom)
    mm, skeleton = _input_and_skeleton()
    nodes, degraded = en.enrich_branches(mm, skeleton, model="m", timeout_sec=5)
    assert degraded is True
    assert {n["title"] for n in nodes} >= {"1. Khái niệm", "2. Phương pháp"}  # skeleton còn nguyên


def test_enrich_respects_cancel(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    calls = {"n": 0}
    def fake_ask(*a, **kw):
        calls["n"] += 1
        return json.dumps({"title": "X", "note": "", "children": []})
    monkeypatch.setattr(en, "ask_ai", fake_ask)
    mm, skeleton = _input_and_skeleton()
    nodes, _ = en.enrich_branches(mm, skeleton, model="m", timeout_sec=5, cancel_cb=lambda: True)
    assert calls["n"] == 0                       # huỷ trước khi gọi
