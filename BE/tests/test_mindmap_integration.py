# BE/tests/test_mindmap_integration.py — real graph -> real pipeline -> real store.
#
# Gap being closed: existing tests either stub the pipeline (test_mindmap_graph.py)
# or stub the graph/store (test_mindmap_routes.py). Nothing exercises the REAL
# LocalMindmapPipeline (services/mindmap/pipeline/{skeleton,enrich,relations}.py)
# wired through the REAL build_mindmap_graph into the REAL sqlite store
# (app/domains/mindmap/store.py). SKIP_MODEL_LOAD=1 keeps it fast/deterministic
# (no LLM call) while still exercising every non-LLM layer end to end.
from __future__ import annotations

import json

from app.clients.mindmap_factory import LocalMindmapPipeline
from app.domains.mindmap import input_collector, store as mindmap_store
from app.domains.vectorstore import chunk_text_store
from app.graphs.mindmap_graph import build_mindmap_graph


def test_route_graph_store_real_pipeline_persists_v2_record(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mindmaps.sqlite"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: f"nội dung đoạn {cid}")

    index_meta_path = tmp_path / "index.json"
    index_meta_path.write_text(
        json.dumps(
            {
                "0": {"source_stem": "bao_cao_docx", "heading_path": "1. Mở đầu"},
                "1": {"source_stem": "bao_cao_docx", "heading_path": "2. Kết luận"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    graph = build_mindmap_graph(
        data_dir=tmp_path,
        index_meta_path=index_meta_path,
        jobs_update=None,
        collect_input=input_collector.collect_mindmap_input,
        pipeline=LocalMindmapPipeline(),
        persist_record=mindmap_store.save_record,
    )

    out = graph.invoke(
        {
            "job_id": "it-1",
            "source_names": ["bao_cao_docx"],
            "progress": 0,
            "current_node": "",
            "error": None,
        },
        config={"configurable": {"thread_id": "it-1"}},
    )

    assert out.get("error") is None
    assert out.get("cancelled") is not True
    record = out["result"]

    # Persisted for real in sqlite — not just returned in-memory.
    persisted = mindmap_store.get_by_hash(record["content_hash"])
    assert persisted is not None
    assert persisted["id"] == record["id"]

    assert record["schema_version"] == 2
    assert record["nodes"], "skeleton nodes must survive sanitize + persistence"
    root = next(n for n in record["nodes"] if n["kind"] == "root")
    sections = [n for n in record["nodes"] if n["kind"] == "section"]
    assert root is not None
    assert {n["title"] for n in sections} == {"1. Mở đầu", "2. Kết luận"}

    # SKIP_MODEL_LOAD path: enrich returns skeleton unchanged (not degraded),
    # relations is skipped outright (also not degraded) — see enrich.py/relations.py.
    assert record["generator"]["degraded"] is False
    assert record["relations"] == []
