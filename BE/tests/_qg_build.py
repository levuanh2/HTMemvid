"""Helper dựng query graph thật (không mock) với callable stub — cho test CRAG/Supervisor/HITL."""

from __future__ import annotations

import tempfile
from pathlib import Path

import shared.config as cfg
from app.graphs.query_graph import build_query_graph


class StubChunk:
    def __init__(self, text: str, stem: str = "doc", cid: int = 1):
        self.text = text
        self.video_stem = stem
        self.chunk_id = cid
        self.vector_score = None
        self.bm25_score = None


class StubRetriever:
    def __init__(self, chunks):
        self._chunks = chunks

    def retrieve(self, q, **kwargs):
        return list(self._chunks)


def base_env(monkeypatch, **flags):
    """Set env an toàn để test đi nhánh stub (không gọi LLM/ensemble thật) + reload config."""
    monkeypatch.setenv("USE_LC_QA_CHAIN", "0")      # generate dùng summarize_results stub
    monkeypatch.setenv("USE_LC_ENSEMBLE", "0")      # retrieve gọi retriever.retrieve trực tiếp
    monkeypatch.setenv("QUERY_STREAM_TOKENS", "0")
    monkeypatch.setenv("EVAL_ENABLED", "false")
    # Mặc định KHÔNG tải model thật trong unit test: rerank/nli warmup() gọi
    # get_reranker()/get_nli() → nếu không có SKIP_MODEL_LOAD sẽ kéo CrossEncoder/
    # mDeBERTa thật. Test nào cần engine thật (warmup) sẽ tự bật lại "0".
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    for k, v in flags.items():
        monkeypatch.setenv(k, v)
    cfg.reload()


def build(*, retriever_chunks=None, summarize=None, **overrides):
    """Trả về (graph, cache_dict)."""
    d = Path(tempfile.mkdtemp())
    cache: dict = {}
    chunks = retriever_chunks if retriever_chunks is not None else [StubChunk("python testing fixtures relevant")]
    kwargs = dict(
        data_dir=d,
        index_meta_path=d / "index.json",
        jobs_update=None,
        make_cache_key=lambda q, s, m, f=None, cs="public": f"ck::{cs}::{q}",
        get_cached=lambda k: cache.get(k),
        set_cached=lambda k, v: cache.__setitem__(k, v),
        check_sources_status=lambda s: {},
        get_source_status_by_stem=lambda s: None,
        search_index=lambda q: [],
        summarize_results=summarize or (lambda *a, **k: "generated answer"),
        query_with_memory_tree=lambda *a, **k: None,
        get_session_history=None,
        retriever=StubRetriever(chunks),
    )
    kwargs.update(overrides)
    return build_query_graph(**kwargs), cache


def run(g, state, thread_id: str = "t"):
    """invoke với thread_id (graph compile kèm checkpointer nên bắt buộc)."""
    return g.invoke(state, config={"configurable": {"thread_id": thread_id}})


def init_state(q: str, **over) -> dict:
    s = {
        "job_id": "j1",
        "session_id": "",
        "conversation_history": [],
        "q": q,
        "selected_sources": [],
        "use_memory_tree": False,
        "category": None,
        "language": None,
        "retrieved_chunks": [],
        "retrieved_sources": [],
        "context": "",
        "answer": "",
        "retry_count": 0,
        "low_confidence": False,
        "progress": 0,
        "current_node": "Queued",
        "error": None,
    }
    s.update(over)
    return s
