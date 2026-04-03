import os
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple
import numpy as np
import faiss
from embedding_model import get_sentence_transformer, DEFAULT_MODEL_NAME

# Đường dẫn index và metadata (ưu tiên DATA_DIR=/app trong Docker)
DATA_ROOT = Path(os.environ.get("DATA_DIR", str(Path(__file__).resolve().parent)))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", str(DATA_ROOT / "index")))
INDEX_PATH = str(INDEX_DIR / "index.faiss")
META_PATH = str(INDEX_DIR / "index.json")
os.makedirs(str(INDEX_DIR), exist_ok=True)
MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)

# Singleton model (được cache trong embedding_model.py)
_model = get_sentence_transformer(MODEL_NAME)


# ===== Helpers =====
def _load_meta() -> Dict[str, Dict]:
    if os.path.exists(META_PATH):
        with open(META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
            if isinstance(meta, dict) and "__meta__" not in meta:
                # Backward compatibility: nâng cấp metadata versioning cho index hiện có
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
    """
    Lưu metadata một cách an toàn (ghi ra file tạm rồi replace).
    """
    os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
    tmp_path = Path(META_PATH).with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    tmp_path.replace(META_PATH)


def _normalize_source_id(name: str) -> str:
    """
    Chuẩn hóa source_id từ tên video hoặc id do FE truyền vào.
    Áp dụng logic tương tự _normalize_video_stem ở memory_tree.
    """
    from unicodedata import normalize
    name = (name or "").strip()
    if not name:
        return ""
    # Nếu là path, chỉ lấy tên file
    if "/" in name or "\\" in name:
        name = os.path.basename(name)
    # Bỏ phần mở rộng nếu có
    if "." in name:
        name = os.path.splitext(name)[0]
    cleaned = normalize("NFKD", name).replace("\u00a0", " ")
    # Bỏ suffix dạng timestamp _YYYYMMDD_HHMMSS nếu có
    import re
    cleaned = re.sub(r"_\d{8}_\d{6}$", "", cleaned)
    return cleaned.strip().lower()


def _load_index(dim: int):
    """Luôn dùng IndexIDMap để giữ id"""
    if os.path.exists(INDEX_PATH):
        idx = faiss.read_index(INDEX_PATH)
        # Nếu index đang không phải IDMap thì rebuild lại
        if not isinstance(idx, faiss.IndexIDMap):
            base = faiss.IndexFlatL2(dim)
            new_idx = faiss.IndexIDMap(base)
            xb = idx.reconstruct_n(0, idx.ntotal)
            ids = np.arange(idx.ntotal, dtype="int64")
            new_idx.add_with_ids(xb, ids)
            return new_idx
        return idx
    else:
        base = faiss.IndexFlatL2(dim)
        return faiss.IndexIDMap(base)

# ===== Public API =====
def append_to_index(chunks: List[str], video_name: str = "", custom_metadata: List[Dict] = None, batch_size: int = 32):
    """
    Append chunks vào FAISS index với batch embedding để tối ưu tốc độ.
    
    Args:
        chunks: List các text chunks
        video_name: Tên video/source
        custom_metadata: List metadata tương ứng với từng chunk
        batch_size: Kích thước batch cho embedding (default: 32)
    """
    if not chunks:
        return

    # Batch embedding để tối ưu tốc độ (giữ batch_size cố định)
    all_embeds = []
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        batch_embeds = _model.encode(
            batch,
            convert_to_numpy=True,
            batch_size=batch_size,
            show_progress_bar=False
        ).astype("float32")
        all_embeds.append(batch_embeds)

    embeds = np.vstack(all_embeds) if len(all_embeds) > 1 else all_embeds[0]
    dim = embeds.shape[1]

    meta = _load_meta()

    # Tìm id tiếp theo
    existing_ids: List[int] = []
    for k in (meta or {}).keys():
        if isinstance(k, str) and k.isdigit():
            existing_ids.append(int(k))
    next_id = max(existing_ids) + 1 if existing_ids else 0
    ids = np.arange(next_id, next_id + len(chunks), dtype="int64")

    # Load hoặc tạo index
    idx = _load_index(dim)
    idx.add_with_ids(embeds, ids)
    faiss.write_index(idx, INDEX_PATH)

    # Cập nhật metadata (chunk_id -> {text, video, ...})
    now = datetime.now().isoformat()
    for i, chunk in enumerate(chunks):
        meta_entry = {
            "text": chunk,
            "video": video_name,
            "timestamp": now,
        }
        if custom_metadata and i < len(custom_metadata):
            meta_entry.update(custom_metadata[i])  # thêm parent_id, sub_order, etc.

        meta[str(int(ids[i]))] = meta_entry

    # Cập nhật metadata versioning để support /stats và tương lai migrations
    num_chunks = sum(1 for k in meta.keys() if isinstance(k, str) and k.isdigit())
    meta["__meta__"] = {
        "version": "1.0",
        "created_at": meta.get("__meta__", {}).get("created_at") or now,
        "num_chunks": num_chunks,
    }
    _save_meta(meta)
    print(f"[INDEX] added {len(chunks)} chunks video={video_name!r} (total={num_chunks})")


def search_index(query: str, k: int = 5) -> List[str]:
    """Tìm kiếm và trả về list text chunks"""
    if not os.path.exists(INDEX_PATH):
        return []

    qv = _model.encode([query], convert_to_numpy=True).astype("float32")
    idx = faiss.read_index(INDEX_PATH)
    D, I = idx.search(qv, k)

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
    """Xóa tất cả chunks liên quan tới 1 video (theo đúng tên video)."""
    meta = _load_meta()
    keep_meta = {k: v for k, v in meta.items() if v.get("video") != video_name}
    _save_meta(keep_meta)

    # Rebuild FAISS từ metadata còn lại
    rebuild_chunk_index(keep_meta)


def delete_chunks_by_source(source_id: str) -> int:
    """
    Xóa tất cả chunks thuộc về 1 source (source_id là ROOT của 1 file upload).
    - Dựa trên trường 'video' trong metadata và chuẩn hóa về cùng dạng source_id.
    - Trả về số chunk bị xóa.
    """
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

    # Lưu lại meta đã filter
    _save_meta(keep_meta)

    # Rebuild FAISS từ metadata còn lại
    rebuild_chunk_index(keep_meta)

    return deleted


def rebuild_chunk_index(existing_meta: Dict[str, Dict] | None = None) -> None:
    """
    Rebuild toàn bộ index/index.faiss từ metadata hiện có.
    - Giữ nguyên id của từng chunk (dùng key trong META làm id).
    - Nếu không còn metadata nào → xóa file index.
    """
    meta = existing_meta if existing_meta is not None else _load_meta()

    if not meta:
        # Không còn metadata -> clear index nếu có
        if os.path.exists(INDEX_PATH):
            os.remove(INDEX_PATH)
        return

    texts: List[str] = []
    ids: List[int] = []
    # Bỏ qua key không phải chunk_id số (vd: "__meta__")
    for k, v in meta.items():
        try:
            if not isinstance(k, str) or not k.isdigit():
                continue
            ids.append(int(k))
            texts.append(v.get("text", ""))
        except ValueError:
            # Bỏ qua key không phải số (phòng trường hợp legacy)
            continue

    if not texts or not ids:
        if os.path.exists(INDEX_PATH):
            os.remove(INDEX_PATH)
        # Nếu còn meta nhưng không có chunk, vẫn cập nhật __meta__ num_chunks=0
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

    # Batch embedding để tối ưu tốc độ rebuild
    batch_size = 32
    all_embeds = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        batch_embeds = _model.encode(
            batch,
            convert_to_numpy=True,
            batch_size=batch_size,
            show_progress_bar=False
        ).astype("float32")
        all_embeds.append(batch_embeds)
    
    embeds = np.vstack(all_embeds) if len(all_embeds) > 1 else all_embeds[0]
    dim = embeds.shape[1]

    base = faiss.IndexFlatL2(dim)
    idx = faiss.IndexIDMap(base)
    idx.add_with_ids(embeds, np.array(ids, dtype="int64"))
    faiss.write_index(idx, INDEX_PATH)

    # Update lại __meta__ (không phá format legacy keys số)
    num_chunks = len(ids)
    meta["__meta__"] = {
        "version": "1.0",
        "created_at": meta.get("__meta__", {}).get("created_at") or datetime.now().isoformat(),
        "num_chunks": num_chunks,
    }
    _save_meta(meta)
    print(f"[INDEX] rebuilt FAISS vectors= {num_chunks}")
