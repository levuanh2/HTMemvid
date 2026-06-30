"""Query/global embedding phải dùng CÙNG encoder mean-pool như chunk (late chunking)
→ cosine query↔chunk có nghĩa. Kiểm tra wiring (lazy, KHÔNG nạp model thật)."""


def _fresh(monkeypatch, **env):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_ADDR", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import shared.config as cfg
    cfg.reload()
    import app.clients.llm_factory as lf
    lf.clear_embeddings_cache()
    return lf


def test_late_chunking_enabled_flag(monkeypatch):
    lf = _fresh(monkeypatch, LATE_CHUNKING="1")
    assert lf._late_chunking_enabled() is True
    lf2 = _fresh(monkeypatch, LATE_CHUNKING="0")
    assert lf2._late_chunking_enabled() is False


def test_get_embedding_model_is_late_chunk_encoder(monkeypatch):
    lf = _fresh(monkeypatch, LATE_CHUNKING="1")
    from app.domains.ingest.late_chunk import LateChunkEncoder

    m = lf.get_embedding_model()  # lazy: chỉ khởi tạo, không nạp model
    assert isinstance(m, LateChunkEncoder)


def test_get_embeddings_is_late_chunk_wrapper(monkeypatch):
    lf = _fresh(monkeypatch, LATE_CHUNKING="1")
    emb = lf.get_embeddings()
    assert emb.__class__.__name__ == "LateChunkEmbeddings"
    # langchain Embeddings interface
    assert hasattr(emb, "embed_query") and hasattr(emb, "embed_documents")
