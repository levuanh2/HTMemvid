from __future__ import annotations

from numbers import Real
from typing import Any

from app.domains.retrieval.hybrid import _tokenize


def _chunk_text(chunk: Any) -> str:
    if isinstance(chunk, str):
        return chunk
    return str(getattr(chunk, "text", "") or "")


def _numeric_score(chunk: Any, name: str) -> float | None:
    value = getattr(chunk, name, None)
    if isinstance(value, Real):
        score = float(value)
        return min(1.0, max(0.0, score))
    return None


def _relevance(query: str, chunk: Any) -> float:
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return 0.0

    chunk_tokens = set(_tokenize(_chunk_text(chunk)))
    lexical = len(query_tokens & chunk_tokens) / len(query_tokens)

    vector_score = _numeric_score(chunk, "vector_score")
    bm25_score = _numeric_score(chunk, "bm25_score")
    extras = [score for score in (vector_score, bm25_score) if score is not None]
    return max([lexical, *extras], default=lexical)


def grade_documents(
    query: str,
    chunks: list,
    *,
    relevance_threshold: float = 0.25,
    wrong_floor: float = 0.1,
    rerank_scores: list | None = None,
) -> str:
    if not chunks:
        return "wrong"

    rels = [_relevance(query, chunk) for chunk in chunks]
    # Sau Rerank, chunk là str (mất vector/bm25 score) → grade rớt về lexical thuần.
    # Cross-encoder là tín hiệu liên quan tốt hơn lexical: fold vào nếu khớp độ dài.
    if rerank_scores and len(rerank_scores) == len(rels):
        rels = [
            max(r, min(1.0, max(0.0, float(s))))
            for r, s in zip(rels, rerank_scores)
        ]

    best = max(rels, default=0.0)
    if best >= relevance_threshold:
        return "correct"
    if best <= wrong_floor:
        return "wrong"
    return "ambiguous"
