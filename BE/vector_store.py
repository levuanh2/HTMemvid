"""
Index FAISS + index.json — LangChain LC store (USE_LC_VECTOR_STORE) + legacy faiss-cpu.
Thay thế hoàn toàn faiss_utils.py + phần LC trước đây tách file.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import faiss
import numpy as np
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from llm_factory import (
    DEFAULT_EMBEDDING_MODEL_NAME,
    get_embedding_model,
    get_embeddings,
)

try:
    from env_loader import load_project_env

    load_project_env(override=False)
except Exception:
    pass

DATA_ROOT = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent)))
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


def _load_index(dim: int):
    if os.path.exists(INDEX_PATH):
        idx = faiss.read_index(INDEX_PATH)
        if not isinstance(idx, faiss.IndexIDMap):
            base = faiss.IndexFlatL2(dim)
            new_idx = faiss.IndexIDMap(base)
            xb = idx.reconstruct_n(0, idx.ntotal)
            ids = np.arange(idx.ntotal, dtype="int64")
            new_idx.add_with_ids(xb, ids)
            return new_idx
        return idx
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
) -> None:
    if _skip_faiss_in_ci():
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

    vs_existing = load_vectorstore()
    if vs_existing is None:
        vs = FAISS.from_documents(docs, emb)
    else:
        vs = vs_existing
        vs.add_documents(docs)

    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    _backup_dir_before_write(INDEX_DIR, keep=keep)
    vs.save_local(str(INDEX_DIR))

    for i, chunk in enumerate(chunks):
        meta_entry: Dict[str, Any] = {
            "text": chunk,
            "video": video_name,
            "timestamp": now,
        }
        if custom_metadata and i < len(custom_metadata):
            meta_entry.update(custom_metadata[i])
        emb_vec = _optional_prefix_embedding_list(chunk)
        if emb_vec is not None:
            meta_entry["embedding"] = emb_vec
        meta[str(ids[i])] = meta_entry

    num_chunks = sum(1 for k in meta.keys() if isinstance(k, str) and k.isdigit())
    meta["__meta__"] = {
        "version": "1.0",
        "created_at": meta.get("__meta__", {}).get("created_at") or now,
        "num_chunks": num_chunks,
        "vector_backend": "langchain_faiss",
    }
    _save_meta(meta)
    print(f"[vector_store] added {len(chunks)} chunks video={video_name!r} (total={num_chunks})")


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
    for cid, v in pairs:
        docs.append(
            Document(
                page_content=v.get("text") or "",
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
    meta["__meta__"] = {
        "version": "1.0",
        "created_at": meta.get("__meta__", {}).get("created_at") or datetime.now().isoformat(),
        "num_chunks": num_chunks,
        "vector_backend": "langchain_faiss",
    }
    _save_meta(meta)
    print(f"[vector_store] rebuilt LC FAISS vectors={num_chunks}")


def similarity_search_lc(query: str, k: int = 5) -> List[str]:
    vs = load_vectorstore()
    if vs is None:
        return []
    docs = vs.similarity_search(query, k=k)
    return [(d.page_content or "").strip() for d in docs if (d.page_content or "").strip()]


# ----- Public API (thay faiss_utils) -----
def append_to_index(chunks: List[str], video_name: str = "", custom_metadata: List[Dict] = None, batch_size: int = 32):
    if not chunks:
        return

    if _skip_faiss_in_ci():
        print("[vector_store] Skipped append_to_index (CI mode)")
        return

    if _use_lc_vector_store():
        try:
            append_chunks_to_lc_index(chunks, video_name, custom_metadata, batch_size)
            return
        except Exception as exc:
            print(f"[vector_store] LangChain vector store failed, fallback legacy FAISS: {exc}")

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
        all_embeds.append(batch_embeds)

    embeds = np.vstack(all_embeds) if len(all_embeds) > 1 else all_embeds[0]
    dim = embeds.shape[1]

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
    for i, chunk in enumerate(chunks):
        meta_entry = {
            "text": chunk,
            "video": video_name,
            "timestamp": now,
        }
        if custom_metadata and i < len(custom_metadata):
            meta_entry.update(custom_metadata[i])
        emb_vec = _optional_prefix_embedding_list(chunk)
        if emb_vec is not None:
            meta_entry["embedding"] = emb_vec

        meta[str(int(ids[i]))] = meta_entry

    num_chunks = sum(1 for k in meta.keys() if isinstance(k, str) and k.isdigit())
    meta["__meta__"] = {
        "version": "1.0",
        "created_at": meta.get("__meta__", {}).get("created_at") or now,
        "num_chunks": num_chunks,
    }
    _save_meta(meta)
    print(f"[INDEX] added {len(chunks)} chunks video={video_name!r} (total={num_chunks})")


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
    idx = faiss.read_index(INDEX_PATH)
    _, I = idx.search(qv, k)

    meta = _load_meta()
    results = []
    for iid in I[0]:
        if iid == -1:
            continue
        key = str(int(iid))
        if key in meta:
            results.append(meta[key]["text"])
    return results


def delete_source_from_index(video_name: str):
    meta = _load_meta()
    keep_meta = {k: v for k, v in meta.items() if v.get("video") != video_name}
    _save_meta(keep_meta)
    rebuild_chunk_index(keep_meta)


def delete_chunks_by_source(source_id: str) -> int:
    meta = _load_meta()
    if not meta:
        return 0

    target = _normalize_source_id(source_id)
    keep_meta: Dict[str, Dict] = {}
    deleted = 0

    for k, v in meta.items():
        video_raw = v.get("video", "")
        vid_norm = _normalize_source_id(video_raw)
        if vid_norm == target:
            deleted += 1
            continue
        keep_meta[k] = v

    _save_meta(keep_meta)
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

    texts: List[str] = []
    ids: List[int] = []
    for k, v in meta.items():
        try:
            if not isinstance(k, str) or not k.isdigit():
                continue
            ids.append(int(k))
            texts.append(v.get("text", ""))
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
        all_embeds.append(batch_embeds)

    embeds = np.vstack(all_embeds) if len(all_embeds) > 1 else all_embeds[0]
    dim = embeds.shape[1]

    base = faiss.IndexFlatL2(dim)
    idx = faiss.IndexIDMap(base)
    idx.add_with_ids(embeds, np.array(ids, dtype="int64"))
    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    save_index_with_backup(idx, INDEX_DIR, keep=keep)

    num_chunks = len(ids)
    meta["__meta__"] = {
        "version": "1.0",
        "created_at": meta.get("__meta__", {}).get("created_at") or datetime.now().isoformat(),
        "num_chunks": num_chunks,
    }
    _save_meta(meta)
    print(f"[INDEX] rebuilt FAISS vectors= {num_chunks}")
