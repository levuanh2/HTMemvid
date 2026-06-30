"""Late chunking cần map mỗi metadata_entry (có thể bị sub-split) về CHUNK gốc để
lấy đúng vector late-chunk. process_and_store_chunks phải gắn `chunk_index` (chỉ số
chunk nguồn) lên mọi entry — kể cả sub-chunk của chunk dài."""
import app.domains.ingest.chunk_processor as cp


def test_entries_carry_source_chunk_index(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "save_qr_frames_to_video", lambda frames, prefix="": str(tmp_path / "v.mp4"))
    import app.domains.vectorstore.store as store
    monkeypatch.setattr(store, "_load_meta", lambda: {})

    short_chunk = "Đoạn ngắn gọn."
    long_chunk = ("Câu dài lặp lại. " * 400).strip()  # > MAX_QR_CHARS → bị sub-split
    chunks = [short_chunk, long_chunk]

    _video, entries = cp.process_and_store_chunks(chunks, video_name="doc.mp4", timestamp="2026-06-30T00:00:00")

    assert entries
    for e in entries:
        assert "chunk_index" in e, "mỗi entry phải mang chunk_index"
        assert e["chunk_index"] in (0, 1)
    # chunk dài (index 1) phải sinh nhiều entry sub-chunk
    long_entries = [e for e in entries if e["chunk_index"] == 1]
    assert len(long_entries) > 1
    assert {e["chunk_index"] for e in entries} == {0, 1}


def test_video_failure_is_non_fatal(monkeypatch):
    """Video QR là lưu trữ phụ — ghi hỏng (headless thiếu codec) KHÔNG được chặn indexing.
    process_and_store_chunks phải trả entries + video_path rỗng thay vì ném."""
    def _boom(frames, prefix=""):
        raise RuntimeError("no working codec")

    monkeypatch.setattr(cp, "save_qr_frames_to_video", _boom)
    import app.domains.vectorstore.store as store
    monkeypatch.setattr(store, "_load_meta", lambda: {})

    vp, entries = cp.process_and_store_chunks(
        ["đoạn một", "đoạn hai"], video_name="doc.mp4", timestamp="2026-06-30T00:00:00"
    )
    assert entries, "entries (để index) phải còn nguyên dù video lỗi"
    assert vp == "", "video lỗi → path rỗng, không chặn pipeline"


def test_entries_get_frame_index_after_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "save_qr_frames_to_video", lambda frames, prefix="": str(tmp_path / "v.mp4"))
    import app.domains.vectorstore.store as store
    monkeypatch.setattr(store, "_load_meta", lambda: {})
    _v, entries = cp.process_and_store_chunks(["a", "b", "c"], "doc.mp4", "2026-06-30T00:00:00")
    assert [e["frame_index"] for e in entries] == list(range(len(entries)))

