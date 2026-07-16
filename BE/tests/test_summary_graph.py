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

    _persist = persist or (lambda r: None)
    return build_summary_graph(
        data_dir=tmp_path, index_meta_path=meta_path,
        jobs_update=_jobs_update, collect_input=collect_input,
        pipeline=pipeline or StubPipeline(),
        # Phase D: persist_record now takes a user_id kwarg; absorb it for the stub.
        persist_record=lambda r, **_k: _persist(r),
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


def test_standard_mode_has_no_study_block(tmp_path):
    g = _build(tmp_path)
    out = g.invoke({"job_id": "sjm0", "source_names": ["a_docx"], "length_mode": "medium",
                    "progress": 0, "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sjm0"}})
    rec = out["result"]
    assert rec["mode"] == "standard"     # mode thiếu trong state → standard
    assert "study" not in rec


def test_study_mode_builds_study_block_from_facts_and_pointers(tmp_path):
    class FactsPipeline:
        def sections(self, mm):
            return [{"id": "s1", "title": "A", "chunk_refs": ["0"], "order": 0}], "headings"

        def summarize(self, mm, sections, length_mode="medium", progress_cb=None, cancel_cb=None):
            # study mode + facts present → section mang facts (mô phỏng SUMMARY_FACTS ON)
            return ([{**s, "summary": "tóm tắt", "key_points": ["ý"],
                      "facts": {"definitions": ["def A"], "important_terms": ["A"]}}
                     for s in sections], [])

        def synthesize(self, sections, doc_title="", length_mode="medium"):
            return {"title": doc_title, "overview": "ov", "entities": []}, False

    g = _build(tmp_path, pipeline=FactsPipeline())
    out = g.invoke({"job_id": "sjm1", "source_names": ["a_docx"], "length_mode": "medium",
                    "mode": "study", "progress": 0, "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sjm1"}})
    rec = out["result"]
    assert rec["mode"] == "study"
    assert rec["study"]["key_concepts"] == ["A"]
    assert rec["study"]["definitions"] == ["def A"]
    # pointer suy từ chunk_refs (chunk "0" có heading "1. A" trong collect_input) → review có mục
    assert rec["study"]["recommended_review"][0]["chunk_id"] == "0"


def test_dedup_removes_repeated_keypoints_and_facts_in_record(tmp_path):
    class DupPipeline:
        def sections(self, mm):
            return [{"id": "s1", "title": "A", "chunk_refs": ["0"], "order": 0}], "headings"

        def summarize(self, mm, sections, length_mode="medium", progress_cb=None, cancel_cb=None):
            # key_points + facts lặp (mô phỏng LLM trả trùng) → dedup phải tỉa
            return ([{**s, "summary": "tóm tắt", "key_points": ["ý chính", "ý chính"],
                      "facts": {"definitions": ["D", "d"], "important_terms": ["A", "a"]}}
                     for s in sections], [])

        def synthesize(self, sections, doc_title="", length_mode="medium"):
            return {"title": doc_title, "overview": "ov", "entities": []}, False

    g = _build(tmp_path, pipeline=DupPipeline())
    out = g.invoke({"job_id": "sjd1", "source_names": ["a_docx"], "length_mode": "medium",
                    "mode": "study", "progress": 0, "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sjd1"}})
    rec = out["result"]
    assert rec["sections"][0]["key_points"] == ["ý chính"]        # trùng tỉa
    assert rec["sections"][0]["facts"]["definitions"] == ["D"]
    assert rec["study"]["definitions"] == ["D"]                   # study cũng dedup
    assert rec["study"]["key_concepts"] == ["A"]


class _BasePipeline:
    """Minimal stub with sections/summarize/synthesize; coverage added per-test."""
    def sections(self, mm):
        return [{"id": "s1", "title": "A", "chunk_refs": ["0"], "order": 0}], "headings"

    def summarize(self, mm, sections, length_mode="medium", progress_cb=None, cancel_cb=None):
        return ([{**s, "summary": "tóm tắt", "key_points": ["ý"]} for s in sections], [])

    def synthesize(self, sections, doc_title="", length_mode="medium"):
        return {"title": doc_title, "overview": "tổng quan", "entities": []}, False


def test_graph_skips_coverage_when_pipeline_returns_none(tmp_path):
    # flag OFF is modelled by pipeline.coverage() -> None (real adapter returns None when off)
    class Pipe(_BasePipeline):
        def coverage(self, record):
            return None

    g = _build(tmp_path, pipeline=Pipe())
    out = g.invoke({"job_id": "sjc0", "source_names": ["a_docx"], "length_mode": "medium",
                    "progress": 0, "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sjc0"}})
    assert "coverage" not in out["result"]


def test_graph_includes_coverage_when_judge_returns_diagnostics(tmp_path):
    diag = {"covered": ["A"], "missing": ["B"], "unsupported": [], "vague": False, "notes": []}
    seen = {}

    class Pipe(_BasePipeline):
        def coverage(self, record):
            # judge runs on the final deduped draft; capture it to prove it isn't mutated
            seen["overview"] = record.get("overview")
            seen["section_summary"] = record["sections"][0]["summary"]
            return diag

    saved = []
    updates = []
    g = _build(tmp_path, pipeline=Pipe(), persist=saved.append, jobs_updates=updates)
    out = g.invoke({"job_id": "sjc1", "source_names": ["a_docx"], "length_mode": "medium",
                    "mode": "study", "progress": 0, "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sjc1"}})
    rec = out["result"]
    assert rec["coverage"] == diag
    # overview/sections/study text unchanged by the judge (judge-only, no rewrite)
    assert rec["overview"] == "tổng quan" == seen["overview"]
    assert rec["sections"][0]["summary"] == "tóm tắt" == seen["section_summary"]
    assert "study" in rec
    # persisted + done atomic with result (unchanged behaviour)
    assert saved and saved[0]["coverage"] == diag
    done = [u for u in updates if u.get("status") == "done"]
    assert len(done) == 1 and done[0]["result"]["coverage"] == diag


def test_graph_coverage_judge_failure_does_not_fail_summary(tmp_path):
    class Pipe(_BasePipeline):
        def coverage(self, record):
            raise RuntimeError("judge blew up")

    saved = []
    g = _build(tmp_path, pipeline=Pipe(), persist=saved.append)
    out = g.invoke({"job_id": "sjc2", "source_names": ["a_docx"], "length_mode": "medium",
                    "progress": 0, "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "sjc2"}})
    assert out.get("error") is None            # judge fault absorbed
    assert "coverage" not in out["result"]     # no diagnostics, but summary still done
    assert saved and saved[0]["id"] == out["result"]["id"]


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
