"""
Seam cho LLM và Embedding.

- LLMProvider.ask  : mirror chữ ký llm_factory.ask_ai() (provider fallback ẩn bên trong).
- LLMProvider.ask_stream : token streaming (thay qa_chain stream / AskStream gRPC).
- EmbeddingProvider.encode : tương thích LangChainEmbeddingAdapter.encode() hiện có.

Hai impl sẽ khớp interface này:
  1) Local*  — bọc llm_factory (in-process), dùng cho test/chạy không cần service.
  2) Grpc*   — client tới llm-gateway (Phase 3).
"""

from __future__ import annotations

from typing import Any, Iterator, Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    def ask(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        options: Optional[dict] = None,
        feature: str = "chat",
        timeout: Optional[float] = None,
    ) -> str:
        """Gọi LLM, trả về text. feature ∈ {'chat','summary','mindmap'}."""
        ...

    def ask_stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        feature: str = "chat",
    ) -> Iterator[str]:
        """Stream từng token/đoạn text."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    def encode(
        self,
        texts: list[str] | str,
        convert_to_numpy: bool = True,
        batch_size: int = 32,
        **kwargs: Any,
    ) -> Any:
        """Encode text -> vector(s). Trả np.ndarray khi convert_to_numpy=True."""
        ...

    def dim(self) -> int:
        """Số chiều embedding (vd 384 cho all-MiniLM-L6-v2)."""
        ...
