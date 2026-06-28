"""
Implementation in-process của các Protocol trong shared/interfaces (Phase 1).

- LocalLLMProvider / LocalEmbeddingProvider: bọc llm_factory để dùng cho test và
  cho chế độ "chạy monolith không cần service". Khi tách llm-gateway (Phase 3),
  một GrpcLLMProvider/GrpcEmbeddingProvider khớp cùng Protocol sẽ thay thế.
- ProviderPool: gói thứ tự fallback provider thành object khởi tạo lúc-runtime
  (không phải lúc-import như llm_factory.PROVIDERS). Tái dùng builder của
  llm_factory (_ollama_chat_llm/_gemini_chat_llm/_groq_chat_llm/_invoke_chat) nên
  KHÔNG nhân đôi logic gọi LLM; chỉ vòng lặp fallback là của riêng pool.

Phase 1 cố ý KHÔNG đụng llm_factory.PROVIDERS (conftest reload dựa vào nó) —
module này chỉ thêm vào song song.
"""

from __future__ import annotations

from typing import Any, Iterator, List, Optional

import app.clients.llm_factory as _lf
from app.domains.vectorstore.embedding_utils import get_embedding_dim_safe
from shared.config import get_settings


class LocalEmbeddingProvider:
    """Khớp shared.interfaces.EmbeddingProvider, ủy quyền cho llm_factory."""

    def __init__(self, model_name: Optional[str] = None):
        self._model_name = model_name

    def encode(
        self,
        texts: list[str] | str,
        convert_to_numpy: bool = True,
        batch_size: int = 32,
        **kwargs: Any,
    ) -> Any:
        model = _lf.get_embedding_model(self._model_name)
        if model is None:
            raise RuntimeError("Embedding model not available (SKIP_MODEL_LOAD=1)")
        return model.encode(
            texts, convert_to_numpy=convert_to_numpy, batch_size=batch_size, **kwargs
        )

    def dim(self) -> int:
        return get_embedding_dim_safe(
            _lf.get_embedding_model, self._model_name, default_dim=384
        )


class ProviderPool:
    """Thứ tự fallback provider, cấu hình lúc-runtime (mục tiêu của llm-gateway).

    ask() lặp qua self.providers và tái dùng builder của llm_factory. set_providers()
    cho phép đổi thứ tự mà không cần restart (sẽ expose qua RPC SetProviders ở Phase 3).
    """

    def __init__(self, providers: Optional[List[str]] = None):
        self._providers = list(providers) if providers is not None else list(
            get_settings().providers
        )
        self._last_provider_used: Optional[str] = None

    @property
    def providers(self) -> List[str]:
        return list(self._providers)

    @property
    def last_provider_used(self) -> Optional[str]:
        return self._last_provider_used

    def set_providers(self, providers: List[str]) -> List[str]:
        self._providers = list(providers)
        self._last_provider_used = None
        return self.providers

    def ask(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        options: Optional[dict] = None,
        feature: str = "chat",
        timeout: Optional[float] = None,
    ) -> str:
        effective_model = model or _lf._model_map(feature)
        last_error: Optional[Exception] = None
        self._last_provider_used = None
        for provider in self._providers:
            try:
                if provider == "ollama":
                    llm = _lf._ollama_chat_llm(effective_model, feature, options)
                elif provider == "gemini":
                    llm = _lf._gemini_chat_llm(feature, options)
                elif provider == "groq":
                    llm = _lf._groq_chat_llm(feature, options)
                else:
                    continue
                out = _lf._invoke_chat(llm, prompt, system_prompt, timeout=timeout)
                self._last_provider_used = provider
                return out
            except Exception as e:  # noqa: BLE001 — giống ask_ai: thử provider kế tiếp
                last_error = e
                continue
        if not self._providers:
            raise RuntimeError(
                "No AI provider configured. Set OLLAMA_HOST, GEMINI_API_KEY hoặc GROQ_API_KEY."
            )
        raise RuntimeError(f"All AI providers failed (tried {self._providers}): {last_error}")


class LocalLLMProvider:
    """Khớp shared.interfaces.LLMProvider, ủy quyền cho ProviderPool / llm_factory."""

    def __init__(self, pool: Optional[ProviderPool] = None):
        self._pool = pool or ProviderPool()

    @property
    def pool(self) -> ProviderPool:
        return self._pool

    def ask(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        options: Optional[dict] = None,
        feature: str = "chat",
        timeout: Optional[float] = None,
    ) -> str:
        return self._pool.ask(
            prompt,
            system_prompt=system_prompt,
            model=model,
            options=options,
            feature=feature,
            timeout=timeout,
        )

    def ask_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        feature: str = "chat",
    ) -> Iterator[str]:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = _lf.get_llm(feature)
        msgs = (
            [SystemMessage(content=system_prompt), HumanMessage(content=prompt)]
            if system_prompt
            else [HumanMessage(content=prompt)]
        )
        yield from _lf.stream_chat_tokens(llm, msgs)
