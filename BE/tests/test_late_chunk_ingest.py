"""Integration: vector late-chunk tính ở chunk_node phải chảy xuống EmbedAndIndex,
map đúng theo chunk_index (kể cả entry sub-split dùng chung vector chunk cha)."""
from pathlib import Path

import numpy as np


def test_late_embeddings_flow_to_append(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "0")  # cho phép nhánh late chunking chạy
    monkeypatch.setenv("USE_MARKDOWN_INGEST", "1")
    monkeypatch.setenv("CHUNK_STRATEGY", "markdown_header")
    monkeypatch.setenv("CONTEXTUAL_EMBEDDINGS", "0")
    monkeypatch.setenv("HYPO_QA", "0")
    monkeypatch.setenv("MD_DIR", str(tmp_path / "md"))
    import shared.config as cfg
    cfg.reload()

    # Fake encoder: vector của span i = [i,i,i,i] → dễ kiểm tra mapping.
    class FakeEnc:
        def warmup(self):
            pass

        def embed_document(self, text, spans):
            return np.array([[float(i)] * 4 for i in range(len(spans))], dtype="float32")

        def embed_query(self, t):
            return np.array([[-1.0, -1.0, -1.0, -1.0]], dtype="float32")

    import app.domains.ingest.late_chunk as lc
    monkeypatch.setattr(lc, "get_late_chunk_encoder", lambda *a, **k: FakeEnc())

    captured = {}

    def fake_process(chunks, video_name, timestamp):
        # chunk 0 → 2 entry sub-split; các chunk khác → 1 entry. Tất cả mang chunk_index.
        entries = []
        for i, c in enumerate(chunks):
            n = 2 if i == 0 else 1
            for s in range(n):
                entries.append({
                    "text": c, "video": video_name, "timestamp": timestamp,
                    "parent_id": None, "sub_order": s, "total_parts": n,
                    "is_subchunk": n > 1, "chunk_index": i,
                })
        return ("fake_video.mp4", entries)

    def fake_append(chunks, video_name, custom_metadata=None, batch_size=32, embeddings=None):
        captured["chunks"] = chunks
        captured["embeddings"] = embeddings
        captured["custom_metadata"] = custom_metadata

    from app.graphs.ingest_graph import build_ingest_graph

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
    embs = captured.get("embeddings")
    assert embs is not None, "embed_index phải truyền embeddings precomputed (late chunking)"
    cm = captured["custom_metadata"]
    assert embs.shape == (len(cm), 4)
    # mỗi entry lấy đúng vector theo chunk_index (sub-chunk chia sẻ vector chunk cha)
    entries_ci = [m for m in cm]  # custom_metadata không có chunk_index, kiểm qua chunks order
    # entry 0 và 1 đều là chunk 0 → vector [0,0,0,0]; phải bằng nhau
    assert np.allclose(embs[0], embs[1]), "2 sub-chunk của chunk 0 dùng chung vector"
    assert np.allclose(embs[0], np.zeros(4)), "chunk 0 → span index 0 → vector 0"
