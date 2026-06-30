from __future__ import annotations

import os
import threading
from typing import Any, Callable, Iterable

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer


def l2_normalize(arr: np.ndarray) -> np.ndarray:
    """Chuan hoa L2 theo hang, co bao ve cho vector 0."""
    values = np.asarray(arr, dtype=np.float32)
    if values.ndim == 1:
        norm = np.linalg.norm(values)
        if norm <= 1e-12:
            return values.copy()
        return values / norm
    if values.ndim != 2:
        raise ValueError("arr must be 1D or 2D")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    safe = np.where(norms > 1e-12, norms, 1.0)
    return values / safe


def char_span_to_token_span(
    offsets: np.ndarray, char_start: int, char_end: int
) -> tuple[int, int] | None:
    """Map span ky tu sang span token [start, end)."""
    if char_end <= char_start:
        return None
    token_offsets = np.asarray(offsets, dtype=np.int64)
    if token_offsets.size == 0:
        return None

    tok_start: int | None = None
    for idx, (_, end) in enumerate(token_offsets):
        if int(end) > char_start:
            tok_start = idx
            break
    if tok_start is None:
        return None

    tok_end = len(token_offsets)
    for idx, (start, _) in enumerate(token_offsets):
        if idx < tok_start:
            continue
        if int(start) >= char_end:
            tok_end = idx
            break

    if tok_start >= tok_end:
        return None
    return tok_start, tok_end


def accumulate_token_embeddings(
    n_tokens: int,
    hidden: int,
    forward_fn: Callable[[int, int], np.ndarray],
    max_length: int,
    window_overlap: int,
) -> np.ndarray:
    """Gop embedding tu nhieu cua so token, average vung overlap."""
    if window_overlap >= max_length:
        raise ValueError("window_overlap must be smaller than max_length")
    if n_tokens <= 0:
        return np.zeros((0, hidden), dtype=np.float32)

    stride = max_length - window_overlap
    sums = np.zeros((n_tokens, hidden), dtype=np.float32)
    counts = np.zeros((n_tokens, 1), dtype=np.float32)

    start = 0
    while start < n_tokens:
        end = min(start + max_length, n_tokens)
        window = np.asarray(forward_fn(start, end), dtype=np.float32)
        if window.shape != (end - start, hidden):
            raise ValueError("forward_fn returned unexpected shape")
        sums[start:end] += window
        counts[start:end] += 1.0
        if end >= n_tokens:
            break
        start += stride

    counts = np.where(counts > 0.0, counts, 1.0)
    return sums / counts


def pool_spans(
    token_embs: np.ndarray,
    offsets: np.ndarray,
    spans: Iterable[tuple[int, int]],
    fallback_fn: Callable[[tuple[int, int]], np.ndarray] | None = None,
) -> np.ndarray:
    """Mean-pool token embedding theo span ky tu, co fallback khi span rong."""
    token_values = np.asarray(token_embs, dtype=np.float32)
    token_offsets = np.asarray(offsets, dtype=np.int64)
    hidden = int(token_values.shape[1]) if token_values.ndim == 2 else 0

    pooled: list[np.ndarray] = []
    for span in list(spans):
        token_span = char_span_to_token_span(token_offsets, span[0], span[1])
        if token_span is not None:
            start, end = token_span
            vec = token_values[start:end].mean(axis=0, dtype=np.float32)
        elif fallback_fn is not None:
            vec = np.asarray(fallback_fn(span), dtype=np.float32).reshape(hidden)
        else:
            vec = np.zeros(hidden, dtype=np.float32)
        pooled.append(l2_normalize(vec))

    if not pooled:
        return np.zeros((0, hidden), dtype=np.float32)
    return np.stack(pooled, axis=0).astype(np.float32, copy=False)


class LateChunkEncoder:
    _shared_lock = threading.Lock()
    _shared_backends: dict[tuple[str, str], tuple[Any, Any, int]] = {}

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        device: str = "cpu",
        max_length: int = 8192,
        window_overlap: int = 256,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.max_length = int(max_length)
        self.window_overlap = int(window_overlap)
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._hidden_size: int | None = None
        self._warmed = False
        self._warmup_lock = threading.Lock()

    @classmethod
    def for_testing(
        cls,
        tokenizer: Any,
        model: Any,
        *,
        device: str = "cpu",
        max_length: int = 8192,
        window_overlap: int = 256,
    ) -> "LateChunkEncoder":
        encoder = cls(
            model_name=getattr(model, "name_or_path", "test-double"),
            device=device,
            max_length=max_length,
            window_overlap=window_overlap,
        )
        encoder._tokenizer = tokenizer
        encoder._model = model
        encoder._hidden_size = encoder._infer_hidden_size(model)
        return encoder

    @property
    def dim(self) -> int | None:
        return self._hidden_size

    def _infer_hidden_size(self, model: Any) -> int | None:
        config = getattr(model, "config", None)
        hidden = getattr(config, "hidden_size", None)
        return int(hidden) if hidden is not None else None

    def _ensure_backend(self) -> tuple[Any, Any]:
        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model
        if os.getenv("SKIP_MODEL_LOAD") == "1":
            raise RuntimeError("Model loading skipped by SKIP_MODEL_LOAD=1")

        key = (self.model_name, self.device)
        backend = self._shared_backends.get(key)
        if backend is None:
            with self._shared_lock:
                backend = self._shared_backends.get(key)
                if backend is None:
                    tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                    # use_safetensors=True: torch 2.5.x (pin của repo) + transformers chặn
                    # torch.load file .bin (CVE-2025-32434) → buộc nạp .safetensors.
                    model = AutoModel.from_pretrained(self.model_name, use_safetensors=True)
                    model.eval()
                    if self.device:
                        model.to(self.device)
                    hidden = self._infer_hidden_size(model)
                    if hidden is None:
                        raise RuntimeError("Cannot determine hidden size")
                    backend = (tokenizer, model, hidden)
                    self._shared_backends[key] = backend

        self._tokenizer, self._model, self._hidden_size = backend
        return self._tokenizer, self._model

    def warmup(self) -> None:
        if os.getenv("SKIP_MODEL_LOAD") == "1" or self._warmed:
            return
        with self._warmup_lock:
            if self._warmed:
                return
            tokenizer, model = self._ensure_backend()
            encoded = tokenizer(
                "warmup",
                add_special_tokens=False,
                return_tensors="pt",
                truncation=True,
                max_length=min(self.max_length, 8),
            )
            inputs = self._tensorize_inputs(encoded)
            with torch.no_grad():
                _ = model(**inputs).last_hidden_state
            self._warmed = True

    def _tensorize_inputs(self, encoded: dict[str, Any]) -> dict[str, torch.Tensor]:
        inputs: dict[str, torch.Tensor] = {}
        for key, value in encoded.items():
            if key == "offset_mapping":
                continue
            tensor = value if isinstance(value, torch.Tensor) else torch.tensor(value)
            inputs[key] = tensor.to(self.device)
        if "attention_mask" not in inputs and "input_ids" in inputs:
            inputs["attention_mask"] = torch.ones_like(inputs["input_ids"])
        return inputs

    def _mean_pool_texts(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        if not texts:
            hidden = self.dim or 0
            return np.zeros((0, hidden), dtype=np.float32)

        tokenizer, model = self._ensure_backend()
        hidden = self.dim
        if hidden is None:
            raise RuntimeError("Hidden size unavailable")

        outputs = np.zeros((len(texts), hidden), dtype=np.float32)
        non_empty_indices = [idx for idx, text in enumerate(texts) if text]
        if not non_empty_indices:
            return outputs

        step = max(1, int(batch_size))
        for batch_start in range(0, len(non_empty_indices), step):
            batch_indices = non_empty_indices[batch_start : batch_start + step]
            batch = [texts[idx] for idx in batch_indices]
            encoded = tokenizer(
                batch,
                add_special_tokens=False,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            inputs = self._tensorize_inputs(encoded)
            mask = inputs["attention_mask"].to(torch.float32)
            with torch.no_grad():
                hidden_state = model(**inputs).last_hidden_state.to(torch.float32)
            lengths = mask.sum(dim=1, keepdim=True)
            lengths = torch.clamp(lengths, min=1.0)
            pooled = (hidden_state * mask.unsqueeze(-1)).sum(dim=1) / lengths
            batch_arr = pooled.detach().cpu().numpy().astype(np.float32, copy=False)
            outputs[batch_indices] = batch_arr

        if outputs.shape[1] != hidden:
            raise RuntimeError("Unexpected embedding width")
        return l2_normalize(outputs)

    def embed_document(self, text: str, spans: list[tuple[int, int]]) -> np.ndarray:
        tokenizer, model = self._ensure_backend()
        hidden = self.dim
        if hidden is None:
            raise RuntimeError("Hidden size unavailable")

        encoded = tokenizer(
            text,
            add_special_tokens=False,
            return_offsets_mapping=True,
            truncation=False,
        )
        offsets = np.asarray(encoded.get("offset_mapping", []), dtype=np.int64)
        input_ids = list(encoded.get("input_ids", []))
        attention_mask = list(encoded.get("attention_mask", [1] * len(input_ids)))

        def _forward_window(start: int, end: int) -> np.ndarray:
            window_inputs = {
                "input_ids": torch.tensor([input_ids[start:end]], dtype=torch.long, device=self.device),
                "attention_mask": torch.tensor(
                    [attention_mask[start:end]], dtype=torch.long, device=self.device
                ),
            }
            with torch.no_grad():
                hidden_state = model(**window_inputs).last_hidden_state[0]
            return hidden_state.detach().cpu().numpy().astype(np.float32, copy=False)

        token_embs = accumulate_token_embeddings(
            n_tokens=len(input_ids),
            hidden=hidden,
            forward_fn=_forward_window,
            max_length=self.max_length,
            window_overlap=self.window_overlap,
        )

        def _fallback(span: tuple[int, int]) -> np.ndarray:
            snippet = text[span[0] : span[1]]
            return np.asarray(
                self.encode(snippet, convert_to_numpy=True),
                dtype=np.float32,
            ).reshape(hidden)

        return pool_spans(token_embs, offsets, spans, fallback_fn=_fallback)

    def embed_query(self, text: str) -> np.ndarray:
        return np.asarray(self.encode(text, convert_to_numpy=True), dtype=np.float32).reshape(1, -1)

    def encode(self, texts, convert_to_numpy: bool = True, batch_size: int = 32, **kwargs):
        del kwargs
        single = isinstance(texts, str)
        items = [texts] if single else list(texts)
        normalized = self._mean_pool_texts(
            [str(item or "") for item in items],
            batch_size=batch_size,
        )
        if single:
            result = normalized[0] if len(normalized) else np.zeros(self.dim or 0, dtype=np.float32)
        else:
            result = normalized
        if convert_to_numpy:
            return result
        return torch.from_numpy(np.asarray(result, dtype=np.float32))


# --- Singleton dùng chung (ingest embed_document ⟷ query embed_query): CÙNG model/pooling ---
# Default bge-m3 (long-context 8192). KHÔNG default all-MiniLM (max 512 → vỡ late chunking).
_DEFAULT_LATE_CHUNK_MODEL = "BAAI/bge-m3"
_encoder_singleton: "LateChunkEncoder | None" = None
_encoder_lock = threading.Lock()


def get_late_chunk_encoder(model_name: str | None = None) -> "LateChunkEncoder":
    """Trả encoder singleton (cache theo model-name). Đổi model-name → tạo lại."""
    global _encoder_singleton
    name = (model_name or os.getenv("EMBEDDING_MODEL_NAME") or _DEFAULT_LATE_CHUNK_MODEL).strip()
    if not name:
        name = _DEFAULT_LATE_CHUNK_MODEL
    enc = _encoder_singleton
    if enc is not None and enc.model_name == name:
        return enc
    with _encoder_lock:
        if _encoder_singleton is None or _encoder_singleton.model_name != name:
            _encoder_singleton = LateChunkEncoder(model_name=name)
        return _encoder_singleton


def _reset_encoder_singleton() -> None:
    """Test hook: xoá cache singleton."""
    global _encoder_singleton
    _encoder_singleton = None
