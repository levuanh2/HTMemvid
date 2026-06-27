from __future__ import annotations

from app.clients.llm_client import GrpcEmbeddingProvider, GrpcLLMProvider
from app.clients.local_providers import LocalEmbeddingProvider, LocalLLMProvider
from shared.config import get_settings


def get_llm_provider():
    addr = get_settings().llm_gateway_addr
    if addr:
        return GrpcLLMProvider(addr)
    return LocalLLMProvider()


def get_embedding_provider():
    addr = get_settings().llm_gateway_addr
    if addr:
        return GrpcEmbeddingProvider(addr)
    return LocalEmbeddingProvider()
