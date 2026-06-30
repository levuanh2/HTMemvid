import json
import app.domains.vectorstore.store as store
import app.domains.vectorstore.chunk_text_store as cts

def test_bm25_corpus_from_sqlite_not_index_text(tmp_path, monkeypatch):
    store.INDEX_DIR = tmp_path
    store.META_PATH = str(tmp_path / "index.json")
    cts.reset_cache()
    # index.json: pointer, KHÔNG text
    meta = {"0": {"video": "d.mp4", "frame_index": 0, "source_stem": "d"},
            "1": {"video": "d.mp4", "frame_index": 1, "source_stem": "d"}, "__meta__": {}}
    with open(store.META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    cts.put_many([(0, "alpha lexical token"), (1, "beta other token")])

    from app.domains.retrieval.hybrid import HybridRetriever
    from pathlib import Path
    r = HybridRetriever(index_path=Path(store.INDEX_PATH), meta_path=Path(store.META_PATH))
    r._ensure_loaded()
    assert {c.chunk_id for c in r._chunks} == {0, 1}
    assert any("alpha" in c.text for c in r._chunks), "text phải đến từ sqlite"
    assert r._bm25 is not None

