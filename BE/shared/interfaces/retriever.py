"""
Seam cho Retriever (hybrid FAISS + BM25).

RetrievedChunk ở đây phản chiếu dataclass cùng tên trong retrieval/hybrid.py
(structural — HybridRetriever đã khớp Retriever mà không cần kế thừa). Mục tiêu:
inject Retriever vào build_query_graph thay vì khởi tạo HybridRetriever bên trong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    text: str
    video_stem: str
    bm25_score: Optional[float] = None
    vector_score: Optional[float] = None


@runtime_checkable
class Retriever(Protocol):
    def retrieve(
        self,
        query: str,
        *,
        selected_sources: Optional[List[str]] = None,
        top_k: int = 6,
    ) -> List[RetrievedChunk]:
        """Trả về danh sách chunk liên quan nhất (đã merge RRF)."""
        ...
