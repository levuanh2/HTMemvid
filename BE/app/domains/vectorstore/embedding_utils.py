"""
Embedding utilities - shared helpers cho embedding/vector operations.
Dung chung cho vector_store.py, memory_tree.py, mindmap_generation_worker.py, retrieval/*.py
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

import faiss
import numpy as np

logger = logging.getLogger(__name__)


def normalize_embeddings_array(
    embeds,
    *,
    expected_count=None,
    expected_dim=None,
    context="embedding",
):
    """
    Chuẩn hóa embeddings thành np.ndarray 2D float32.
    Raise ValueError nếu shape không khớp.
    """
    if not isinstance(embeds, np.ndarray):
        if not embeds:
            raise ValueError(f"[{context}] embedding list is empty")
        embeds = np.asarray(embeds, dtype="float32")

    if embeds.ndim == 1:
        embeds = embeds.reshape(1, -1)

    if embeds.ndim != 2:
        raise ValueError(f"[{context}] embedding array must be 2D, got shape={embeds.shape}")

    if expected_count is not None and embeds.shape[0] != expected_count:
        raise ValueError(
            f"[{context}] embedding count mismatch: expected={expected_count}, got={embeds.shape[0]}, shape={embeds.shape}"
        )

    if expected_dim is not None and expected_dim > 0 and embeds.shape[1] != expected_dim:
        raise ValueError(
            f"[{context}] embedding dim mismatch: expected={expected_dim}, got={embeds.shape[1]}, shape={embeds.shape}"
        )

    if embeds.shape[1] <= 0:
        raise ValueError(f"[{context}] invalid embedding dim: shape={embeds.shape}")

    return embeds


def safe_stack_vectors(
    vectors,
    *,
    expected_dim=None,
    context="embedding",
    skip_mismatched=True,
):
    """
    Stack list of vectors thành 2D array.
    - skip_mismatched=True: bỏ qua vectors có dim khác expected_dim
    - skip_mismatched=False: raise ValueError nếu có dim mismatch
    """
    if not vectors:
        return None

    valid_vectors = []
    skipped = 0

    for i, v in enumerate(vectors):
        if not isinstance(v, np.ndarray):
            v = np.asarray(v, dtype="float32")
        if v.ndim == 1:
            v = v.reshape(1, -1)
        if v.ndim != 2:
            skipped += 1
            logger.warning("[%s] skip invalid vector at index %d: ndim=%s", context, i, v.ndim)
            continue

        dim = v.shape[1]
        if expected_dim is not None and expected_dim > 0 and dim != expected_dim:
            skipped += 1
            logger.warning(
                "[%s] skip vector at index %d: dim=%d != expected=%d",
                context, i, dim, expected_dim
            )
            continue

        valid_vectors.append(v)

    if skipped > 0:
        logger.info("[%s] skipped %d vectors due to dim mismatch", context, skipped)

    if not valid_vectors:
        logger.warning("[%s] no valid vectors after filtering", context)
        return None

    if len(valid_vectors) == 1:
        return valid_vectors[0]

    result = np.vstack(valid_vectors)
    logger.debug("[%s] stacked %d vectors: shape=%s", context, len(valid_vectors), result.shape)
    return result


def get_embedding_dim_safe(
    get_model_fn,
    model_name=None,
    default_dim=0,
):
    """
    Lấy embedding dimension từ model một cách an toàn.
    Trả về default_dim nếu có lỗi.
    """
    try:
        model = get_model_fn(model_name)
        if model is None:
            return default_dim
        dummy = model.encode(["__dim_check__"], convert_to_numpy=True, show_progress_bar=False)
        return int(dummy.shape[1])
    except Exception as e:
        logger.warning("[embedding_utils] Failed to get embedding dim: %s", e)
        return default_dim


def validate_vector_index_compatibility(index_path, expected_dim):
    """
    Kiểm tra FAISS index có tương thích với expected_dim không.
    - Nếu index không tồn tại: trả về True (không cần rebuild)
    - Nếu index.d == expected_dim: trả về True
    - Nếu index.d != expected_dim: trả về False
    """
    path = Path(index_path)
    if not path.exists():
        return True

    try:
        idx = faiss.read_index(str(path))
        dim_match = idx.d == expected_dim
        logger.info(
            "[embedding_utils] index %s: index.d=%d expected=%d match=%s",
            path.name, idx.d, expected_dim, dim_match
        )
        return dim_match
    except Exception as e:
        logger.warning("[embedding_utils] Failed to read index %s: %s", path, e)
        return False
