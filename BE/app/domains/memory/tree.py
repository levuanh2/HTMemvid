import json
import os
import re
import unicodedata
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import faiss
import numpy as np
from app.clients.llm_factory import ask_ai, get_embedding_model
from app.domains.vectorstore.store import MODEL_NAME
from app.domains.vectorstore.embedding_utils import normalize_embeddings_array, safe_stack_vectors
from shared.source_id import canonical_source_stem
try:
    from shared.env_loader import load_project_env
    load_project_env(override=False)
except Exception:
    pass

# Chỉ dùng cho local Ollama (Gemini sẽ bỏ qua model).
SLM_MODEL = os.environ.get("SLM_MODEL_CHAT", os.environ.get("SLM_MODEL", "qwen3.6:35b-a3b"))
SLM_MODEL_INTENT = os.environ.get("SLM_MODEL_INTENT", "gemma2:2b")


from shared.paths import BE_ROOT
BASE_DIR = BE_ROOT
# Ưu tiên DATA_DIR=/app trong Docker (volume mount sẽ cung cấp /app/*)
DATA_DIR_DEFAULT = str(BASE_DIR)
DATA_DIR = Path(os.environ.get("DATA_DIR", DATA_DIR_DEFAULT))
MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", str(DATA_DIR / "memory")))
INDEX_DIR = Path(os.environ.get("INDEX_DIR", str(DATA_DIR / "index")))
INDEX_META_PATH = INDEX_DIR / "index.json"

MEMORY_TREES_PATH = MEMORY_DIR / "memory_trees.json"
MEMORY_INDEX_PATH = MEMORY_DIR / "memory_index.faiss"
MEMORY_INDEX_META_PATH = MEMORY_DIR / "memory_index.json"

os.makedirs(MEMORY_DIR, exist_ok=True)


def _require_mem_model():
    """Lazy load embedding model (không load khi import module)."""
    model = get_embedding_model(MODEL_NAME)
    if model is None:
        raise RuntimeError("Embedding model not available (CI mode)")
    return model


@dataclass
class MemoryNode:
    memory_id: str
    type: str  # "document" | "section" | "topic"
    title: str
    summary: str
    embedding: List[float]
    chunk_refs: List[str]
    children: List[str]
    metadata: Dict[str, Any]
    intent_type: str  # "definition" | "procedure" | "argument" | "comparison" | "reference"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_video_stem(name: str) -> str:
    # Dùng chung canonicalizer (shared.source_id) để memory-tree, check_sources_status
    # và retrieval cùng MỘT quy tắc khớp file (kể cả tên có space/dấu/ký tự đặc biệt).
    return canonical_source_stem(name)


def _embed(text: str) -> List[float]:
    """Embed single text (for backward compatibility)."""
    text = (text or "").strip()
    if not text:
        return []
    vec = _require_mem_model().encode([text], convert_to_numpy=True).astype("float32")[0]
    return vec.tolist()


def _embed_batch(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    """
    Batch embedding để tối ưu tốc độ.
    Trả về list embeddings tương ứng với từng text.
    """
    if not texts:
        return []
    
    all_embeds = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        batch_embeds = _require_mem_model().encode(
            batch, 
            convert_to_numpy=True, 
            batch_size=batch_size,
            show_progress_bar=False
        ).astype("float32")
        all_embeds.extend([vec.tolist() for vec in batch_embeds])
    
    return all_embeds


def _classify_intent_type(text: str, title: str = "") -> str:
    """
    Phân loại intent_type của node dựa trên summary/title.
    Trả về một trong: "definition" | "procedure" | "argument" | "comparison" | "reference"
    """
    combined = f"{title}\n{text}".strip()[:1000]  # Giới hạn để LLM nhanh hơn
    
    # Heuristic nhanh trước
    combined_lower = combined.lower()
    if any(kw in combined_lower for kw in ["định nghĩa", "khái niệm", "là gì", "what is", "definition"]):
        return "definition"
    if any(kw in combined_lower for kw in ["bước", "cách", "quy trình", "procedure", "step", "how to"]):
        return "procedure"
    if any(kw in combined_lower for kw in ["so sánh", "khác nhau", "compare", "comparison", "versus"]):
        return "comparison"
    if any(kw in combined_lower for kw in ["tham khảo", "reference", "xem thêm", "see also"]):
        return "reference"
    
    # Nếu heuristic không rõ, dùng LLM nhẹ
    system_prompt = (
        "Bạn là hệ thống phân loại intent cho Memory Tree.\n"
        "Phân loại nội dung này vào 1 trong 5 loại:\n"
        "- definition: Giải thích khái niệm, định nghĩa\n"
        "- procedure: Mô tả các bước, quy trình, cách làm\n"
        "- argument: Lập luận, phân tích, đưa ra quan điểm\n"
        "- comparison: So sánh, đối chiếu\n"
        "- reference: Tham khảo, liên kết\n"
        "Chỉ trả về 1 từ: definition, procedure, argument, comparison, hoặc reference.\n"
        "Không giải thích thêm."
    )
    user_prompt = f"Tiêu đề: {title}\n\nNội dung: {combined[:500]}\n\nLoại intent:"
    
    try:
        result = ask_ai(user_prompt, system_prompt=system_prompt, model=SLM_MODEL_INTENT).strip().lower()
        # Validate kết quả
        valid = {"definition", "procedure", "argument", "comparison", "reference"}
        for v in valid:
            if v in result:
                return v
        # Fallback: mặc định argument nếu không match
        return "argument"
    except Exception as exc:
        print(f"⚠️ Lỗi classify intent_type: {exc}")
        return "argument"  # fallback


def _llm_summarize_for_memory(text: str, level: str) -> str:
    """
    Tóm tắt cho node Memory Tree (document / section / topic).
    """
    text = (text or "").strip()
    if not text:
        return ""

    system_prompt = (
        "Bạn là hệ thống xây dựng CÂY TRÍ NHỚ (Memory Tree) cho dự án MemvidX.\n"
        f"Bạn đang tóm tắt ở cấp: {level}.\n"
        "- Hãy tóm tắt ngắn gọn nhưng đủ ý chính (5-10 câu).\n"
        "- Không thêm thông tin ngoài văn bản gốc.\n"
        "- Ưu tiên các mục tiêu, khái niệm, giải pháp chính.\n"
    )
    user_prompt = f"Văn bản nguồn:\n{text[:6000]}\n\nHãy tóm tắt ở cấp {level}:"
    return ask_ai(user_prompt, system_prompt=system_prompt, model=SLM_MODEL)


def _load_index_meta() -> Dict[str, Any]:
    # Đọc index.json QUA seam VectorStore.load_meta() thay vì mở file trực tiếp —
    # gỡ coupling-file ngầm giữa memory_tree và vector store (vẫn in-process).
    try:
        from app.domains.vectorstore.store import load_meta as _vs_load_meta
        return _vs_load_meta()
    except Exception:
        # Fallback an toàn nếu vector_store không import được vì lý do gì đó.
        if not INDEX_META_PATH.exists():
            return {}
        try:
            with open(INDEX_META_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            print(f"⚠️ Không thể đọc index metadata: {exc}")
            return {}


def _load_memory_trees() -> List[Dict[str, Any]]:
    if not MEMORY_TREES_PATH.exists():
        return []
    try:
        with open(MEMORY_TREES_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception as exc:
        print(f"⚠️ Không thể đọc memory_trees.json: {exc}")
    return []


def _save_memory_trees(trees: List[Dict[str, Any]]) -> None:
    try:
        tmp = MEMORY_TREES_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(trees, f, ensure_ascii=False, indent=2)
        tmp.replace(MEMORY_TREES_PATH)
    except Exception as exc:
        print(f"⚠️ Không thể lưu memory_trees.json: {exc}")


def delete_memory_tree_by_source(source_id: str) -> int:
    """
    Xóa toàn bộ Memory Tree (document + section nodes) cho 1 source.
    - source_id: ROOT đại diện cho 1 file upload (sử dụng logic normalize như FE).
    - Trả về số node bị xóa.
    """
    norm = _normalize_video_stem(source_id)
    trees = _load_memory_trees()
    if not trees:
        return 0

    new_trees: List[Dict[str, Any]] = []
    deleted_nodes = 0

    for t in trees:
        stem = _normalize_video_stem(t.get("source_stem", ""))
        if stem == norm:
            deleted_nodes += len(t.get("nodes", []))
            continue
        new_trees.append(t)

    _save_memory_trees(new_trees)
    return deleted_nodes


def _save_partial_tree(partial_tree: Dict[str, Any]) -> None:
    """
    Lưu partial tree (chỉ có document node) để frontend có thể hiển thị ngay.
    """
    trees = _load_memory_trees()
    # Tìm và thay thế tree cũ hoặc append mới
    source_stem = partial_tree.get("source_stem")
    updated = False
    for i, t in enumerate(trees):
        if t.get("source_stem") == source_stem:
            trees[i] = partial_tree
            updated = True
            break
    if not updated:
        trees.append(partial_tree)
    _save_memory_trees(trees)


def _append_section_to_tree(source_stem: str, section_node: Dict[str, Any]) -> None:
    """
    Append section node vào tree hiện có (incremental update).
    """
    trees = _load_memory_trees()
    for tree in trees:
        if tree.get("source_stem") == source_stem:
            # Thêm section node vào danh sách nodes
            if "nodes" not in tree:
                tree["nodes"] = []
            # Kiểm tra xem section đã có chưa (tránh duplicate)
            existing_ids = {n.get("memory_id") for n in tree.get("nodes", [])}
            if section_node.get("memory_id") not in existing_ids:
                tree["nodes"].append(section_node)
                # Cập nhật children của document node
                doc_node = next((n for n in tree.get("nodes", []) if n.get("type") == "document"), None)
                if doc_node:
                    children = doc_node.get("children", [])
                    sec_id = section_node.get("memory_id")
                    if sec_id and sec_id not in children:
                        children.append(sec_id)
                        doc_node["children"] = children
            _save_memory_trees(trees)
            return
    # Nếu không tìm thấy tree, tạo mới
    new_tree = {
        "tree_id": f"memtree_{source_stem}",
        "source_stem": source_stem,
        "built_at": _now_iso(),
        "version": "1.0",
        "status": "building",
        "nodes": [section_node],
    }
    trees.append(new_tree)
    _save_memory_trees(trees)


def _update_tree_status(source_stem: str, status: str) -> None:
    """
    Cập nhật status của tree (building → completed).
    """
    trees = _load_memory_trees()
    for tree in trees:
        if tree.get("source_stem") == source_stem:
            tree["status"] = status
            _save_memory_trees(trees)
            return


def _join_chunk_text(chunks: List[Dict[str, Any]], max_chars: int = 8000) -> str:
    texts: List[str] = []
    total = 0
    from app.domains.vectorstore import chunk_text_store
    for c in chunks:
        t = (c.get("text") or "").strip()
        if not t and c.get("chunk_id") is not None:
            t = (chunk_text_store.get_text(int(c["chunk_id"])) or "").strip()
        if not t:
            continue
        if total + len(t) > max_chars and texts:
            break
        texts.append(t)
        total += len(t)
    return "\n\n".join(texts)


def _simple_section_group(chunks: List[Dict[str, Any]], max_sections: int = 6) -> List[Dict[str, Any]]:
    """
    Heuristic chia section theo kích thước cố định (tránh gọi LLM cho grouping),
    có thể thay bằng LLM sau này.
    """
    n = len(chunks)
    if n == 0:
        return []
    if n <= max_sections * 3:
        # ít chunk -> 1 section
        return [{
            "title": "Tổng quan tài liệu",
            "chunk_ids": [c.get("chunk_id") or k for c, k in _enumerate_chunks(chunks)]
        }]

    # Chia đều thành ~max_sections phần
    size = max(3, n // max_sections)
    specs: List[Dict[str, Any]] = []
    chunk_ids = [c.get("chunk_id") or str(i) for i, c in enumerate(chunks)]
    for i in range(0, n, size):
        part_ids = chunk_ids[i:i + size]
        if not part_ids:
            continue
        specs.append({
            "title": f"Section {len(specs) + 1}",
            "chunk_ids": part_ids
        })
    return specs


def _enumerate_chunks(chunks: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], str]]:
    out: List[Tuple[Dict[str, Any], str]] = []
    for i, c in enumerate(chunks):
        cid = c.get("chunk_id") or c.get("id") or str(i)
        out.append((c, str(cid)))
    return out


def build_memory_tree_for_sources(source_stems: List[str]) -> Dict[str, Any]:
    """
    Build cây trí nhớ cho 1 hoặc nhiều nguồn (stem đã normalize như FE dùng).
    KHÔNG thay đổi chunk/index hiện có.
    """
    meta = _load_index_meta()
    if not meta:
        return {"error": "No index metadata found"}

    # Chuẩn hóa input
    norm_sources = {_normalize_video_stem(s) for s in source_stems if s}
    if not norm_sources:
        return {"error": "No valid sources"}

    # Gom chunk cho từng source
    chunks_by_src: Dict[str, List[Dict[str, Any]]] = {s: [] for s in norm_sources}
    for key, m in meta.items():
        if not isinstance(key, str) or not key.isdigit():
            continue
        video_raw = (m.get("video") or "").strip()
        if not video_raw:
            continue
        stem = _normalize_video_stem(video_raw)
        if stem in norm_sources:
            # Gắn chunk_id từ key index.json
            m_with_id = dict(m)
            m_with_id["chunk_id"] = m_with_id.get("chunk_id") or key
            chunks_by_src[stem].append(m_with_id)

    trees = _load_memory_trees()
    current_model = _get_current_model_name()
    current_dim = _get_current_embedding_dim()
    new_trees: List[Dict[str, Any]] = []

    # Filter: giữ lại trees KHÔNG trong norm_sources VÀ phải khớp model hiện tại
    kept_trees: List[Dict[str, Any]] = []
    for tree in trees:
        if tree.get("source_stem") in norm_sources:
            # Tree này sẽ được rebuild, bỏ qua
            continue
        # Kiểm tra tree cũ có embedding model/dim khác không
        if not _tree_matches_current_embedding(tree, current_model, current_dim):
            print(f"[memory_tree] Drop legacy tree for {tree.get('source_stem')}: "
                  f"model={tree.get('embedding_model_name', '?')} dim={tree.get('embedding_dim', '?')} "
                  f"!= current model={current_model} dim={current_dim}")
            continue
        kept_trees.append(tree)

    built_for: List[str] = []
    new_trees: List[Dict[str, Any]] = []

    for stem, chunks in chunks_by_src.items():
        if not chunks:
            continue

        # Document node
        chunks_sorted = sorted(chunks, key=lambda c: (c.get("parent_id") or "", c.get("sub_order") or 0))
        doc_id = f"mem_doc_{stem}"
        doc_text = _join_chunk_text(chunks_sorted, max_chars=8000)
        doc_summary = _llm_summarize_for_memory(doc_text, level="document")
        doc_emb = _embed(doc_summary)

        doc_intent = _classify_intent_type(doc_summary, f"Tài liệu: {stem}")
        
        doc_node = MemoryNode(
            memory_id=doc_id,
            type="document",
            title=f"Tài liệu: {stem}",
            summary=doc_summary,
            embedding=doc_emb,
            chunk_refs=[c["chunk_id"] for c in chunks_sorted],
            children=[],
            metadata={
                "source_stem": stem,
                "num_chunks": len(chunks_sorted),
            },
            intent_type=doc_intent,
        )

        nodes: Dict[str, MemoryNode] = {doc_id: doc_node}

        # Bước 1: Lưu document node trước (partial tree với status "building")
        tree_obj = {
            "tree_id": f"memtree_{stem}",
            "source_stem": stem,
            "built_at": _now_iso(),
            "version": "1.0",
            "status": "building",  # building → completed
            "embedding_model_name": _get_current_model_name(),
            "embedding_dim": _get_current_embedding_dim(),
            "nodes": [asdict(doc_node)],
        }
        # Lưu partial tree ngay để frontend có thể hiển thị document node
        _save_partial_tree(tree_obj)
        print(f"📝 [Build] Đã lưu document node cho {stem}")

        # Bước 2: Build section nodes dần dần (với batch embedding)
        section_specs = _simple_section_group(chunks_sorted)
        section_nodes: List[Dict[str, Any]] = []
        
        # Chuẩn bị texts và titles để generate summary
        section_raw_data = []
        for idx, spec in enumerate(section_specs):
            sec_chunks = [c for c in chunks_sorted if c["chunk_id"] in spec["chunk_ids"]]
            if not sec_chunks:
                continue
            sec_text = _join_chunk_text(sec_chunks, max_chars=4000)
            sec_title = spec.get("title") or f"Section {idx + 1}"
            section_raw_data.append((idx, spec, sec_chunks, sec_text, sec_title))

        # Gọi LLM tóm tắt song song để tăng tốc
        section_data = []
        import concurrent.futures
        
        # Hàm worker để tóm tắt
        def _summarize_section(item):
            idx, spec, sec_chunks, sec_text, sec_title = item
            sec_summary = _llm_summarize_for_memory(sec_text, level="section")
            return (idx, spec, sec_chunks, sec_summary, sec_title)

        # Số lượng worker vừa phải để tránh quá tải Ollama local hoặc chạm limit API
        max_workers = min(4, int(os.environ.get("MAX_SUMMARIZE_WORKERS", "3")))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            for result in executor.map(_summarize_section, section_raw_data):
                section_data.append(result)
        
        # Sắp xếp lại theo idx cho chắc chắn vì ThreadPoolExecutor.map trả về theo thứ tự input,
        # nhưng code cũ append tuần tự. Map giữ đúng thứ tự, nên không lo.

        
        # Batch embedding cho tất cả section summaries
        if section_data:
            section_summaries = [data[3] for data in section_data]  # Extract summaries
            batch_sz = int(os.environ.get("EMBED_BATCH_SIZE", "64"))
            section_embeddings = _embed_batch(section_summaries, batch_size=batch_sz)
        else:
            section_embeddings = []
        
        # Tạo section nodes với embeddings đã batch
        for (idx, spec, sec_chunks, sec_summary, sec_title), sec_emb in zip(section_data, section_embeddings):
            sec_id = f"mem_sec_{stem}_{idx}"
            sec_intent = _classify_intent_type(sec_summary, sec_title)
            
            sec_node = MemoryNode(
                memory_id=sec_id,
                type="section",
                title=sec_title,
                summary=sec_summary,
                embedding=sec_emb,
                chunk_refs=[c["chunk_id"] for c in sec_chunks],
                children=[],
                metadata={
                    "source_stem": stem,
                    "section_index": idx,
                },
                intent_type=sec_intent,
            )
            nodes[sec_id] = sec_node
            doc_node.children.append(sec_id)
            section_nodes.append(asdict(sec_node))
            
            # Append section node vào tree ngay (incremental update)
            _append_section_to_tree(stem, asdict(sec_node))
            print(f"📝 [Build] Đã thêm section {idx + 1}/{len(section_specs)} cho {stem}")

        # Bước 3: Cập nhật tree với tất cả nodes và status "completed"
        tree_obj["nodes"] = [asdict(n) for n in nodes.values()]
        tree_obj["status"] = "completed"
        new_trees.append(tree_obj)
        built_for.append(stem)
        
        # Cập nhật lại tree với status completed
        _update_tree_status(stem, "completed")
        print(f"✅ [Build] Hoàn thành build tree cho {stem} ({len(section_nodes)} sections)")

    if not built_for:
        return {"error": "No chunks found for given sources"}

    # CRITICAL FIX: KHÔNG load lại all_trees từ file
    # Dùng kept_trees + new_trees đã được lọc sạch theo current model/dim
    final_trees = kept_trees + new_trees
    _save_memory_trees(final_trees)
    
    # Rebuild memory index với final_trees đã lọc - KHÔNG load lại từ file
    _rebuild_memory_index(final_trees)
    print(f"🔍 [Build] Da rebuild memory index voi {len(final_trees)} trees (kept={len(kept_trees)} + new={len(new_trees)})")
    
    return {"built_for": built_for, "num_trees": len(final_trees)}


def _tree_matches_current_embedding(tree: Dict[str, Any], current_model: str, current_dim: int) -> bool:
    """
    Kiểm tra tree có embedding model và dim khớp với model hiện tại không.
    Trees cũ (legacy) thiếu metadata sẽ bị coi là không khớp.
    """
    tree_model = tree.get("embedding_model_name") or tree.get("model_name") or ""
    tree_dim = tree.get("embedding_dim", 0)

    # Legacy tree: không có metadata
    if not tree_model or tree_dim == 0:
        return False

    # Model name khác
    if tree_model != current_model:
        return False

    # Dimension khác
    if current_dim > 0 and tree_dim != current_dim:
        return False

    return True


def _rebuild_memory_index(trees: List[Dict[str, Any]]) -> None:
    """
    Rebuild toàn bộ memory_index.faiss và metadata từ danh sách trees.
    Bỏ qua nodes có embedding dim không khớp với model hiện tại.
    """
    import logging
    logger = logging.getLogger(__name__)

    current_model = _get_current_model_name()
    current_dim = _get_current_embedding_dim()
    logger.info("[memory_index] rebuild: model=%s dim=%d trees=%d", current_model, current_dim, len(trees))

    vectors: List[np.ndarray] = []
    meta_rows: List[Dict[str, Any]] = []
    skipped_mismatched = 0
    skipped_empty = 0
    skipped_tree_mismatch = 0

    for tree in trees:
        tree_id = tree.get("tree_id")
        source_stem = tree.get("source_stem")

        # Bỏ qua tree có model/dim khác
        if not _tree_matches_current_embedding(tree, current_model, current_dim):
            skipped_tree_mismatch += 1
            logger.warning(
                "[memory_index] skip tree %s (source=%s): model=%s dim=%s != current model=%s dim=%s",
                tree_id, source_stem,
                tree.get("embedding_model_name", "?"),
                tree.get("embedding_dim", "?"),
                current_model, current_dim
            )
            continue

        for node in tree.get("nodes", []):
            emb = node.get("embedding") or []
            if not emb:
                skipped_empty += 1
                continue
            vec = np.asarray(emb, dtype="float32")
            emb_dim = vec.shape[-1] if vec.ndim > 0 else 0
            if current_dim > 0 and emb_dim != current_dim:
                logger.warning(
                    "[memory_index] skip node %s: embedding_dim=%d != current_dim=%d",
                    node.get("memory_id"), emb_dim, current_dim
                )
                skipped_mismatched += 1
                continue
            vectors.append(vec)
            meta_rows.append({
                "memory_id": node["memory_id"],
                "tree_id": tree_id,
                "type": node.get("type"),
                "title": node.get("title"),
                "source_stem": source_stem,
            })

    logger.info("[memory_index] vectors=%d skipped_tree_mismatch=%d skipped_empty=%d skipped_mismatched=%d",
                len(vectors), skipped_tree_mismatch, skipped_empty, skipped_mismatched)

    if not vectors:
        # clear index & meta
        if MEMORY_INDEX_PATH.exists():
            MEMORY_INDEX_PATH.unlink()
        if MEMORY_INDEX_META_PATH.exists():
            MEMORY_INDEX_META_PATH.unlink()
        return

    # Dùng safe_stack_vectors thay vì np.stack trực tiếp
    xb = safe_stack_vectors(vectors, expected_dim=current_dim if current_dim > 0 else None, context="memory_index_rebuild")
    if xb is None:
        logger.warning("[memory_index] no valid vectors after filtering")
        if MEMORY_INDEX_PATH.exists():
            MEMORY_INDEX_PATH.unlink()
        if MEMORY_INDEX_META_PATH.exists():
            MEMORY_INDEX_META_PATH.unlink()
        return

    logger.info("[memory_index] stacked shape=%s", xb.shape)
    dim = xb.shape[1]

    index = faiss.IndexFlatL2(dim)
    index.add(xb)
    faiss.write_index(index, str(MEMORY_INDEX_PATH))

    meta_obj = {
        "index_built_at": _now_iso(),
        "dim": dim,
        "nodes": meta_rows,
        "embedding_model_name": current_model,
        "embedding_dim": current_dim,
    }
    with open(MEMORY_INDEX_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta_obj, f, ensure_ascii=False, indent=2)
    logger.info("[memory_index] saved: dim=%d vectors=%d", dim, len(vectors))


def rebuild_memory_index() -> None:
    """
    Public API: Rebuild toàn bộ memory_index.{faiss,json} từ memory_trees.json hiện tại.
    Dùng sau các thao tác xóa / thay đổi Memory Tree.
    """
    trees = _load_memory_trees()
    _rebuild_memory_index(trees)


def _get_current_embedding_dim() -> int:
    """Lấy embedding dimension thực tế từ model hiện tại (tránh hard-code)."""
    model = _require_mem_model()
    dummy = model.encode(["dimension_check"], convert_to_numpy=True, show_progress_bar=False)
    return int(dummy.shape[1])


def _get_current_model_name() -> str:
    """Lấy embedding model name hiện tại."""
    return os.environ.get("EMBEDDING_MODEL_NAME", MODEL_NAME)


def _load_memory_index():
    """
    Load memory index với validation model/dim.
    Nếu mismatch thì bỏ qua (return None) để trigger rebuild.
    """
    if os.getenv("SKIP_MODEL_LOAD") == "1":
        return None, None
    if not MEMORY_INDEX_PATH.exists() or not MEMORY_INDEX_META_PATH.exists():
        return None, None
    try:
        index = faiss.read_index(str(MEMORY_INDEX_PATH))
        with open(MEMORY_INDEX_META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
        
        stored_dim = meta.get("dim", 0)
        stored_model = meta.get("embedding_model_name", "")
        current_model = _get_current_model_name()
        current_dim = _get_current_embedding_dim()
        
        # Check dimension mismatch
        if stored_dim and stored_dim != index.d:
            print(
                f"[MEMORY] Dimension mismatch: index có {index.d}, model yêu cầu {stored_dim}. "
                f"Sẽ rebuild memory_index."
            )
            return None, None
        
        # Check model name mismatch
        if stored_model and stored_model != current_model:
            print(
                f"[MEMORY] Model mismatch: index dùng '{stored_model}', "
                f"hiện tại là '{current_model}'. Sẽ rebuild memory_index."
            )
            return None, None
        
        return index, meta
    except Exception as exc:
        print(f"⚠️ Không thể đọc memory_index: {exc}")
        return None, None


# =========================
# Query routing: classify query type
# =========================

def _classify_query_type(query: str) -> str:
    """
    Phân loại query_type chỉ bằng heuristic / regex (~1ms). Không gọi LLM.
    Trả về một trong:
      "overview" | "main_points" | "detail" | "how" | "why" | "compare" | "locate" | "fact"
    """
    import logging
    import re

    logger = logging.getLogger(__name__)
    q = (query or "").strip()
    q_lower = q.lower()

    # UI phổ biến
    if "file này là gì" in q_lower or "tài liệu này là gì" in q_lower:
        out = "overview"
        logger.info("[classify_query_type] %s q=%r", out, q[:120])
        return out
    if "nội dung chính" in q_lower or "ý chính" in q_lower:
        out = "main_points"
        logger.info("[classify_query_type] %s q=%r", out, q[:120])
        return out
    if "chi tiết hơn" in q_lower:
        out = "detail"
        logger.info("[classify_query_type] %s q=%r", out, q[:120])
        return out

    overview_kw = ["tóm tắt", "tổng quan", "overview", "khái quát", "giới thiệu", "summary"]
    main_kw = ["ý chính", "nội dung chính", "main points", "bullet", "điểm chính"]
    detail_kw = ["chi tiết", "detail", "phân tích kỹ", "cụ thể", "elaborate"]
    locate_kw = ["ở đâu", "nằm ở", "vị trí", "locate", "where", "position", "trang nào", "mục nào"]
    how_kw = ["như thế nào", "cách ", "how ", "làm sao", "quy trình", "how to"]
    compare_kw = ["so sánh", "khác nhau", "compare", "versus", "đối chiếu", "khác biệt"]
    why_kw = ["tại sao", "vì sao", "why ", "why?", "lý do", "nguyên nhân", "explain why"]

    if any(kw in q_lower for kw in overview_kw):
        out = "overview"
    elif any(kw in q_lower for kw in main_kw):
        out = "main_points"
    elif any(kw in q_lower for kw in detail_kw):
        out = "detail"
    elif any(kw in q_lower for kw in locate_kw):
        out = "locate"
    elif any(kw in q_lower for kw in how_kw):
        out = "how"
    elif any(kw in q_lower for kw in compare_kw):
        out = "compare"
    elif any(kw in q_lower for kw in why_kw):
        out = "why"
    elif re.search(r"\b(là gì|bao nhiêu|khi nào|ai là|có phải|thông tin)\b", q_lower):
        out = "fact"
    elif re.search(r"\b(what is|how many|when |who is|is it true)\b", q_lower):
        out = "fact"
    elif re.search(r"\b(why|explain|analyze|evaluate)\b", q_lower):
        out = "why"
    else:
        out = "fact"

    logger.info("[classify_query_type] %s q=%r", out, q[:120])
    return out


# =========================
# UX helpers: build clean context & answer
# =========================

def build_human_context(top_nodes: List[Dict[str, Any]], evidence_chunks: List[Dict[str, Any]], max_len: int = 4000) -> str:
    """
    Tạo context sạch, không lộ metadata kỹ thuật.
    - Dùng summary từ node (đã là ý chính).
    - Thêm vài trích đoạn ngắn từ evidence.
    """
    summaries = []
    for n in top_nodes:
        s = (n.get("summary") or "").strip()
        if s:
            summaries.append(s)

    snippets = []
    total = 0
    from app.domains.vectorstore import chunk_text_store
    for c in evidence_chunks:
        t = (c.get("text") or "").strip()
        if not t and c.get("chunk_id") is not None:
            t = (chunk_text_store.get_text(int(c["chunk_id"])) or "").strip()
        if not t:
            continue
        snippet = t[:400]
        if total + len(snippet) > max_len and snippets:
            break
        snippets.append(snippet)
        total += len(snippet)

    parts = []
    if summaries:
        parts.append("Tài liệu đề cập đến:\n- " + "\n- ".join(summaries))
    if snippets:
        parts.append("Một vài trích đoạn:\n- " + "\n- ".join(snippets[:5]))

    return "\n\n".join(parts) if parts else ""


def generate_notebooklm_style_answer(question: str, human_context: str, intent_type: Optional[str] = None) -> str:
    """
    Generate answer theo phong cách NotebookLM: giải thích lại nội dung tài liệu cho người dùng.
    """
    # Base system prompt
    system_prompt = (
        "Bạn là người đã đọc, hiểu và ghi chú toàn bộ nội dung tài liệu thay cho người dùng.\n\n"
        
        "Bạn KHÔNG giới thiệu vai trò của mình.\n"
        "Bạn KHÔNG nói về cách bạn trả lời.\n"
        "Bạn CHỈ nói về nội dung, như một người vừa đọc xong và đang giải thích lại.\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "CÁCH SUY NGHĨ (KHÔNG ĐƯỢC VIẾT RA):\n\n"
        
        "- Người dùng đang hỏi để làm gì?\n"
        "- Họ muốn nghe một câu trả lời TỰ NHIÊN như người thật nói chuyện\n"
        "- Cấu trúc câu trả lời phải mượt, liền mạch, không lộ khung\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "CÁCH VIẾT BẮT BUỘC:\n\n"
        
        "- BẮT ĐẦU TRỰC TIẾP vào nội dung, KHÔNG mở đầu chung chung\n"
        "- Viết như đang kể lại, giải thích, hoặc tóm tắt cho một người khác\n"
        "- Ý phải nối tiếp nhau, không rời rạc\n"
        "- Không dùng bullet trừ khi bắt buộc (quy trình, các bước)\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "XỬ LÝ CÂU HỎI CHUNG CHUNG (VD: \"file này là gì\", \"này nói gì\"):\n\n"
        
        "- Trả lời gọn ý chính trước\n"
        "- Sau đó diễn giải thêm để người nghe hiểu bản chất\n"
        "- Nếu có kiến thức nền → lồng vào tự nhiên, không dạy đời\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "VỚI NỘI DUNG KỸ THUẬT:\n\n"
        
        "- Giải thích theo kiểu \"ý tưởng là…\"\n"
        "- So sánh với ví dụ đời thường nếu hợp lý\n"
        "- Tránh thuật ngữ, hoặc giải thích ngay khi dùng\n\n"
    )
    
    # Thêm intent-specific guidance (ngầm, không để lộ)
    if intent_type == "definition":
        system_prompt += "Lưu ý: Câu hỏi về định nghĩa/khái niệm. Giải thích bằng lời đơn giản, có ví dụ nếu phù hợp.\n\n"
    elif intent_type == "procedure":
        system_prompt += "Lưu ý: Câu hỏi về quy trình/cách làm. Mô tả theo trình tự logic, có thể chia bước nếu cần.\n\n"
    elif intent_type == "argument":
        system_prompt += "Lưu ý: Câu hỏi về lập luận/phân tích. Trình bày lập luận chính và lý do một cách liền mạch.\n\n"
    elif intent_type == "comparison":
        system_prompt += "Lưu ý: Câu hỏi về so sánh. Đối chiếu rõ điểm giống và khác.\n\n"
    elif intent_type == "reference":
        system_prompt += "Lưu ý: Câu hỏi về tham khảo. Đưa thông tin chính xác, rõ ràng.\n\n"
    
    system_prompt += (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "GỢI Ý HỎI TIẾP:\n\n"
        
        "- CHỈ thêm nếu thật sự hợp lý\n"
        "- Viết như một câu nói thêm, không phải lời mời gọi máy móc\n"
        "- Ví dụ: \"Nếu bạn muốn đào sâu hơn phần này, mình có thể giải thích kỹ hơn.\"\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "CẤM TUYỆT ĐỐI:\n\n"
        
        "- Không nói \"bạn đang muốn…\"\n"
        "- Không nói \"dưới đây là…\"\n"
        "- Không nói \"tài liệu đề cập…\"\n"
        "- Không liệt kê kiểu slide\n"
        "- Không để lộ Answer Mode, intent, hay cấu trúc suy nghĩ\n"
        "- Không copy nguyên văn\n"
        "- Không nói mình là AI hay LLM\n"
        "- Không nhắc chunk, node, embedding, tìm kiếm\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "NGÔN NGỮ:\n\n"
        
        "- Luôn là tiếng Việt\n"
        "- Tự nhiên, giống người thật\n"
        "- Giống trợ lý nghiên cứu cá nhân, không giống chatbot\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "OUTPUT:\n"
        "- Chỉ trả về đoạn trả lời cho người dùng\n"
        "- Văn xuôi, mạch lạc\n"
        "- Nếu tài liệu không đủ thông tin, nói ngắn gọn và tự nhiên"
    )
    
    user_prompt = (
        f"Câu hỏi: {question}\n\n"
        f"Nội dung liên quan từ tài liệu:\n{human_context}\n\n"
        "Hãy trả lời trực tiếp vào câu hỏi, viết như đang giải thích lại cho người khác một cách tự nhiên, mạch lạc."
    )
    
    # feature='answer' → factual temp (≈0): đây là sinh đáp án grounded trên tài liệu,
    # giữ nhất quán với GenerateAnswer/summarize_results (giảm bịa đặt).
    return ask_ai(user_prompt, system_prompt=system_prompt, model=SLM_MODEL, feature="answer")


def query_with_memory_tree(query: str, selected_sources: Optional[List[str]] = None, top_k: int = 5) -> Optional[Dict[str, Any]]:
    """
    Query theo pipeline với query routing:
      1) Phân loại query_type (overview | main_points | detail | how | why | compare | locate | fact)
      2) Dựa trên query_type chọn strategy retrieval
      3) Embed query & search memory_index
      4) Lấy memory node phù hợp nhất
      5) Load chunk_refs làm evidence
      6) Gọi LLM trả lời

    Trả về dict giống dạng:
    {
      "answer": "...",
      "memory_nodes": [...],
      "evidence_chunk_ids": [...],
      "query_type": "...",
    }

    Nếu query_type = "locate", trả về None để fallback sang chunk-level search.
    """
    q = (query or "").strip()
    if not q:
        return None

    # Bước 1: Phân loại query_type
    query_type = _classify_query_type(q)
    print(f"🔍 Query type: {query_type}")

    # Bước 2: Routing strategy
    # locate → fallback chunk-level search trực tiếp
    if query_type == "locate":
        return None  # Fallback sang chunk-level search

    # Điều chỉnh top_k và filter node type dựa trên query_type
    strategy_top_k = top_k
    preferred_node_type: Optional[str] = None
    
    if query_type == "overview":
        # Ưu tiên document-level node cho câu hỏi kiểu "file này là gì"
        preferred_node_type = "document"
        strategy_top_k = min(top_k, 3)  # Ít node hơn, tập trung document
    elif query_type == "main_points":
        # Lấy cả document và section summary cho "nội dung chính"
        preferred_node_type = None
        strategy_top_k = top_k + 2
    elif query_type == "detail":
        # Đi sâu chi tiết: ưu tiên section + nhiều chunk
        preferred_node_type = "section"
        strategy_top_k = top_k + 3
    elif query_type == "fact":
        # Ưu tiên section node
        preferred_node_type = "section"
        strategy_top_k = top_k
    elif query_type == "how":
        # Section + nhiều chunk
        preferred_node_type = "section"
        strategy_top_k = top_k + 2  # Lấy thêm node để có nhiều chunk
    elif query_type == "compare":
        # Nhiều section node
        preferred_node_type = "section"
        strategy_top_k = top_k + 3  # Lấy nhiều section để so sánh
    elif query_type == "why":
        # Section hoặc document đều được
        strategy_top_k = top_k

    idx, meta = _load_memory_index()
    if idx is None or meta is None:
        return None

    nodes_meta = meta.get("nodes") or []
    if not nodes_meta:
        return None

    # Filter theo selected_sources nếu có
    allowed_sources: Optional[set] = None
    if selected_sources:
        allowed_sources = {_normalize_video_stem(s) for s in selected_sources}

    # Build mask (có thể filter theo node type nếu có preferred_node_type)
    filtered_indices: List[int] = []
    for i, row in enumerate(nodes_meta):
        stem = _normalize_video_stem(row.get("source_stem"))
        if allowed_sources is not None and stem not in allowed_sources:
            continue
        # Filter theo node type nếu có yêu cầu
        if preferred_node_type:
            node_type = row.get("type", "")
            if node_type != preferred_node_type:
                continue
        filtered_indices.append(i)

    if not filtered_indices:
        # Nếu không có node type phù hợp, bỏ filter type và thử lại
        if preferred_node_type:
            filtered_indices = []
            for i, row in enumerate(nodes_meta):
                stem = _normalize_video_stem(row.get("source_stem"))
                if allowed_sources is not None and stem not in allowed_sources:
                    continue
                filtered_indices.append(i)
        if not filtered_indices:
            return None

    # Embed query
    qv = _require_mem_model().encode([q], convert_to_numpy=True).astype("float32")

    # Search với top_k lớn hơn để có nhiều candidate
    search_k = min(strategy_top_k * 3, len(nodes_meta))
    D, I = idx.search(qv, search_k)

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for score, idx_id in zip(D[0], I[0]):
        if idx_id < 0 or idx_id >= len(nodes_meta):
            continue
        row = nodes_meta[idx_id]
        if allowed_sources is not None:
            stem = _normalize_video_stem(row.get("source_stem"))
            if stem not in allowed_sources:
                continue
        # Filter theo node type nếu có (lần 2, sau khi search)
        if preferred_node_type:
            node_type = row.get("type", "")
            if node_type != preferred_node_type:
                continue
        scored.append((float(score), row))

    if not scored:
        return None

    # Lấy top_k theo strategy
    scored.sort(key=lambda x: x[0])
    scored = [(1.0 / (1.0 + d), r) for d, r in scored]  # chuyển thành similarity
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:strategy_top_k]

    # Load trees & node map
    trees = _load_memory_trees()
    node_map: Dict[str, Dict[str, Any]] = {}
    for t in trees:
        for n in t.get("nodes", []):
            node_map[n["memory_id"]] = n

    top_nodes: List[Dict[str, Any]] = []
    evidence_ids: List[str] = []
    evidence_seen: set[str] = set()

    for score, row in top:
        mid = row["memory_id"]
        node = node_map.get(mid)
        if not node:
            continue
        node_view = {
            "memory_id": node["memory_id"],
            "type": node.get("type"),
            "title": node.get("title"),
            "summary": node.get("summary"),
            "score": score,
            "source_stem": row.get("source_stem"),
            "children": node.get("children", []),
        }
        top_nodes.append(node_view)
        for cid in node.get("chunk_refs", []):
            if cid is None:
                continue
            cid_str = str(cid)
            if not cid_str:
                continue
            if cid_str in evidence_seen:
                continue
            evidence_seen.add(cid_str)
            evidence_ids.append(cid_str)

    if not top_nodes or not evidence_ids:
        return None

    # Load evidence text từ index/index.json
    meta_chunks = _load_index_meta()
    evidence_chunks: List[Dict[str, Any]] = []
    for cid in evidence_ids:
        if cid in meta_chunks:
            c = dict(meta_chunks[cid])
            c["chunk_id"] = cid
            evidence_chunks.append(c)

    if not evidence_chunks:
        return None

    # Điều chỉnh max_len cho evidence dựa trên query_type
    evidence_max_len = 4000
    if query_type in ("detail", "how"):
        evidence_max_len = 6000  # Lấy nhiều chunk hơn cho câu hỏi chi tiết / how-to
    elif query_type == "compare":
        evidence_max_len = 5000  # Lấy nhiều chunk để so sánh
    elif query_type == "overview":
        evidence_max_len = 3000  # Tổng quan: context gọn hơn
    
    human_context = build_human_context(top_nodes, evidence_chunks, max_len=evidence_max_len)
    # Lấy intent_type từ node_map (ưu tiên node đầu tiên)
    primary_intent = None
    if top_nodes and len(top_nodes) > 0:
        first_node_id = top_nodes[0].get("memory_id")
        if first_node_id and first_node_id in node_map:
            primary_intent = node_map[first_node_id].get("intent_type")
    
    answer = generate_notebooklm_style_answer(query, human_context, intent_type=primary_intent)

    return {
        "answer": answer,
        "memory_nodes": top_nodes,
        "evidence_chunk_ids": evidence_ids,
        "query_type": query_type,
    }


def build_human_context(top_nodes: List[Dict[str, Any]], evidence_chunks: List[Dict[str, Any]], max_len: int = 4000) -> str:
    """
    Làm sạch context gửi vào LLM: ý chính + trích đoạn ngắn, không lộ metadata kỹ thuật.
    """
    main_points: List[str] = []
    for n in top_nodes:
        summ = (n.get("summary") or "").strip()
        if summ:
            main_points.append(summ)

    snippets: List[str] = []
    total = 0
    from app.domains.vectorstore import chunk_text_store
    for c in evidence_chunks:
        t = (c.get("text") or "").strip()
        if not t and c.get("chunk_id") is not None:
            t = (chunk_text_store.get_text(int(c["chunk_id"])) or "").strip()
        if not t:
            continue
        snippet = t[:400]
        if total + len(snippet) > max_len and snippets:
            break
        snippets.append(snippet)
        total += len(snippet)

    parts: List[str] = []
    if main_points:
        parts.append("Ý chính tài liệu:\n- " + "\n- ".join(main_points[:5]))

    if snippets:
        parts.append("Trích đoạn liên quan:\n- " + "\n- ".join(snippets[:5]))

    return "\n\n".join(parts)
