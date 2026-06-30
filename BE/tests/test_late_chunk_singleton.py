"""Singleton encoder dùng chung cho ingest (embed_document) và query (embed_query)
→ đảm bảo CÙNG model/pooling. Default model = bge-m3 (long-context, cần cho late
chunking), KHÔNG phải all-MiniLM (chỉ 512 token)."""
import importlib


def test_get_late_chunk_encoder_caches_and_defaults_bge_m3(monkeypatch):
    monkeypatch.delenv("EMBEDDING_MODEL_NAME", raising=False)
    lc = importlib.import_module("app.domains.ingest.late_chunk")
    lc._reset_encoder_singleton()  # test hook

    enc1 = lc.get_late_chunk_encoder()
    enc2 = lc.get_late_chunk_encoder()
    assert enc1 is enc2, "phải là singleton (cache)"
    assert enc1.model_name == "BAAI/bge-m3"


def test_get_late_chunk_encoder_respects_env_model(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")
    lc = importlib.import_module("app.domains.ingest.late_chunk")
    lc._reset_encoder_singleton()
    enc = lc.get_late_chunk_encoder()
    assert enc.model_name == "BAAI/bge-m3"
