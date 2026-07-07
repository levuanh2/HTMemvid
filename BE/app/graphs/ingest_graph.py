from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock
from langgraph.graph import END, StateGraph

from app.graphs.logger import _Timer, log_node_event
from app.graphs.sqlite_checkpointer import sqlite_saver_from_path
from app.graphs.state import IngestState
from shared.source_id import canonical_source_stem

def build_ingest_graph(
    *,
    update_source_status: Callable[..., None],
    data_dir: Path,
    extract_text: Callable[[str], str],
    split_text: Callable[[str], list[str]],
    process_and_store_chunks: Callable[..., Any],
    append_to_index: Callable[..., None],
    build_memory_tree_for_sources: Callable[[list[str]], None],
    jobs_update: Callable[..., None] | None = None,
) -> Any:
    """
    Build LangGraph ingest pipeline.
    - Giữ nguyên hành vi status lifecycle: processing → index_ready → ready/error
    - Ghi logs local vào logs.sqlite
    """

    def _set_job(job_id: str, **kw: Any) -> None:
        if jobs_update is None:
            return
        try:
            jobs_update(job_id, **kw)
        except Exception:
            pass

    def extract_text_node(state: dict) -> dict:
        t = _Timer()
        try:
            update_source_status(state["source_id"], "processing", progress=0.1)
            _set_job(state["job_id"], status="running", progress=10, current_node="ExtractText")
            use_lc = (os.getenv("USE_LC_INGEST", "1") or "").strip().lower() not in ("0", "false", "no", "off")
            if use_lc:
                from app.domains.ingest.document_loader import load_document
                raw_docs = load_document(state["file_path"])
                if not raw_docs:
                    raise ValueError("Cannot read file content")
                stem = Path(state["file_path"]).stem
                for doc in raw_docs:
                    doc.metadata.setdefault("source", stem)
                    doc.metadata["file_path"] = state["file_path"]
                text = "\n\n".join(d.page_content for d in raw_docs)
                if not (text or "").strip():
                    raise ValueError("Cannot read file content")
                log_node_event(state["job_id"], "ExtractText", "ok", t.ms(), {"chars": len(text), "lc_docs": len(raw_docs)})
                return {
                    **state,
                    "text": text,
                    "raw_docs": raw_docs,
                    "progress": 10,
                    "current_node": "ExtractText",
                    "error": None,
                }
            text = extract_text(state["file_path"])
            if not (text or "").strip():
                raise ValueError("Cannot read file content")
            log_node_event(state["job_id"], "ExtractText", "ok", t.ms(), {"chars": len(text)})
            return {**state, "text": text, "progress": 10, "current_node": "ExtractText", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "ExtractText", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "ExtractText"}

    def normalize_node(state: dict) -> dict:
        """Raw -> Markdown (giữ heading/bảng) + làm sạch + lưu .md artifact.
        Lỗi ở đây KHÔNG chặn pipeline: để trống markdown -> chunk fallback text cũ."""
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=20, current_node="Normalize")
            from shared.config import get_settings
            s = get_settings()
            markdown = ""
            md_path = ""
            if s.use_markdown_ingest:
                try:
                    from app.domains.ingest.markdown_convert import convert_and_save
                    from app.domains.ingest.clean import clean_markdown
                    raw_md, md_path = convert_and_save(state["file_path"], md_dir=s.md_dir or None)
                    markdown = clean_markdown(raw_md, source=state.get("filename"))
                except Exception as exc:
                    log_node_event(state["job_id"], "Normalize", "warn", t.ms(), {"fallback": str(exc)})
                    markdown = ""
            log_node_event(state["job_id"], "Normalize", "ok", t.ms(), {"md_chars": len(markdown)})
            return {**state, "markdown": markdown, "md_path": md_path, "progress": 20, "current_node": "Normalize", "error": None}
        except Exception:
            return {**state, "markdown": "", "progress": 20, "current_node": "Normalize", "error": None}

    def chunk_node(state: dict) -> dict:
        t = _Timer()
        try:
            update_source_status(state["source_id"], "processing", progress=0.3)
            _set_job(state["job_id"], progress=30, current_node="Chunk")
            from shared.config import get_settings
            s = get_settings()
            use_lc = (os.getenv("USE_LC_INGEST", "1") or "").strip().lower() not in ("0", "false", "no", "off")
            markdown = state.get("markdown") or ""
            chunk_headings: list[str] = []
            doc_text = ""           # hệ toạ độ char cho late chunking (chỉ nhánh markdown)
            spans: list = []        # span (start,end) trong doc_text, aligned với chunks

            if markdown and s.chunk_strategy == "markdown_header":
                # Structured: cắt theo heading; Enriched: contextual + hypo-QA (gate trong enrich)
                from app.domains.ingest.chunking import chunk_markdown_spans
                from app.domains.ingest import enrich
                doc_text, pieces = chunk_markdown_spans(markdown)
                doc_context = doc_text[:2000]
                chunks = []
                for p in pieces:
                    txt = enrich.contextualize(p["text"], doc_context)
                    qa = enrich.hypothetical_qa(p["text"])
                    if qa:
                        txt = txt + "\n\n" + qa
                    if txt.strip():
                        chunks.append(txt)
                        chunk_headings.append(p.get("heading_path", ""))
                        spans.append((p.get("start", -1), p.get("end", -1)))
            elif use_lc and state.get("raw_docs"):
                from app.domains.ingest.document_loader import split_documents
                chunk_size = int(os.getenv("CHUNK_SIZE", "500"))
                chunk_overlap = int(os.getenv("CHUNK_OVERLAP", "50"))
                lc_chunks = split_documents(state["raw_docs"], chunk_size, chunk_overlap)
                chunks = [c.page_content.strip() for c in lc_chunks if (c.page_content or "").strip()]
            else:
                chunks = split_text(state["text"])

            if not chunks:
                raise ValueError("No chunks generated")

            # Doc-level metadata (rẻ, không cần LLM): source/category/date/language — áp cho mọi chunk.
            doc_meta: dict[str, Any] = {}
            if s.enrich_metadata:
                from app.domains.ingest import enrich as _enrich
                dm = _enrich.attach_metadata(
                    state.get("text") or chunks[0],
                    source=state.get("filename") or "",
                    file_path=state.get("file_path"),
                )
                dm.pop("heading_path", None)
                dm.pop("page", None)
                doc_meta = dm

            # LATE CHUNKING: embed token TOÀN VĂN (doc_text) rồi mean-pool theo span →
            # mỗi vector "thấm" ngữ cảnh toàn cục. Chỉ nhánh markdown (có doc_text+spans).
            # CI/lỗi/encoder không sẵn → bỏ qua (late_embeddings=None) → EmbedAndIndex tự encode.
            late_embeddings = None
            if chunks and spans and len(spans) == len(chunks) and os.getenv("SKIP_MODEL_LOAD") != "1":
                try:
                    from app.domains.ingest.late_chunk import get_late_chunk_encoder
                    enc = get_late_chunk_encoder()
                    enc.warmup()  # nạp model NGOÀI mọi timeout (bài học playbook)
                    safe_spans = [(max(s0, 0), max(e0, 0)) for (s0, e0) in spans]
                    arr = enc.embed_document(doc_text, safe_spans)
                    # piece không định vị được (start<0) → fallback embed standalone (tránh vector 0)
                    for i, (s0, e0) in enumerate(spans):
                        if s0 < 0 or e0 <= s0:
                            arr[i] = enc.embed_query(chunks[i])[0]
                    late_embeddings = [row.tolist() for row in arr]
                    log_node_event(state["job_id"], "Chunk", "late_chunk_ok", t.ms(), {"vecs": len(late_embeddings)})
                except Exception as le:
                    log_node_event(state["job_id"], "Chunk", "late_chunk_skip", t.ms(), {"reason": str(le)})
                    late_embeddings = None

            log_node_event(state["job_id"], "Chunk", "ok", t.ms(), {"chunks": len(chunks), "md": bool(markdown)})
            return {
                **state,
                "chunks": chunks,
                "chunk_headings": chunk_headings,
                "doc_meta": doc_meta,
                "late_embeddings": late_embeddings,
                "progress": 30,
                "current_node": "Chunk",
                "error": None,
            }
        except Exception as e:
            log_node_event(state["job_id"], "Chunk", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "Chunk"}

    def process_chunks_node(state: dict) -> dict:
        t = _Timer()
        try:
            update_source_status(state["source_id"], "processing", progress=0.4)
            _set_job(state["job_id"], progress=55, current_node="ProcessChunks")
            video_name = f"{state['filename'].replace('.', '_')}"
            timestamp = datetime.now().isoformat()
            video_path, metadata_entries = process_and_store_chunks(
                chunks=state["chunks"],
                video_name=video_name,
                timestamp=timestamp,
            )
            log_node_event(state["job_id"], "ProcessChunks", "ok", t.ms(), {"frames": len(metadata_entries)})
            return {
                **state,
                "video_name": video_name,
                "video_path": video_path,
                "metadata_entries": metadata_entries,
                "progress": 55,
                "current_node": "ProcessChunks",
                "error": None,
            }
        except Exception as e:
            log_node_event(state["job_id"], "ProcessChunks", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "ProcessChunks"}

    def embed_index_node(state: dict) -> dict:
        t = _Timer()
        try:
            update_source_status(state["source_id"], "processing", progress=0.5)
            _set_job(state["job_id"], progress=75, current_node="EmbedAndIndex")

            entries = state["metadata_entries"]
            all_chunks = [entry["text"] for entry in entries]
            doc_meta = state.get("doc_meta") or {}
            headings = state.get("chunk_headings") or []
            # Map heading qua entry["chunk_index"] (index chunk CHA — chunk_processor gắn
            # cho mọi entry kể cả sub-split, cùng cơ chế late_embeddings dùng). Cách cũ
            # `len(headings)==len(entries)` vỡ khi QR sub-split → rớt TOÀN BỘ heading.
            aligned = len(headings) == len(entries)  # fallback cho entry thiếu chunk_index

            def _heading_for(i: int, entry: dict) -> str:
                ci = entry.get("chunk_index")
                if headings and ci is not None and 0 <= ci < len(headings):
                    return headings[ci]
                return headings[i] if aligned else ""
            # Định danh canonical ghi thẳng vào metadata để retrieval khớp CHÍNH XÁC
            # (không phải tái dựng từ video_path đã sanitize).
            src_stem = canonical_source_stem(state["filename"])
            all_metadata = []
            for i, entry in enumerate(entries):
                md = {
                    "parent_id": entry.get("parent_id"),
                    "sub_order": entry.get("sub_order"),
                    "total_parts": entry.get("total_parts"),
                    "is_subchunk": entry.get("is_subchunk", False),
                    "source_stem": src_stem,
                    "source_id": state.get("source_id"),
                    "video": state.get("video_path") or "",
                    "frame_index": entry.get("frame_index"),
                }
                if doc_meta:
                    md.update(doc_meta)  # source/category/date/language (doc-level)
                hp = _heading_for(i, entry)
                if hp:
                    md["heading_path"] = hp
                all_metadata.append(md)

            # LATE CHUNKING: lấy lại vector của CHUNK gốc cho mỗi entry (entry có thể bị
            # sub-split bởi QR processor → dùng chung vector của chunk cha qua chunk_index).
            late = state.get("late_embeddings")
            embeddings = None
            if late:
                try:
                    import numpy as _np
                    rows = []
                    for entry in entries:
                        ci = entry.get("chunk_index")
                        if ci is None or not (0 <= ci < len(late)):
                            rows = None
                            break
                        rows.append(late[ci])
                    if rows is not None:
                        embeddings = _np.asarray(rows, dtype="float32")
                except Exception as ee:
                    log_node_event(state["job_id"], "EmbedAndIndex", "late_map_skip", t.ms(), {"reason": str(ee)})
                    embeddings = None

            if embeddings is not None:
                append_to_index(
                    chunks=all_chunks,
                    video_name=state["video_path"],
                    custom_metadata=all_metadata,
                    batch_size=32,
                    embeddings=embeddings,
                )
            else:
                # CI/không có late vector → đường cũ y hệt (fake/inject signature cũ vẫn chạy).
                append_to_index(
                    chunks=all_chunks,
                    video_name=state["video_path"],
                    custom_metadata=all_metadata,
                    batch_size=32,
                )

            source_stem = canonical_source_stem(state["filename"])
            update_source_status(
                state["source_id"],
                status="index_ready",
                progress=0.7,
                substatus="faiss_ready",
                capabilities={"chunk_query": True, "memory_query": False},
            )

            log_node_event(state["job_id"], "EmbedAndIndex", "ok", t.ms(), {"chunks": len(all_chunks)})
            return {**state, "source_stem": source_stem, "progress": 75, "current_node": "EmbedAndIndex", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "EmbedAndIndex", "error", t.ms(), {"error": str(e)})
            return {**state, "error": str(e), "current_node": "EmbedAndIndex"}

    def memory_tree_node(state: dict) -> dict:
        t = _Timer()
        try:
            _set_job(state["job_id"], progress=90, current_node="BuildMemoryTree")
            update_source_status(
                state["source_id"],
                status="index_ready",
                progress=0.8,
                substatus="building_memory_tree",
            )

            lock_path = str(data_dir / "memory_tree.lock")
            with FileLock(lock_path):
                build_memory_tree_for_sources([state["source_stem"]])

            update_source_status(
                state["source_id"],
                status="ready",
                progress=1.0,
                substatus="memory_tree_ready",
                capabilities={"chunk_query": True, "memory_query": True},
            )

            log_node_event(state["job_id"], "BuildMemoryTree", "ok", t.ms())
            return {**state, "progress": 90, "current_node": "BuildMemoryTree", "error": None}
        except Exception as e:
            log_node_event(state["job_id"], "BuildMemoryTree", "error", t.ms(), {"error": str(e)})
            # Không revert status về processing; giữ index_ready và gắn lỗi
            try:
                update_source_status(
                    state["source_id"],
                    status="index_ready",
                    progress=0.8,
                    substatus="memory_tree_failed",
                    capabilities={"chunk_query": True, "memory_query": False},
                    error=str(e),
                )
            except Exception:
                pass
            return {**state, "error": str(e), "current_node": "BuildMemoryTree"}

    def finalize_node(state: dict) -> dict:
        _set_job(state["job_id"], status="done", progress=100, current_node="Finalize")
        log_node_event(state["job_id"], "Finalize", "ok", 0.0)
        return {**state, "progress": 100, "current_node": "Finalize"}

    def error_handler_node(state: dict) -> dict:
        raw = state.get("error")
        err = (str(raw).strip() if raw is not None else "") or "unknown error"
        _set_job(state["job_id"], status="error", progress=0, current_node="ErrorHandler", error_text=err)
        log_node_event(state["job_id"], "ErrorHandler", "error", 0.0, {"error": err})
        try:
            update_source_status(state["source_id"], status="error", progress=0.0, error=err)
        except Exception:
            pass
        return {**state, "current_node": "ErrorHandler"}

    # LangGraph không cho router trả về '' — key phải có trong mapping conditional_edges.
    def _route_err_or_continue(s: dict) -> str:
        return "ErrorHandler" if s.get("error") else "Continue"

    g = StateGraph(IngestState)
    g.add_node("ExtractText", extract_text_node)
    g.add_node("Normalize", normalize_node)
    g.add_node("Chunk", chunk_node)
    g.add_node("ProcessChunks", process_chunks_node)
    g.add_node("EmbedAndIndex", embed_index_node)
    g.add_node("BuildMemoryTree", memory_tree_node)
    g.add_node("Finalize", finalize_node)
    g.add_node("ErrorHandler", error_handler_node)

    g.set_entry_point("ExtractText")
    for node_name, next_name in (
        ("ExtractText", "Normalize"),
        ("Normalize", "Chunk"),
        ("Chunk", "ProcessChunks"),
        ("ProcessChunks", "EmbedAndIndex"),
        ("EmbedAndIndex", "BuildMemoryTree"),
        ("BuildMemoryTree", "Finalize"),
    ):
        g.add_conditional_edges(
            node_name,
            _route_err_or_continue,
            {"ErrorHandler": "ErrorHandler", "Continue": next_name},
        )
    g.add_edge("Finalize", END)
    g.add_edge("ErrorHandler", END)

    checkpointer = sqlite_saver_from_path(data_dir / "checkpoints.sqlite")
    return g.compile(checkpointer=checkpointer)

