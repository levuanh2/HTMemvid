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


def test_enrich_nested_children_become_detail_nodes(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    def fake_ask(prompt, system_prompt=None, model=None, feature=None, options=None, **kw):
        return json.dumps({"title": "Khái niệm A", "note": "Tóm.", "children": [
            {"title": "Định nghĩa", "note": "n", "chunk_keys": ["0"], "children": [
                {"title": "Ví dụ cụ thể", "note": "vd", "chunk_keys": ["0", "BỊA"]},
            ]},
        ]})
    monkeypatch.setattr(en, "ask_ai", fake_ask)
    mm, skeleton = _input_and_skeleton()
    nodes, degraded = en.enrich_branches(mm, skeleton, model="m", timeout_sec=5)
    assert degraded is False
    idea = next(n for n in nodes if n["title"] == "Định nghĩa")
    detail = next(n for n in nodes if n["title"] == "Ví dụ cụ thể")
    assert detail["parent"] == idea["id"]
    assert detail["kind"] == "detail"
    assert detail["chunk_refs"] == ["0"]  # "BỊA" bị lọc ở cả tầng detail


def test_enrich_numeric_chunk_keys_stored_as_strings(monkeypatch):
    # codex #3: model trả chunk_keys dạng số [0] → chunk_refs phải là chuỗi "0"
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    def fake_ask(*a, **kw):
        return json.dumps({"title": "T", "note": "", "children": [
            {"title": "Con", "note": "", "chunk_keys": [0]}]})
    monkeypatch.setattr(en, "ask_ai", fake_ask)
    mm, skeleton = _input_and_skeleton()
    nodes, _ = en.enrich_branches(mm, skeleton, model="m", timeout_sec=5)
    kid = next(n for n in nodes if n["title"] == "Con")
    assert kid["chunk_refs"] == ["0"]


def test_enrich_retries_once_on_malformed_json(monkeypatch):
    # LLM thi thoảng trả JSON hỏng (đo thật 1/4 nhánh) — retry 1 lần trước khi degraded
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    calls = {"n": 0}
    def flaky_ask(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"title": "hỏng", broken'
        return json.dumps({"title": "OK sau retry", "note": "n", "children": []})
    monkeypatch.setattr(en, "ask_ai", flaky_ask)
    mm, skeleton = _input_and_skeleton()
    nodes, degraded = en.enrich_branches(mm, skeleton[:2], model="m", timeout_sec=5)  # root + 1 section
    assert degraded is False
    assert any(n["title"] == "OK sau retry" for n in nodes)
    assert calls["n"] == 2
