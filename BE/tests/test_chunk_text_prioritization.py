import pytest
from services.mindmap.worker import collect_chunks_for_sources
from app.domains.memory.tree import _join_chunk_text, build_human_context
from app.domains.vectorstore import chunk_text_store

def test_collect_chunks_for_sources_prioritizes_store(monkeypatch):
    # Mock chunk_text_store.get_text to return specific text
    def mock_get_text(chunk_id):
        if chunk_id == 0:
            return "text from sqlite store"
        return None
    monkeypatch.setattr(chunk_text_store, "get_text", mock_get_text)

    # Metadata entry has an inline 'text' as well
    meta = {
        "0": {
            "source_stem": "test_doc",
            "video": "abc.mp4",
            "text": "old inline metadata text",
            "embedding": [0.1, 0.2]
        }
    }

    # Retrieve chunks
    chunks = collect_chunks_for_sources(meta, ["test_doc"])
    assert len(chunks) == 1
    # Verify it prioritizes the text from the store (sqlite)
    assert chunks[0]["text"] == "text from sqlite store"


def test_join_chunk_text_prioritizes_store(monkeypatch):
    # Mock chunk_text_store.get_text
    def mock_get_text(chunk_id):
        if chunk_id == 42:
            return "text from sqlite store for join"
        return None
    monkeypatch.setattr(chunk_text_store, "get_text", mock_get_text)

    chunks = [
        {
            "chunk_id": 42,
            "text": "old inline text"
        }
    ]

    joined = _join_chunk_text(chunks)
    assert joined == "text from sqlite store for join"


def test_build_human_context_prioritizes_store(monkeypatch):
    # Mock chunk_text_store.get_text
    def mock_get_text(chunk_id):
        if chunk_id == 99:
            return "text from sqlite store for context"
        return None
    monkeypatch.setattr(chunk_text_store, "get_text", mock_get_text)

    top_nodes = [{"summary": "Node summary"}]
    evidence_chunks = [
        {
            "chunk_id": 99,
            "text": "old inline text for evidence"
        }
    ]

    context = build_human_context(top_nodes, evidence_chunks)
    assert "text from sqlite store for context" in context
    assert "old inline text for evidence" not in context
