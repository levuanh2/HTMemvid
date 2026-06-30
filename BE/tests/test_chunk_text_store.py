import importlib
import json
import app.domains.vectorstore.store as store

def _setup(tmp_path):
    store.INDEX_DIR = tmp_path
    store.META_PATH = str(tmp_path / "index.json")
    store.INDEX_PATH = str(tmp_path / "index.faiss")
    # Force reload of chunk_text_store so it picks up the patched INDEX_DIR
    import app.domains.vectorstore.chunk_text_store as cts
    importlib.reload(cts)
    cts.reset_cache()
    return cts

def test_put_get_iter(tmp_path):
    cts = _setup(tmp_path)
    cts.put_many([(0, "alpha"), (1, "beta")])
    assert cts.get_text(0) == "alpha"
    assert cts.get_texts([0, 1]) == {0: "alpha", 1: "beta"}
    assert sorted(cts.iter_all()) == [(0, "alpha"), (1, "beta")]

def test_get_text_falls_back_to_inline_meta(tmp_path, monkeypatch):
    cts = _setup(tmp_path)  # sqlite is empty
    monkeypatch.setattr(store, "load_meta", lambda: {"5": {"text": "legacy"}})
    assert cts.get_text(5) == "legacy"

def test_get_text_decodes_video_when_no_sqlite_no_inline(tmp_path, monkeypatch):
    cts = _setup(tmp_path)
    monkeypatch.setattr(store, "load_meta", lambda: {"7": {"video": "d.mp4", "frame_index": 2}})
    import app.domains.ingest.video_utils as vu
    monkeypatch.setattr(vu, "decode_frame", lambda path, fi: "from_video" if (path == "d.mp4" and fi == 2) else None)
    assert cts.get_text(7) == "from_video"
    # test caching: change decode_frame, should still return cached value
    monkeypatch.setattr(vu, "decode_frame", lambda path, fi: None)
    assert cts.get_text(7) == "from_video"

def test_iter_all_uses_inline_when_sqlite_empty(tmp_path, monkeypatch):
    cts = _setup(tmp_path)
    monkeypatch.setattr(store, "load_meta", lambda: {"0": {"text": "x"}, "1": {"text": "y"}})
    assert sorted(cts.iter_all()) == [(0, "x"), (1, "y")]
