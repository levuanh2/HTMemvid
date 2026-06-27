"""
Ghép BM25 + FAISS bằng LangChain EnsembleRetriever (thay RRF thủ công khi bật env).
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

from langchain.retrievers import EnsembleRetriever
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict, Field

from app.domains.retrieval.hybrid import HybridRetriever, RetrievedChunk

class _HybridSubRetriever(BaseRetriever):
    """Bridge HybridRetriever → LangChain retriever (một kênh BM25 hoặc FAISS)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    hybrid: Any = Field(...)
    mode: str = Field(..., description="bm25 hoặc faiss")
    selected_sources: Optional[List[str]] = None
    inner_k: int = 20

    def _get_relevant_documents(self, query: str) -> List[Document]:
        if self.mode == "bm25":
            chunks = self.hybrid.retrieve_bm25_only(query, selected_sources=self.selected_sources, top_k=self.inner_k)
        else:
            chunks = self.hybrid.retrieve_faiss_only(query, selected_sources=self.selected_sources, top_k=self.inner_k)
        out: List[Document] = []
        for c in chunks:
            md: dict = {"chunk_id": c.chunk_id, "video_stem": c.video_stem}
            if c.bm25_score is not None:
                md["bm25_score"] = c.bm25_score
            if c.vector_score is not None:
                md["vector_score"] = c.vector_score
            out.append(Document(page_content=c.text, metadata=md))
        return out


def hybrid_retrieve_with_ensemble(
    hybrid: HybridRetriever,
    query: str,
    *,
    selected_sources: Optional[List[str]] = None,
    top_k: int = 6,
    bm25_weight: Optional[float] = None,
    faiss_weight: Optional[float] = None,
) -> List[RetrievedChunk]:
    """
    Trả về danh sách RetrievedChunk sau khi EnsembleRetriever gộp hai kênh.
    """
    w_bm = float(bm25_weight if bm25_weight is not None else os.getenv("HYBRID_BM25_WEIGHT", "0.4"))
    w_f = float(faiss_weight if faiss_weight is not None else os.getenv("HYBRID_FAISS_WEIGHT", "0.6"))
    inner = max(20, top_k * 3)

    r_bm25 = _HybridSubRetriever(
        hybrid=hybrid, mode="bm25", selected_sources=selected_sources, inner_k=inner
    )
    r_faiss = _HybridSubRetriever(
        hybrid=hybrid, mode="faiss", selected_sources=selected_sources, inner_k=inner
    )
    ensemble = EnsembleRetriever(retrievers=[r_bm25, r_faiss], weights=[w_bm, w_f])
    docs = ensemble.invoke(query)
    if not isinstance(docs, list):
        docs = []

    seen: set[int] = set()
    ordered: List[RetrievedChunk] = []
    for d in docs:
        meta = d.metadata or {}
        cid = meta.get("chunk_id")
        if cid is None:
            continue
        cid = int(cid)
        if cid in seen:
            continue
        seen.add(cid)
        ordered.append(
            RetrievedChunk(
                chunk_id=cid,
                text=d.page_content or "",
                video_stem=str(meta.get("video_stem") or ""),
            )
        )
        if len(ordered) >= top_k:
            break
    return ordered
