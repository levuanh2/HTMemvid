import os
from functools import lru_cache

from sentence_transformers import SentenceTransformer

DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@lru_cache(maxsize=1)
def get_sentence_transformer(model_name: str | None = None) -> SentenceTransformer:
    """
    Singleton SentenceTransformer để:
    - Giảm RAM/CPU do load model lặp
    - Tái sử dụng cho chunk indexing và Memory Tree
    """
    name = model_name or os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
    return SentenceTransformer(name)

