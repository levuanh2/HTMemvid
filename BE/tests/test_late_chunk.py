from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domains.ingest.late_chunk import (
    LateChunkEncoder,
    accumulate_token_embeddings,
    char_span_to_token_span,
    l2_normalize,
    pool_spans,
)


def test_l2_normalize_handles_zero_rows():
    arr = np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    out = l2_normalize(arr)

    assert np.isclose(np.linalg.norm(out[0]), 1.0)
    assert np.allclose(out[1], np.zeros(2, dtype=np.float32))


def test_char_span_to_token_span_boundaries_and_empty():
    offsets = np.array([[0, 2], [2, 4], [4, 7]], dtype=np.int64)

    assert char_span_to_token_span(offsets, 2, 4) == (1, 2)
    assert char_span_to_token_span(offsets, 1, 2) == (0, 1)
    assert char_span_to_token_span(offsets, 5, 6) == (2, 3)
    assert char_span_to_token_span(offsets, 4, 4) is None
    assert char_span_to_token_span(offsets, 7, 8) is None


def test_accumulate_token_embeddings_averages_overlap_and_validates():
    ones = accumulate_token_embeddings(
        n_tokens=10,
        hidden=3,
        forward_fn=lambda start, end: np.ones((end - start, 3), dtype=np.float32),
        max_length=4,
        window_overlap=1,
    )
    assert np.allclose(ones, np.ones((10, 3), dtype=np.float32))

    indexed = accumulate_token_embeddings(
        n_tokens=10,
        hidden=2,
        forward_fn=lambda start, end: np.arange(start, end, dtype=np.float32)[:, None].repeat(2, axis=1),
        max_length=4,
        window_overlap=1,
    )
    expected = np.arange(10, dtype=np.float32)[:, None].repeat(2, axis=1)
    assert np.allclose(indexed, expected)

    with pytest.raises(ValueError):
        accumulate_token_embeddings(
            n_tokens=3,
            hidden=1,
            forward_fn=lambda start, end: np.ones((end - start, 1), dtype=np.float32),
            max_length=4,
            window_overlap=4,
        )


def test_pool_spans_mean_direction_and_fallback():
    token_embs = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )
    offsets = np.array([[0, 2], [2, 4], [4, 6]], dtype=np.int64)
    spans = [(0, 4), (5, 5)]

    fallback_calls: list[tuple[int, int]] = []

    def _fallback(span: tuple[int, int]) -> np.ndarray:
        fallback_calls.append(span)
        return np.array([2.0, 0.0, 0.0], dtype=np.float32)

    out = pool_spans(token_embs, offsets, spans, fallback_fn=_fallback)

    expected_first = np.array([1.0, 1.0, 0.0], dtype=np.float32)
    expected_first = expected_first / np.linalg.norm(expected_first)
    assert np.allclose(out[0], expected_first)
    assert np.isclose(np.linalg.norm(out[0]), 1.0)
    assert np.isclose(np.linalg.norm(out[1]), 1.0)
    assert fallback_calls == [(5, 5)]
    assert np.allclose(out[1], np.array([1.0, 0.0, 0.0], dtype=np.float32))


class FakeTokenizer:
    def __call__(
        self,
        text,
        add_special_tokens: bool = False,
        return_offsets_mapping: bool = False,
        truncation: bool = False,
        padding: bool = False,
        return_tensors: str | None = None,
        max_length: int | None = None,
    ):
        del add_special_tokens, truncation
        if isinstance(text, list):
            token_rows = [self._tokenize(item, max_length=max_length)[0] for item in text]
            max_len = max((len(row) for row in token_rows), default=0)
            if padding:
                token_rows = [row + [0] * (max_len - len(row)) for row in token_rows]
            masks = [[1 if token != 0 else 0 for token in row] for row in token_rows]
            out = {"input_ids": token_rows, "attention_mask": masks}
            if return_tensors == "pt":
                return {key: torch.tensor(value, dtype=torch.long) for key, value in out.items()}
            return out

        input_ids, offsets = self._tokenize(text, max_length=max_length)
        attention_mask = [1] * len(input_ids)
        out = {"input_ids": input_ids, "attention_mask": attention_mask}
        if return_offsets_mapping:
            out["offset_mapping"] = offsets
        if return_tensors == "pt":
            tensor_out = {
                key: torch.tensor([value], dtype=torch.long) for key, value in out.items() if key != "offset_mapping"
            }
            if return_offsets_mapping:
                tensor_out["offset_mapping"] = torch.tensor([offsets], dtype=torch.long)
            return tensor_out
        return out

    def _tokenize(self, text: str, max_length: int | None = None) -> tuple[list[int], list[tuple[int, int]]]:
        words = []
        start = 0
        for token in text.split():
            char_start = text.index(token, start)
            char_end = char_start + len(token)
            start = char_end
            words.append((token, char_start, char_end))
        if max_length is not None:
            words = words[:max_length]
        token_ids = [len(token) for token, _, _ in words]
        offsets = [(char_start, char_end) for _, char_start, char_end in words]
        return token_ids, offsets


class FakeModel:
    def __init__(self, hidden_size: int = 4) -> None:
        self.config = SimpleNamespace(hidden_size=hidden_size)
        self.name_or_path = "fake-bge"

    def eval(self):
        return self

    def to(self, device: str):
        del device
        return self

    def __call__(self, input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None):
        del attention_mask
        ids = input_ids.to(torch.float32)
        left = torch.zeros_like(ids)
        right = torch.zeros_like(ids)
        if ids.shape[1] > 1:
            left[:, 1:] = ids[:, :-1]
            right[:, :-1] = ids[:, 1:]
        stacked = torch.stack(
            [
                ids,
                ids + left,
                ids + right,
                ids + left + right,
            ],
            dim=-1,
        )
        return SimpleNamespace(last_hidden_state=stacked)


def test_embed_document_uses_injected_fake_context(monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    encoder = LateChunkEncoder.for_testing(
        tokenizer=FakeTokenizer(),
        model=FakeModel(hidden_size=4),
        max_length=3,
        window_overlap=1,
    )
    text = "aa bbbb c dddd"
    spans = [(3, 7), (8, 9), (0, 0)]

    out = encoder.embed_document(text, spans)
    standalone = encoder.embed_query("bbbb")[0]

    assert out.shape == (3, 4)
    assert np.allclose(np.linalg.norm(out[:2], axis=1), np.ones(2, dtype=np.float32))
    assert np.allclose(out[2], np.zeros(4, dtype=np.float32))
    assert not np.allclose(out[0], standalone)
