from __future__ import annotations

from typing import Any, Iterator, Optional

import grpc
import numpy as np

from shared.proto.gen import common_pb2, llm_pb2, llm_pb2_grpc


def _build_options(options: Optional[dict]) -> common_pb2.LlmOptions:
    data = options or {}
    return common_pb2.LlmOptions(
        num_predict=int(data.get("num_predict", 0) or 0),
        temperature=float(data.get("temperature", 0.0) or 0.0),
        num_ctx=int(data.get("num_ctx", 0) or 0),
    )


class GrpcLLMProvider:
    def __init__(self, addr: str):
        self._channel = grpc.insecure_channel(addr)
        self._stub = llm_pb2_grpc.LlmGatewayStub(self._channel)

    def ask(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        options: Optional[dict] = None,
        feature: str = "chat",
        timeout: Optional[float] = None,
    ) -> str:
        response = self._stub.Ask(
            llm_pb2.AskRequest(
                prompt=prompt,
                system_prompt=system_prompt or "",
                model=model or "",
                feature=feature,
                options=_build_options(options),
                timeout_sec=float(timeout) if timeout is not None else 0.0,
            ),
            timeout=timeout,
        )
        return response.text

    def ask_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        feature: str = "chat",
    ) -> Iterator[str]:
        response = self._stub.AskStream(
            llm_pb2.AskRequest(
                prompt=prompt,
                system_prompt=system_prompt or "",
                feature=feature,
            )
        )
        for token in response:
            if token.text:
                yield token.text


class GrpcEmbeddingProvider:
    def __init__(self, addr: str, model_name: Optional[str] = None):
        self._channel = grpc.insecure_channel(addr)
        self._stub = llm_pb2_grpc.LlmGatewayStub(self._channel)
        self._model_name = model_name

    def encode(
        self,
        texts: list[str] | str,
        convert_to_numpy: bool = True,
        batch_size: int = 32,
        **kwargs: Any,
    ) -> Any:
        del batch_size, kwargs
        text_list = [texts] if isinstance(texts, str) else list(texts)
        response = self._stub.Embed(
            llm_pb2.EmbedRequest(texts=text_list, model_name=self._model_name or "")
        )
        arr = np.array([vec.values for vec in response.vectors], dtype=np.float32)
        return arr if convert_to_numpy else arr.tolist()

    def dim(self) -> int:
        response = self._stub.Embed(
            llm_pb2.EmbedRequest(texts=[""], model_name=self._model_name or "")
        )
        return int(response.dim)
