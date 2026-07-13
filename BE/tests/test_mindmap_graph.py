# BE/tests/test_mindmap_graph.py — graph THẬT, pipeline stub (bài học conftest-mock)
import json
from pathlib import Path
import pytest


def _build(tmp_path, pipeline=None, persist=None, jobs_updates=None):
    from app.graphs.mindmap_graph import build_mindmap_graph
    meta_path = tmp_path / "index.json"
    meta_path.write_text(json.dumps({"0": {"source_stem": "a_docx", "heading_path": "1. Mở đầu"}}), encoding="utf-8")

    def collect_input(index_meta_path, source_names):
        return {"title": "Doc", "sources": ["a_docx"],
                "chunks": [{"key": "0", "text": "t", "heading_path": "1. Mở đầu", "chunk_keys": ["0"]}],
                "tree_sections": []}

    class StubPipeline:
        def skeleton(self, mm):
            return ([{"id": "n0", "parent": None, "kind": "root", "title": "Doc"},
                     {"id": "n1", "parent": "n0", "kind": "section", "title": "1. Mở đầu", "chunk_refs": ["0"]}],
                    "headings")
        def enrich(self, mm, skeleton, progress_cb=None, cancel_cb=None):
            return skeleton, False
        def relations(self, nodes, cancel_cb=None):
            return [], False

    def _jobs_update(job_id, **kw):
        (jobs_updates if jobs_updates is not None else []).append(kw)

    _persist = persist or (lambda r: None)
    return build_mindmap_graph(
        data_dir=tmp_path, index_meta_path=meta_path,
        jobs_update=_jobs_update, collect_input=collect_input,
        pipeline=pipeline or StubPipeline(),
        # Phase D: persist_record now takes a user_id kwarg; absorb it for the stub.
        persist_record=lambda r, **_k: _persist(r),
    )

def test_real_graph_compiles_and_produces_v2_record(tmp_path):
    saved = []
    g = _build(tmp_path, persist=saved.append)
    out = g.invoke({"job_id": "j1", "source_names": ["a_docx"], "progress": 0,
                    "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "j1"}})
    assert out.get("error") is None
    rec = out["result"]
    assert rec["schema_version"] == 2 and rec["nodes"] and "relations" in rec
    assert saved and saved[0]["id"] == rec["id"]

def test_cancel_before_enrich_stops_without_persist(tmp_path, monkeypatch):
    from app.domains.jobs import jobs_store as js
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.create_job("j2", job_type="mindmap")
    js.request_cancel("j2")
    saved = []
    g = _build(tmp_path, persist=saved.append)
    out = g.invoke({"job_id": "j2", "source_names": ["a_docx"], "progress": 0,
                    "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "j2"}})
    assert out.get("cancelled") is True
    assert saved == []

def test_degraded_stage_flows_to_result(tmp_path):
    class DegradedPipeline:
        def skeleton(self, mm):
            return ([{"id": "n0", "parent": None, "kind": "root", "title": "Doc"},
                     {"id": "n1", "parent": "n0", "kind": "section", "title": "S"}], "headings")
        def enrich(self, mm, sk, progress_cb=None, cancel_cb=None):
            return sk, True
        def relations(self, nodes, cancel_cb=None):
            return [], True
    g = _build(tmp_path, pipeline=DegradedPipeline())
    out = g.invoke({"job_id": "j3", "source_names": ["a_docx"], "progress": 0,
                    "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "j3"}})
    assert out["result"]["generator"]["degraded"] is True
    assert set(out["result"]["generator"]["missing"]) == {"enrich", "relations"}
