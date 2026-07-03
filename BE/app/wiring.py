"""
app/wiring.py — dựng các LangGraph pipeline + inject dependency, tách khỏi main.py.

main.py chỉ còn lo định tuyến HTTP; wiring.py trả lời câu hỏi "graph nào được dựng,
ghép với callback/impl nào". Mọi tác vụ nặng (extract/chunk/embed/retrieve/summarize/
mindmap) vẫn được truyền vào dưới dạng callback (DI) — không import cứng ở đây.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class Graphs:
    ingest: Any | None = None
    query: Any | None = None
    mindmap: Any | None = None
    query_build_error: Optional[str] = None


def build_graphs(
    *,
    data_dir,
    index_meta_path,
    # ingest deps
    update_source_status: Callable[..., None],
    extract_text: Callable[..., Any],
    split_text: Callable[..., Any],
    process_and_store_chunks: Callable[..., Any],
    append_to_index: Callable[..., Any],
    build_memory_tree_for_sources: Callable[..., Any],
    jobs_update: Callable[..., None] | None,
    # query deps
    make_cache_key: Callable[..., Any],
    get_cached: Callable[..., Any],
    set_cached: Callable[..., Any],
    check_sources_status: Callable[..., Any],
    get_source_status_by_stem: Callable[..., Any],
    search_index: Callable[..., Any],
    summarize_results: Callable[..., Any],
    query_with_memory_tree: Callable[..., Any],
    get_session_history: Callable[..., Any] | None,
    # mindmap deps
    collect_mindmap_input: Callable[..., dict],
    mindmap_pipeline: Any,
    persist_mindmap: Callable[[dict], None],
    retriever: Any | None = None,
) -> Graphs:
    """Dựng 3 graph; lỗi từng graph được nuốt (trả None) để app vẫn chạy phần còn lại."""
    g = Graphs()

    try:
        from app.graphs.ingest_graph import build_ingest_graph
        g.ingest = build_ingest_graph(
            update_source_status=update_source_status,
            data_dir=data_dir,
            extract_text=extract_text,
            split_text=split_text,
            process_and_store_chunks=process_and_store_chunks,
            append_to_index=append_to_index,
            build_memory_tree_for_sources=build_memory_tree_for_sources,
            jobs_update=jobs_update,
        )
    except Exception as exc:
        print(f"[WARN] INGEST_GRAPH không khởi tạo được: {exc}")

    try:
        from app.graphs.query_graph import build_query_graph
        g.query = build_query_graph(
            data_dir=data_dir,
            index_meta_path=index_meta_path,
            jobs_update=jobs_update,
            make_cache_key=make_cache_key,
            get_cached=get_cached,
            set_cached=set_cached,
            check_sources_status=check_sources_status,
            get_source_status_by_stem=get_source_status_by_stem,
            search_index=search_index,
            summarize_results=summarize_results,
            query_with_memory_tree=query_with_memory_tree,
            get_session_history=get_session_history,
            retriever=retriever,
        )
    except Exception as exc:
        g.query_build_error = repr(exc)
        logging.exception("QUERY_GRAPH không khởi tạo được")

    try:
        from app.graphs.mindmap_graph import build_mindmap_graph
        g.mindmap = build_mindmap_graph(
            data_dir=data_dir,
            index_meta_path=index_meta_path,
            jobs_update=jobs_update,
            collect_input=collect_mindmap_input,
            pipeline=mindmap_pipeline,
            persist_record=persist_mindmap,
        )
    except Exception as exc:
        print(f"[WARN] MINDMAP_GRAPH không khởi tạo được: {exc}")

    return g
