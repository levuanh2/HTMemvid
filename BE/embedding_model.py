import os
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

_model: Optional["SentenceTransformer"] = None
_model_name_loaded: Optional[str] = None


def get_embedding_model(model_name: str | None = None) -> Optional["SentenceTransformer"]:
    """
    Lazy singleton SentenceTransformer.
    - Không load khi import module; chỉ load khi gọi hàm này lần đầu (production).
    - SKIP_MODEL_LOAD=1: trả None (CI / smoke test), không tải model.
    """
    global _model, _model_name_loaded

    if os.getenv("SKIP_MODEL_LOAD") == "1":
        return None

    name = model_name or os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)

    if _model is not None and _model_name_loaded == name:
        return _model

    print(f"[MODEL] Loading embedding model: {name!r} ...")
    # Import chậm để tránh kéo torch/transformers ở thời điểm import module
    # (đặc biệt hữu ích khi SKIP_MODEL_LOAD=1 trong CI).
    from sentence_transformers import SentenceTransformer
    _model = SentenceTransformer(name)
    _model_name_loaded = name
    return _model


def get_sentence_transformer(model_name: str | None = None) -> "SentenceTransformer":
    """
    Tương thích ngược với code cũ: giống get_embedding_model nhưng raise nếu không load được
    (ví dụ CI mode).
    """
    m = get_embedding_model(model_name)
    if m is None:
        raise RuntimeError("Embedding model not available (CI mode)")
    return m
