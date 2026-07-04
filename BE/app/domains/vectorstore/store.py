"""
Index FAISS + index.json — LangChain LC store (USE_LC_VECTOR_STORE) + legacy faiss-cpu.
Thay thế hoàn toàn faiss_utils.py + phần LC trước đây tách file.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import faiss
import numpy as np
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from app.clients.llm_factory import (
    DEFAULT_EMBEDDING_MODEL_NAME,
    get_embedding_model,
    get_embeddings,
)
from app.domains.vectorstore.embedding_utils import normalize_embeddings_array
try:
    from shared.env_loader import load_project_env
    load_project_env(override=False)
except Exception:
    pass

logger = logging.getLogger(__name__)

from shared.paths import BE_ROOT
DATA_ROOT = Path(os.environ.get("DATA_DIR", str(BE_ROOT)))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", str(DATA_ROOT / "index")))


def _optional_prefix_embedding_list(text: str) -> Optional[List[float]]:
    """Embed text[:512] cho meta index.json (mindmap KMeans nhanh); None nếu không có model."""
    model = get_embedding_model()
    if model is None:
        return None
    prefix = (text or "")[:512].strip()
    if not prefix:
        return None
    arr = model.encode([prefix], convert_to_numpy=True, show_progress_bar=False)
    return np.asarray(arr[0], dtype=float).tolist()
INDEX_PATH = str(INDEX_DIR / "index.faiss")
META_PATH = str(INDEX_DIR / "index.json")
os.makedirs(str(INDEX_DIR), exist_ok=True)
MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_EMBEDDING_MODEL_NAME)


def _use_lc_vector_store() -> bool:
    return (os.getenv("USE_LC_VECTOR_STORE", "0") or "").strip().lower() in ("1", "true", "yes", "on")


def cleanup_old_backups(index_dir: Path, max_keep: int = 3) -> None:
    try:
        max_keep = int(max_keep)
    except Exception:
        max_keep = 3
    max_keep = max(0, max_keep)

    parent = Path(index_dir).resolve().parent
    prefix = f"{Path(index_dir).name}_backup_"

    backups: list[Path] = []
    for p in parent.iterdir():
        if p.is_dir() and p.name.startswith(prefix):
            backups.append(p)

    backups.sort(key=lambda p: p.name, reverse=True)
    to_delete = backups if max_keep == 0 else backups[max_keep:]

    for p in to_delete:
        try:
            shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass


def save_index_with_backup(index: Any, index_dir: Path, keep: int = 3) -> None:
    index_dir = Path(index_dir).resolve()
    parent = index_dir.parent
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = parent / f"{index_dir.name}_backup_{ts}"

    if index_dir.exists() and index_dir.is_dir():

        def _ignore(_dir: str, names: list[str]) -> set[str]:
            ignored = []
            for n in names:
                if n.startswith(f"{index_dir.name}_backup_"):
                    ignored.append(n)
            return set(ignored)

        try:
            shutil.copytree(index_dir, backup_dir, ignore=_ignore)
        except Exception:
            pass

    cleanup_old_backups(index_dir, max_keep=keep)

    faiss_path = str(index_dir / "index.faiss")
    faiss.write_index(index, faiss_path)


def _skip_faiss_in_ci() -> bool:
    return os.getenv("SKIP_MODEL_LOAD") == "1"


def _require_embedding_model():
    model = get_embedding_model(MODEL_NAME)
    if model is None:
        raise RuntimeError("Embedding model not available (CI mode)")
    return model


def _load_meta() -> Dict[str, Dict]:
    if os.path.exists(META_PATH):
        with open(META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
            if isinstance(meta, dict) and "__meta__" not in meta:
                num_chunks = sum(1 for k in meta.keys() if isinstance(k, str) and k.isdigit())
                meta["__meta__"] = {
                    "version": "1.0",
                    "created_at": datetime.now().isoformat(),
                    "num_chunks": num_chunks,
                }
                try:
                    _save_meta(meta)
                except Exception:
                    pass
            return meta
    return {}


def load_meta() -> Dict[str, Dict]:
    """Đọc THUẦN index.json (chunk_id -> {text, video, embedding, ...}) cho các
    consumer ngoài (memory_tree, retrieval) — seam VectorStore.load_meta().

    Khác _load_meta() nội bộ: KHÔNG tự thêm khoá __meta__ và KHÔNG ghi lại file
    (tránh side-effect khi chỉ đọc). Hành vi khớp memory_tree._load_index_meta cũ.
    """
    p = Path(META_PATH)
    if not p.exists():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"⚠️ Không thể đọc index metadata: {exc}")
        return {}


def _save_meta(meta: dict) -> None:
    os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
    tmp_path = Path(META_PATH).with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    tmp_path.replace(META_PATH)


def _normalize_source_id(name: str) -> str:
    from unicodedata import normalize

    name = (name or "").strip()
    if not name:
        return ""
    if "/" in name or "\\" in name:
        name = os.path.basename(name)
    if "." in name:
        name = os.path.splitext(name)[0]
    cleaned = normalize("NFKD", name).replace("\u00a0", " ")
    import re

    cleaned = re.sub(r"_\d{8}_\d{6}$", "", cleaned)
    return cleaned.strip().lower()


def _get_current_embedding_dim() -> int:
    """Lấy embedding dimension thực tế từ model hiện tại (tránh hard-code)."""
    model = _require_embedding_model()
    dummy = model.encode(["dimension_check"], convert_to_numpy=True, show_progress_bar=False)
    return int(dummy.shape[1])


def _load_index(dim: int):
    """
    Load hoặc tạo FAISS index với dimension validation.
    Nếu index cũ có dim khác, xóa và tạo mới.
    """
    if os.path.exists(INDEX_PATH):
        try:
            idx = faiss.read_index(INDEX_PATH)
            actual_dim = idx.d
            print(f"[vector_store] _load_index: index.d={actual_dim} requested_dim={dim} ntotal={idx.ntotal}")
            if actual_dim != dim:
                print(
                    f"[INDEX] Dimension mismatch: index có {actual_dim}, model yêu cầu {dim}. "
                    f"Xóa index cũ và tạo mới."
                )
                try:
                    os.remove(INDEX_PATH)
                except OSError:
                    pass
                base = faiss.IndexFlatL2(dim)
                return faiss.IndexIDMap(base)
            if not isinstance(idx, faiss.IndexIDMap):
                base = faiss.IndexFlatL2(dim)
                new_idx = faiss.IndexIDMap(base)
                xb = idx.reconstruct_n(0, idx.ntotal)
                ids = np.arange(idx.ntotal, dtype="int64")
                new_idx.add_with_ids(xb, ids)
                return new_idx
            return idx
        except Exception as exc:
            logger.warning("[vector_store] Cannot read existing index: %s. Creating new.", exc)
            try:
                os.remove(INDEX_PATH)
            except OSError:
                pass

    base = faiss.IndexFlatL2(dim)
    return faiss.IndexIDMap(base)


# ----- LC helpers -----
def _backup_dir_before_write(index_dir: Path, keep: int = 3) -> None:
    index_dir = Path(index_dir).resolve()
    parent = index_dir.parent
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = parent / f"{index_dir.name}_backup_{ts}"
    prefix = f"{index_dir.name}_backup_"

    if index_dir.exists() and index_dir.is_dir():

        def _ignore(_dir: str, names: list[str]) -> set[str]:
            ignored = []
            for n in names:
                if n.startswith(prefix):
                    ignored.append(n)
            return set(ignored)

        try:
            shutil.copytree(index_dir, backup_dir, ignore=_ignore)
        except Exception:
            pass

    cleanup_old_backups(index_dir, max_keep=keep)


def load_vectorstore() -> Optional[FAISS]:
    if _skip_faiss_in_ci():
        return None
    faiss_file = INDEX_DIR / "index.faiss"
    if not faiss_file.exists():
        return None
    pkl = INDEX_DIR / "index.pkl"
    if not pkl.exists():
        return None
    try:
        return FAISS.load_local(str(INDEX_DIR), get_embeddings(), allow_dangerous_deserialization=True)
    except Exception:
        return None


def append_chunks_to_lc_index(
    chunks: List[str],
    video_name: str = "",
    custom_metadata: Optional[List[Dict[str, Any]]] = None,
    batch_size: int = 32,
    embeddings: Optional[Any] = None,
) -> None:
    if embeddings is None and _skip_faiss_in_ci():
        print("[vector_store] Skipped append (CI mode)")
        return
    if not chunks:
        return

    meta = _load_meta()
    existing_ids: List[int] = []
    for k in (meta or {}).keys():
        if isinstance(k, str) and k.isdigit():
            existing_ids.append(int(k))
    next_id = max(existing_ids) + 1 if existing_ids else 0
    ids = list(range(next_id, next_id + len(chunks)))

    now = datetime.now().isoformat()
    docs: List[Document] = []
    for i, chunk in enumerate(chunks):
        cid = ids[i]
        md: Dict[str, Any] = {"chunk_id": cid, "video": video_name}
        if custom_metadata and i < len(custom_metadata):
            for kk, vv in (custom_metadata[i] or {}).items():
                md[kk] = vv
        docs.append(Document(page_content=chunk, metadata=md))

    emb = get_embeddings()
    os.makedirs(str(INDEX_DIR), exist_ok=True)

    if embeddings is not None:
        # LATE CHUNKING: dùng vector precomputed; `emb` chỉ để embed query lúc truy vấn.
        vecs = np.asarray(embeddings, dtype="float32")
        if vecs.shape[0] != len(chunks):
            raise ValueError(
                f"embeddings count {vecs.shape[0]} != chunks {len(chunks)}"
            )
        text_embeddings = list(zip(chunks, [v.tolist() for v in vecs]))
        metadatas = [d.metadata for d in docs]
        vs_existing = load_vectorstore()
        if vs_existing is None:
            vs = FAISS.from_embeddings(text_embeddings, emb, metadatas=metadatas)
        else:
            vs = vs_existing
            vs.add_embeddings(text_embeddings, metadatas=metadatas)
    else:
        vs_existing = load_vectorstore()
        if vs_existing is None:
            vs = FAISS.from_documents(docs, emb)
        else:
            vs = vs_existing
            vs.add_documents(docs)

    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    _backup_dir_before_write(INDEX_DIR, keep=keep)
    vs.save_local(str(INDEX_DIR))

    text_items = []
    for i, chunk in enumerate(chunks):
        cid = ids[i]
        meta_entry: Dict[str, Any] = {
            "video": video_name,
            "timestamp": now,
        }
        if custom_metadata and i < len(custom_metadata):
            meta_entry.update(custom_metadata[i])

        has_video = bool(meta_entry.get("video")) and meta_entry.get("frame_index") is not None
        if has_video:
            text_items.append((cid, chunk))
        else:
            meta_entry["text"] = chunk
            text_items.append((cid, chunk))

        emb_vec = _optional_prefix_embedding_list(chunk)
        if emb_vec is not None:
            meta_entry["embedding"] = emb_vec
        meta[str(cid)] = meta_entry

    if text_items:
        import app.domains.vectorstore.chunk_text_store as chunk_text_store
        chunk_text_store.put_many(text_items)


    num_chunks = sum(1 for k in meta.keys() if isinstance(k, str) and k.isdigit())
    model_name = MODEL_NAME
    if embeddings is not None:
        emb_dim = int(np.asarray(embeddings).shape[1])
    else:
        try:
            # Dùng lại `emb` (get_embeddings module-level) — KHÔNG re-import trong hàm,
            # vì `from ... import get_embeddings` ở đây sẽ shadow biến module-level →
            # `emb = get_embeddings()` phía trên ném UnboundLocalError (lỗi đã gặp).
            dummy = emb.embed_query("dim_check")
            emb_dim = len(dummy)
        except Exception:
            emb_dim = 0
    meta["__meta__"] = {
        "version": "1.1",
        "created_at": meta.get("__meta__", {}).get("created_at") or now,
        "num_chunks": num_chunks,
        "embedding_model_name": model_name,
        "embedding_dim": emb_dim,
        "vector_backend": "langchain_faiss",
        "pooling": "mean_late" if embeddings is not None else "encode",
    }
    _save_meta(meta)
    print(f"[vector_store] added {len(chunks)} chunks video={video_name!r} (total={num_chunks}, model={model_name})")


def rebuild_lc_index_from_meta(meta: Dict[str, Any]) -> None:
    if _skip_faiss_in_ci():
        return

    pairs: List[tuple[int, Dict[str, Any]]] = []
    for k, v in meta.items():
        if not isinstance(k, str) or not k.isdigit():
            continue
        if not isinstance(v, dict):
            continue
        pairs.append((int(k), v))

    if not pairs:
        for fn in ("index.faiss", "index.pkl"):
            p = INDEX_DIR / fn
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        if os.path.exists(INDEX_PATH):
            try:
                os.remove(INDEX_PATH)
            except Exception:
                pass
        return

    pairs.sort(key=lambda x: x[0])
    docs: List[Document] = []
    from app.domains.vectorstore import chunk_text_store
    for cid, v in pairs:
        t = chunk_text_store.get_text(cid) or v.get("text") or ""
        docs.append(
            Document(
                page_content=t,
                metadata={
                    "chunk_id": cid,
                    "video": v.get("video") or "",
                },
            )
        )

    emb = get_embeddings()
    vs = FAISS.from_documents(docs, emb)
    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    _backup_dir_before_write(INDEX_DIR, keep=keep)
    vs.save_local(str(INDEX_DIR))

    num_chunks = len(pairs)
    emb_dim = 0
    try:
        dummy = emb.embed_query("dim_check")
        emb_dim = len(dummy)
    except Exception:
        pass
    meta["__meta__"] = {
        "version": "1.1",
        "created_at": meta.get("__meta__", {}).get("created_at") or datetime.now().isoformat(),
        "num_chunks": num_chunks,
        "embedding_model_name": MODEL_NAME,
        "embedding_dim": emb_dim,
        "vector_backend": "langchain_faiss",
    }
    _save_meta(meta)
    print(f"[vector_store] rebuilt LC FAISS vectors={num_chunks} (model={MODEL_NAME})")


def similarity_search_lc(query: str, k: int = 5) -> List[str]:
    vs = load_vectorstore()
    if vs is None:
        return []
    docs = vs.similarity_search(query, k=k)
    return [(d.page_content or "").strip() for d in docs if (d.page_content or "").strip()]


def remove_chunks_from_lc_index(chunk_ids: list[int]) -> int:
    if _skip_faiss_in_ci():
        return 0
    if not chunk_ids:
        return 0

    vs = load_vectorstore()
    if vs is None:
        return 0

    wanted = {int(cid) for cid in chunk_ids}
    docstore_ids: list[str] = []
    doc_dict = getattr(vs.docstore, "_dict", {}) or {}
    for docstore_id, doc in doc_dict.items():
        md = getattr(doc, "metadata", {}) or {}
        try:
            cid = int(md.get("chunk_id"))
        except Exception:
            continue
        if cid in wanted:
            docstore_ids.append(str(docstore_id))

    if not docstore_ids:
        return 0

    ok = vs.delete(ids=docstore_ids)
    if ok is False:
        raise RuntimeError("LangChain FAISS.delete returned False")

    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    _backup_dir_before_write(INDEX_DIR, keep=keep)
    vs.save_local(str(INDEX_DIR))
    return len(docstore_ids)


def remove_chunks_from_raw_index(chunk_ids: list[int]) -> int:
    if _skip_faiss_in_ci():
        return 0

    ids = [int(cid) for cid in chunk_ids]
    if not ids or not os.path.exists(INDEX_PATH):
        return 0

    idx = faiss.read_index(INDEX_PATH)
    removed = idx.remove_ids(np.array(ids, dtype="int64"))
    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    save_index_with_backup(idx, INDEX_DIR, keep=keep)
    return int(removed)


def _save_meta_with_updated_num_chunks(meta: Dict[str, Dict]) -> None:
    meta_to_save = dict(meta)
    old_meta = meta.get("__meta__", {}) if isinstance(meta.get("__meta__"), dict) else {}
    num_chunks = sum(1 for k in meta_to_save.keys() if isinstance(k, str) and k.isdigit())
    meta_to_save["__meta__"] = {
        **old_meta,
        "num_chunks": num_chunks,
        "created_at": old_meta.get("created_at") or datetime.now().isoformat(),
    }
    _save_meta(meta_to_save)


# ----- Public API (thay faiss_utils) -----
def append_to_index(
    chunks: List[str],
    video_name: str = "",
    custom_metadata: List[Dict] = None,
    batch_size: int = 32,
    embeddings: Optional[Any] = None,
):
    """Thêm chunk vào index.

    `embeddings` (LATE CHUNKING): mảng (n_chunks, dim) ĐÃ mean-pool sẵn ở chunk_node.
    Khi có, BỎ QUA encode lại (vector late-chunk không tái tạo được từ text chunk).
    Vì không cần model, đường này vẫn chạy dưới SKIP_MODEL_LOAD.
    """
    if not chunks:
        return

    if embeddings is None and _skip_faiss_in_ci():
        print("[vector_store] Skipped append_to_index (CI mode)")
        return

    if _use_lc_vector_store():
        try:
            append_chunks_to_lc_index(chunks, video_name, custom_metadata, batch_size, embeddings)
            return
        except Exception as exc:
            print(f"[vector_store] LangChain vector store failed, fallback legacy FAISS: {exc}")

    if embeddings is not None:
        embeds = np.asarray(embeddings, dtype="float32")
    else:
        model = _require_embedding_model()
        all_embeds = []
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            batch_embeds = model.encode(
                batch,
                convert_to_numpy=True,
                batch_size=batch_size,
                show_progress_bar=False,
            ).astype("float32")
            print(f"[vector_store] batch {i//batch_size}: type={type(batch_embeds)}, shape={getattr(batch_embeds, 'shape', None)}")
            all_embeds.append(batch_embeds)
        embeds = np.vstack(all_embeds) if len(all_embeds) > 1 else all_embeds[0]

    # Validate embeddings trước khi add vào index
    try:
        embeds = normalize_embeddings_array(
            embeds,
            expected_count=len(chunks),
            context="append_to_index",
        )
    except ValueError as e:
        print(f"[vector_store] ERROR: embedding validation failed: {e}")
        raise

    dim = embeds.shape[1]
    print(f"[vector_store] append_to_index: model={MODEL_NAME} chunks={len(chunks)} embeds_shape={embeds.shape}")

    meta = _load_meta()

    existing_ids: List[int] = []
    for k in (meta or {}).keys():
        if isinstance(k, str) and k.isdigit():
            existing_ids.append(int(k))
    next_id = max(existing_ids) + 1 if existing_ids else 0
    ids = np.arange(next_id, next_id + len(chunks), dtype="int64")

    idx = _load_index(dim)
    idx.add_with_ids(embeds, ids)
    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    save_index_with_backup(idx, INDEX_DIR, keep=keep)

    now = datetime.now().isoformat()
    text_items = []
    for i, chunk in enumerate(chunks):
        cid = int(ids[i])
        meta_entry = {
            "video": video_name,
            "timestamp": now,
        }
        if custom_metadata and i < len(custom_metadata):
            meta_entry.update(custom_metadata[i])

        has_video = bool(meta_entry.get("video")) and meta_entry.get("frame_index") is not None
        if has_video:
            text_items.append((cid, chunk))
        else:
            meta_entry["text"] = chunk
            text_items.append((cid, chunk))

        emb_vec = _optional_prefix_embedding_list(chunk)
        if emb_vec is not None:
            meta_entry["embedding"] = emb_vec

        meta[str(cid)] = meta_entry

    if text_items:
        import app.domains.vectorstore.chunk_text_store as chunk_text_store
        chunk_text_store.put_many(text_items)


    num_chunks = sum(1 for k in meta.keys() if isinstance(k, str) and k.isdigit())
    meta["__meta__"] = {
        "version": "1.1",
        "created_at": meta.get("__meta__", {}).get("created_at") or now,
        "num_chunks": num_chunks,
        "embedding_model_name": MODEL_NAME,
        "embedding_dim": dim,
        # mean_late: vector late-chunk (mean-pool theo span); encode: tự encode 1 vector/chunk.
        "pooling": "mean_late" if embeddings is not None else "encode",
    }
    _save_meta(meta)
    print(f"[INDEX] added {len(chunks)} chunks video={video_name!r} (total={num_chunks}, model={MODEL_NAME}, dim={dim})")


def search_index(query: str, k: int = 5) -> List[str]:
    if _skip_faiss_in_ci():
        print("[vector_store] Skipped search_index (CI mode)")
        return []

    if _use_lc_vector_store():
        try:
            if load_vectorstore() is not None:
                return similarity_search_lc(query, k)
        except Exception as exc:
            print(f"[vector_store] LC search failed, fallback legacy: {exc}")

    if not os.path.exists(INDEX_PATH):
        return []

    model = _require_embedding_model()
    qv = model.encode([query], convert_to_numpy=True).astype("float32")
    
    # Validate query vector shape
    if qv.ndim == 1:
        qv = qv.reshape(1, -1)
    if qv.ndim != 2 or qv.shape[0] != 1:
        logger.warning("[vector_store] Invalid query vector shape: %s", qv.shape)
        return []
    
    try:
        idx = faiss.read_index(INDEX_PATH)
    except Exception as exc:
        logger.error("[vector_store] Cannot read FAISS index: %s", exc)
        return []
    
    # Validate dimension match
    if qv.shape[1] != idx.d:
        logger.warning(
            "[vector_store] Query dim=%d != index dim=%d. Cannot search.",
            qv.shape[1], idx.d
        )
        return []
    
    _, I = idx.search(qv, k)

    meta = _load_meta()
    results = []
    for iid in I[0]:
        if iid == -1:
            continue
        key = str(int(iid))
        if key in meta:
            from app.domains.vectorstore import chunk_text_store
            t = chunk_text_store.get_text(int(key))
            if t:
                results.append(t)
    return results


def delete_source_from_index(video_name: str):
    meta = _load_meta()
    chunk_ids: List[int] = []
    keep_meta: Dict[str, Dict] = {}

    for k, v in meta.items():
        if not isinstance(v, dict):
            keep_meta[k] = v
            continue
        if isinstance(k, str) and k.isdigit() and v.get("video") == video_name:
            chunk_ids.append(int(k))
            continue
        keep_meta[k] = v

    if not chunk_ids:
        return

    try:
        if _use_lc_vector_store():
            remove_chunks_from_lc_index(chunk_ids)
        else:
            remove_chunks_from_raw_index(chunk_ids)
        _save_meta_with_updated_num_chunks(keep_meta)
    except Exception:
        _save_meta_with_updated_num_chunks(keep_meta)
        rebuild_chunk_index(keep_meta)


def delete_chunks_by_source(source_id: str) -> int:
    meta = _load_meta()
    if not meta:
        return 0

    target = _normalize_source_id(source_id)
    keep_meta: Dict[str, Dict] = {}
    deleted = 0
    chunk_ids: List[int] = []

    for k, v in meta.items():
        if not isinstance(v, dict):
            keep_meta[k] = v
            continue
        video_raw = v.get("video", "")
        vid_norm = _normalize_source_id(video_raw)
        if isinstance(k, str) and k.isdigit() and vid_norm == target:
            deleted += 1
            chunk_ids.append(int(k))
            continue
        keep_meta[k] = v

    if deleted == 0:
        return 0

    try:
        if _use_lc_vector_store():
            remove_chunks_from_lc_index(chunk_ids)
        else:
            remove_chunks_from_raw_index(chunk_ids)
        _save_meta_with_updated_num_chunks(keep_meta)
    except Exception:
        _save_meta_with_updated_num_chunks(keep_meta)
        rebuild_chunk_index(keep_meta)

    return deleted


def rebuild_chunk_index(existing_meta: Dict[str, Dict] | None = None) -> None:
    if _skip_faiss_in_ci():
        print("[vector_store] Skipped rebuild_chunk_index (CI mode)")
        return

    meta = existing_meta if existing_meta is not None else _load_meta()

    if not meta:
        if os.path.exists(INDEX_PATH):
            os.remove(INDEX_PATH)
        pkl = INDEX_DIR / "index.pkl"
        if pkl.exists():
            try:
                pkl.unlink()
            except Exception:
                pass
        return

    from app.domains.vectorstore import chunk_text_store
    texts: List[str] = []
    ids: List[int] = []
    for k, v in meta.items():
        try:
            if not isinstance(k, str) or not k.isdigit():
                continue
            t = chunk_text_store.get_text(int(k)) or v.get("text") or ""
            ids.append(int(k))
            texts.append(t)
        except ValueError:
            continue

    if not texts or not ids:
        if os.path.exists(INDEX_PATH):
            os.remove(INDEX_PATH)
        pkl = INDEX_DIR / "index.pkl"
        if pkl.exists():
            try:
                pkl.unlink()
            except Exception:
                pass
        try:
            meta["__meta__"] = {
                "version": "1.0",
                "created_at": meta.get("__meta__", {}).get("created_at") or datetime.now().isoformat(),
                "num_chunks": 0,
            }
            _save_meta(meta)
        except Exception:
            pass
        return

    if _use_lc_vector_store():
        try:
            rebuild_lc_index_from_meta(meta)
            return
        except Exception as exc:
            print(f"[vector_store] LC rebuild failed, fallback legacy: {exc}")

    model = _require_embedding_model()

    batch_size = 32
    all_embeds = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_embeds = model.encode(
            batch,
            convert_to_numpy=True,
            batch_size=batch_size,
            show_progress_bar=False,
        ).astype("float32")
        print(f"[vector_store] rebuild batch {i//batch_size}: shape={getattr(batch_embeds, 'shape', None)}")
        all_embeds.append(batch_embeds)

    embeds = np.vstack(all_embeds) if len(all_embeds) > 1 else all_embeds[0]
    dim = embeds.shape[1]
    print(f"[vector_store] rebuild_chunk_index: model={MODEL_NAME} texts={len(texts)} embeds_shape={embeds.shape}")

    base = faiss.IndexFlatL2(dim)
    idx = faiss.IndexIDMap(base)
    idx.add_with_ids(embeds, np.array(ids, dtype="int64"))
    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    save_index_with_backup(idx, INDEX_DIR, keep=keep)

    num_chunks = len(ids)
    meta["__meta__"] = {
        "version": "1.1",
        "created_at": meta.get("__meta__", {}).get("created_at") or datetime.now().isoformat(),
        "num_chunks": num_chunks,
        "embedding_model_name": MODEL_NAME,
        "embedding_dim": dim,
    }
    _save_meta(meta)
    print(f"[INDEX] rebuilt FAISS vectors={num_chunks} (model={MODEL_NAME}, dim={dim})")
