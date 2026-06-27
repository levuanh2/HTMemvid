from __future__ import annotations

from shared.config import reload
from shared.proto.gen import common_pb2, llm_pb2
from services.llm_gateway.server import LlmGatewayService


class _DummyContext:
    pass


def test_llm_gateway_embed_and_get_providers_offline(monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    reload()

    servicer = LlmGatewayService()
    context = _DummyContext()

    embed_response = servicer.Embed(
        llm_pb2.EmbedRequest(texts=["alpha", "beta"]),
        context,
    )
    providers_response = servicer.GetProviders(common_pb2.Empty(), context)

    assert embed_response.dim == 384
    assert len(embed_response.vectors) == 2
    assert all(len(vector.values) == 384 for vector in embed_response.vectors)
    assert providers_response.providers is not None
