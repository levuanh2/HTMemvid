from __future__ import annotations

import os
from concurrent import futures
from typing import Iterator, Optional

import grpc

import app.clients.llm_factory as llm_factory
from app.clients.local_providers import ProviderPool
from shared.config import get_settings
from shared.proto.gen import common_pb2, llm_pb2, llm_pb2_grpc

_OFFLINE_EMBED_DIM = 384


def _options_to_dict(options: common_pb2.LlmOptions) -> Optional[dict]:
    out: dict[str, float | int] = {}
    if options.num_predict:
        out["num_predict"] = options.num_predict
    if options.temperature:
        out["temperature"] = options.temperature
    if options.num_ctx:
        out["num_ctx"] = options.num_ctx
    return out or None


class LlmGatewayService(llm_pb2_grpc.LlmGatewayServicer):
    def __init__(self, pool: Optional[ProviderPool] = None):
        self._pool = pool or ProviderPool()

    def Ask(self, request: llm_pb2.AskRequest, context) -> llm_pb2.AskResponse:
        text = self._pool.ask(
            request.prompt,
            system_prompt=request.system_prompt or None,
            model=request.model or None,
            options=_options_to_dict(request.options),
            feature=request.feature or "chat",
            timeout=request.timeout_sec or None,
        )
        return llm_pb2.AskResponse(
            text=text,
            provider_used=self._pool.last_provider_used or "",
        )

    def AskStream(
        self, request: llm_pb2.AskRequest, context
    ) -> Iterator[llm_pb2.Token]:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = llm_factory.get_llm(request.feature or "chat")
        messages = (
            [SystemMessage(content=request.system_prompt), HumanMessage(content=request.prompt)]
            if request.system_prompt
            else [HumanMessage(content=request.prompt)]
        )
        for piece in llm_factory.stream_chat_tokens(llm, messages):
            yield llm_pb2.Token(text=piece)

    def Embed(self, request: llm_pb2.EmbedRequest, context) -> llm_pb2.EmbedResponse:
        texts = list(request.texts)
        model_name = request.model_name or None
        if os.getenv("SKIP_MODEL_LOAD") == "1":
            vectors = [
                llm_pb2.FloatVector(values=[0.0] * _OFFLINE_EMBED_DIM) for _ in texts
            ]
            return llm_pb2.EmbedResponse(vectors=vectors, dim=_OFFLINE_EMBED_DIM)

        model = llm_factory.get_embedding_model(model_name)
        if model is None:
            vectors = [
                llm_pb2.FloatVector(values=[0.0] * _OFFLINE_EMBED_DIM) for _ in texts
            ]
            return llm_pb2.EmbedResponse(vectors=vectors, dim=_OFFLINE_EMBED_DIM)

        encoded = model.encode(texts, convert_to_numpy=True)
        vectors = [
            llm_pb2.FloatVector(values=row.astype("float32").tolist()) for row in encoded
        ]
        dim = int(encoded.shape[1]) if len(texts) else 0
        return llm_pb2.EmbedResponse(vectors=vectors, dim=dim)

    def GetProviders(
        self, request: common_pb2.Empty, context
    ) -> llm_pb2.ProvidersResponse:
        return llm_pb2.ProvidersResponse(
            providers=self._pool.providers,
            embedding_model=get_settings().embedding_model_name,
        )

    def SetProviders(
        self, request: llm_pb2.ProvidersResponse, context
    ) -> llm_pb2.ProvidersResponse:
        providers = self._pool.set_providers(list(request.providers))
        embedding_model = request.embedding_model or get_settings().embedding_model_name
        return llm_pb2.ProvidersResponse(
            providers=providers,
            embedding_model=embedding_model,
        )


def serve() -> grpc.Server:
    port = int((os.getenv("LLM_GATEWAY_PORT") or "50051").strip() or "50051")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    llm_pb2_grpc.add_LlmGatewayServicer_to_server(LlmGatewayService(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    return server


if __name__ == "__main__":
    serve().wait_for_termination()
