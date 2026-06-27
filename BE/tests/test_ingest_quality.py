"""D4: e2e ingest graph với Normalize+Enrich (stub các callback nặng)."""
from pathlib import Path


def test_ingest_enriched_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    monkeypatch.setenv("USE_MARKDOWN_INGEST", "1")
    monkeypatch.setenv("CHUNK_STRATEGY", "markdown_header")
    monkeypatch.setenv("ENRICH_METADATA", "1")
    monkeypatch.setenv("CONTEXTUAL_EMBEDDINGS", "0")
    monkeypatch.setenv("HYPO_QA", "0")
    monkeypatch.setenv("DOC_CATEGORY", "yte")
    monkeypatch.setenv("MD_DIR", str(tmp_path / "md"))
    import shared.config as cfg
    cfg.reload()

    from app.graphs.ingest_graph import build_ingest_graph

    captured = {}

    def fake_process(chunks, video_name, timestamp):
        entries = [
            {"text": c, "video": video_name, "timestamp": timestamp,
             "parent_id": None, "sub_order": None, "total_parts": None, "is_subchunk": False}
            for c in chunks
        ]
        return ("fake_video.mp4", entries)

    def fake_append(chunks, video_name, custom_metadata=None, batch_size=32):
        captured["chunks"] = chunks
        captured["custom_metadata"] = custom_metadata

    g = build_ingest_graph(
        update_source_status=lambda *a, **k: None,
        data_dir=tmp_path,
        extract_text=lambda p: Path(p).read_text(encoding="utf-8"),
        split_text=lambda t: [t],
        process_and_store_chunks=fake_process,
        append_to_index=fake_append,
        build_memory_tree_for_sources=lambda srcs: None,
        jobs_update=None,
    )

    f = tmp_path / "doc.md"
    f.write_text(
        "# Tiêu đề\n\n## Mục A\n\nNội dung mục A đủ dài để tạo chunk.\n\n## Mục B\n\nNội dung mục B.\n",
        encoding="utf-8",
    )
    state = {
        "job_id": "j1", "source_id": "s1", "file_path": str(f), "filename": "doc.md",
        "progress": 0, "current_node": "", "artifacts": {}, "error": None,
    }
    out = g.invoke(state, config={"configurable": {"thread_id": "j1"}})

    assert out.get("error") is None, out.get("error")
    cm = captured.get("custom_metadata")
    assert cm, "append_to_index phải nhận custom_metadata"
    # doc-level metadata áp cho mọi chunk
    assert all(m.get("category") == "yte" for m in cm)
    assert all(m.get("source") == "doc.md" for m in cm)
    assert all(m.get("language") for m in cm)
    assert all("date" in m for m in cm)
    # heading_path theo cấu trúc (Mục A/B) có mặt
    assert any("Mục" in (m.get("heading_path") or "") for m in cm)

    # .md artifact được lưu
    assert (tmp_path / "md").exists()

    cfg.reload()  # khôi phục settings cho test khác
