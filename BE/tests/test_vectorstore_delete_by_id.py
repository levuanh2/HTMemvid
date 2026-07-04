from langchain.docstore.document import Document


def test_remove_chunks_from_lc_index_maps_chunk_id_to_docstore_id(monkeypatch):
    import app.domains.vectorstore.store as store

    deleted_ids = []

    class _VS:
        def __init__(self):
            self.docstore = type(
                "DS",
                (),
                {
                    "_dict": {
                        "uuid-a": Document(page_content="A", metadata={"chunk_id": 10, "video": "s1"}),
                        "uuid-b": Document(page_content="B", metadata={"chunk_id": 11, "video": "s1"}),
                        "uuid-c": Document(page_content="C", metadata={"chunk_id": 12, "video": "s2"}),
                    }
                },
            )()

        def delete(self, ids):
            deleted_ids.extend(ids)
            return True

        def save_local(self, path):
            pass

    monkeypatch.setattr(store, "load_vectorstore", lambda: _VS())
    monkeypatch.setattr(store, "_backup_dir_before_write", lambda *a, **k: None)
    monkeypatch.setattr(store, "_skip_faiss_in_ci", lambda: False)

    removed = store.remove_chunks_from_lc_index([10, 12])
    assert removed == 2
    assert deleted_ids == ["uuid-a", "uuid-c"]


def test_remove_chunks_from_raw_index_uses_remove_ids(monkeypatch):
    import numpy as np

    import app.domains.vectorstore.store as store

    removed_ids = []
    saved = []

    class _Index:
        def remove_ids(self, arr):
            removed_ids.extend(arr.tolist())
            return 2

    monkeypatch.setattr(store, "_skip_faiss_in_ci", lambda: False)
    monkeypatch.setattr(store.os.path, "exists", lambda path: path == store.INDEX_PATH)
    monkeypatch.setattr(store.faiss, "read_index", lambda path: _Index())
    monkeypatch.setattr(store, "save_index_with_backup", lambda idx, index_dir, keep=3: saved.append((idx, index_dir, keep)))

    removed = store.remove_chunks_from_raw_index([21, 22])

    assert removed == 2
    assert removed_ids == [21, 22]
    assert len(saved) == 1
