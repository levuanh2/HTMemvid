# BE/tests/test_summary_graph.py — graph THẬT, pipeline stub (bài học conftest-mock)
import json

import pytest


def _build(tmp_path, pipeline=None, persist=None, jobs_updates=None):
    from app.graphs.summary_graph import build_summary_graph
    meta_path = tmp_path / "index.json"
    meta_path.write_text(json.dumps({"0": {"source_stem": "a_docx"}}), encoding="utf-8")

    def collect_input(index_meta_path, source_names):
        return {"title": "Doc", "sources": ["a_docx"],
                "chunks": [{"key": "0", "text": "t", "heading_path": "1. A", "chunk_keys": ["0"]}],
                "tree_sections": []}

    class StubPipeline:
        def sections(self, mm):
            return [{"id": "s1", "title": "A", "chunk_refs": ["0"], "order": 0}], "headings"

        def summarize(self, mm, sections, length_mode="medium", progress_cb=None, cancel_cb=None):
            return ([{**s, "summary": "tóm tắt", "key_points": ["ý"]} for s in sections], [])

        def synthesize(self, sections, doc_title="", length_mode="medium"):
            return {"title": doc_title, "overview": "tổng quan", "entities": ["E"]}, False

    updates = jobs_updates if jobs_updates is not None else []

    def _jobs_update(job_id, **kw):
        updates.append(kw)

    return build_summary_graph(
        data_dir=tmp_path, index_meta_path=meta_path,
        jobs_update=_jobs_update, collect_input=collect_input,
        pipeline=pipeline or StubPipeline(), persist_record=persist or (lambda r: None),
    )


def test_real_graph_compiles_and_produces_v2_record(tmp_path):
    saved = []
    updates = []
    g = _build(tmp_path, persist=saved.append, jobs_updates=updates)
    out = g.invoke({"job_id": "sj1", "source_names": ["a_docx"], "length_mode": "short",
                    "progress": 0, "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sj1"}})
    assert out.get("error") is None
    rec = out["result"]
    assert rec["schema_version"] == 2
    assert rec["length_mode"] == "short"
    assert rec["overview"] == "tổng quan"
    assert rec["sections"][0]["summary"] == "tóm tắt"
    assert rec["sections"][0]["chunk_refs"] == ["0"]
    assert saved and saved[0]["id"] == rec["id"]
    # done PHẢI atomic với result trong MỘT update (bài học race 2026-07-06)
    done_updates = [u for u in updates if u.get("status") == "done"]
    assert len(done_updates) == 1
    assert done_updates[0].get("result", {}).get("id") == rec["id"]


def test_cancel_before_summarize_stops_without_persist(tmp_path, monkeypatch):
    from app.domains.jobs import jobs_store as js
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.create_job("sj2", job_type="summary")
    js.request_cancel("sj2")
    saved = []
    g = _build(tmp_path, persist=saved.append)
    out = g.invoke({"job_id": "sj2", "source_names": ["a_docx"], "progress": 0,
                    "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sj2"}})
    assert out.get("cancelled") is True
    assert saved == []


def test_degraded_stages_flow_to_generator_missing(tmp_path):
    class DegradedPipeline:
        def sections(self, mm):
            return [{"id": "s1", "title": "Doc", "chunk_refs": ["0"], "order": 0}], "single"

        def summarize(self, mm, sections, length_mode="medium", progress_cb=None, cancel_cb=None):
            return ([{**s, "summary": "", "key_points": []} for s in sections], ["section:Doc"])

        def synthesize(self, sections, doc_title="", length_mode="medium"):
            return {"title": doc_title, "overview": "", "entities": []}, True

    g = _build(tmp_path, pipeline=DegradedPipeline())
    out = g.invoke({"job_id": "sj3", "source_names": ["a_docx"], "progress": 0,
                    "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sj3"}})
    gen = out["result"]["generator"]
    assert gen["degraded"] is True
    assert set(gen["missing"]) == {"skeleton", "section:Doc", "synthesize"}
    assert gen["skeleton_method"] == "single"


def test_pipeline_error_routes_to_error_handler(tmp_path):
    class BoomPipeline:
        def sections(self, mm):
            raise RuntimeError("boom")

    updates = []
    g = _build(tmp_path, pipeline=BoomPipeline(), jobs_updates=updates)
    out = g.invoke({"job_id": "sj4", "source_names": ["a_docx"], "progress": 0,
                    "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sj4"}})
    assert out.get("error")
    assert any(u.get("status") == "error" for u in updates)
