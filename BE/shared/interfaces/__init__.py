"""
interfaces/ — các Protocol (PEP 544, structural typing) định nghĩa seam của hệ thống.

Mục tiêu "liên kết dẻo": code lõi phụ thuộc vào *interface*, còn implementation
(local in-process hay gRPC client) được *inject* lúc wiring. Dùng typing.Protocol
nên các class hiện có (LangChainEmbeddingAdapter, HybridRetriever, vector_store)
khớp interface mà KHÔNG cần kế thừa — đổi tối thiểu, không phá vỡ hành vi.
"""

from .llm import EmbeddingProvider, LLMProvider
from .retriever import RetrievedChunk, Retriever
from .vectorstore import VectorStore

__all__ = [
    "LLMProvider",
    "EmbeddingProvider",
    "VectorStore",
    "Retriever",
    "RetrievedChunk",
]
