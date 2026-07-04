def test_delete_chunks_by_source_lc_keeps_remaining_vectors_and_no_rebuild_log(
    monkeypatch, capsys, tmp_path
):
    import os

    from langchain_core.embeddings import Embeddings

    os.environ["USE_LC_VECTOR_STORE"] = "1"
    os.environ["SKIP_MODEL_LOAD"] = "1"

    import app.domains.vectorstore.store as store

    class FakeEmb(Embeddings):
        def embed_documents(self, texts):
            return [[float(i + 1), 0.0, 0.0] for i, _ in enumerate(texts)]

        def embed_query(self, text):
            return [1.0, 0.0, 0.0]

    monkeypatch.setattr(store, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(store, "INDEX_PATH", str(tmp_path / "index.faiss"))
    monkeypatch.setattr(store, "META_PATH", str(tmp_path / "index.json"))
    monkeypatch.setattr(store, "get_embeddings", lambda: FakeEmb())
    monkeypatch.setattr(store, "_skip_faiss_in_ci", lambda: False)

    chunks = ["alpha s1", "beta s1", "gamma s2", "delta s2"]
    metas = [
        {"source_id": "s1", "video": "s1"},
        {"source_id": "s1", "video": "s1"},
        {"source_id": "s2", "video": "s2"},
        {"source_id": "s2", "video": "s2"},
    ]
    store.append_chunks_to_lc_index(chunks, custom_metadata=metas)

    deleted = store.delete_chunks_by_source("s1")
    assert deleted == 2

    meta = store._load_meta()
    data_ids = sorted(int(k) for k in meta.keys() if k.isdigit())
    assert len(data_ids) == 2

    vs = store.load_vectorstore()
    assert vs is not None
    assert len(getattr(vs.docstore, "_dict", {})) == 2

    out = capsys.readouterr().out
    assert "rebuilt LC FAISS" not in out

    hits = store.similarity_search_lc("gamma", k=2)
    assert any("gamma s2" in h for h in hits)
