from __future__ import annotations

from typing import Any, NotRequired, Optional, TypedDict

from langchain_core.documents import Document


class IngestState(TypedDict):
    job_id: str
    source_id: str
    file_path: str
    filename: str
    progress: int  # 0-100
    current_node: str
    artifacts: dict
    error: Optional[str]
    # LangChain ingest (USE_LC_INGEST=1)
    text: NotRequired[str]
    chunks: NotRequired[list[str]]
    raw_docs: NotRequired[list[Document]]
    # Data-quality: Markdown chuẩn hoá + per-chunk/doc enrich (PHẢI khai báo để LangGraph giữ).
    markdown: NotRequired[str]
    md_path: NotRequired[str]
    chunk_headings: NotRequired[list[str]]
    doc_meta: NotRequired[dict[str, Any]]
    # Pipeline sau Chunk — PHẢI khai báo: LangGraph merge state chỉ giữ field có trong TypedDict.
    video_name: NotRequired[str]
    video_path: NotRequired[str]
    metadata_entries: NotRequired[list[dict[str, Any]]]
    source_stem: NotRequired[str]


class QueryState(TypedDict):
    job_id: str
    session_id: str
    conversation_history: list
    q: str
    selected_sources: list
    use_memory_tree: bool
    category: NotRequired[Optional[str]]
    language: NotRequired[Optional[str]]
    retrieved_chunks: list
    retrieved_sources: NotRequired[list]
    context: str
    answer: str
    retry_count: int
    low_confidence: bool
    progress: int
    current_node: str
    error: Optional[str]
    # LangGraph chỉ giữ các field có trong schema — thiếu payload/done → API mất answer.
    payload: NotRequired[Optional[dict]]
    status_code: NotRequired[int]
    done: NotRequired[bool]
    cache_key: NotRequired[Optional[str]]
    eval_score: NotRequired[float]
    processing_message: NotRequired[Optional[str]]


class MindmapState(TypedDict):
    job_id: str
    source_names: list
    strategy: str
    result: dict
    progress: int
    current_node: str
    error: Optional[str]

