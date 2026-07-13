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
    # Late chunking: vector mean-pool theo span (aligned 1:1 với `chunks`); vắng nếu tắt/CI.
    late_embeddings: NotRequired[list]
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
    # Conversation Context Layer (Phase B/C) — all optional so flag-off state is unchanged.
    conversation_context: NotRequired[Optional[dict]]  # structured recent context (source-scoped)
    source_context_hash: NotRequired[Optional[str]]      # cache bucket for the current scope
    original_question: NotRequired[str]                  # user's raw question (answer prompt)
    standalone_question: NotRequired[str]                # rewritten follow-up (retrieval)
    context_mode: NotRequired[str]                       # standalone | contextual | low_confidence_contextual
    context_signature: NotRequired[Optional[str]]        # hash of the turns used (cache safety)
    cache_scope: NotRequired[str]                        # Phase E: cache/single-flight scope ("public" flag off, else user_id)
    category: NotRequired[Optional[str]]
    language: NotRequired[Optional[str]]
    retrieved_chunks: list
    retrieved_stems: NotRequired[list]
    retrieved_sources: NotRequired[list]
    context_conflicts: NotRequired[list]  # cặp chunk mâu thuẫn do NLI phát hiện (VerifyContext)
    rerank_scores: NotRequired[list]  # điểm cross-encoder (0-1) khớp retrieved_chunks — CRAG grade dùng
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
    route: NotRequired[Optional[str]]
    doc_grade: NotRequired[Optional[str]]
    rewrite_count: NotRequired[int]
    crag_fallback: NotRequired[bool]
    gen_fallback: NotRequired[bool]  # answer là message chẩn đoán (model trả rỗng) — không cache
    awaiting_review: NotRequired[bool]
    review_decision: NotRequired[Optional[dict]]


class SummaryState(TypedDict):
    job_id: str
    source_names: list
    user_id: NotRequired[Optional[str]]  # Phase D: record owner (bound at persist)
    length_mode: NotRequired[str]
    mm_input: NotRequired[dict]
    content_hash: NotRequired[str]
    sections: NotRequired[list]
    skeleton_method: NotRequired[str]
    section_summaries: NotRequired[list]
    overview_meta: NotRequired[dict]  # {title, overview, entities} từ Synthesize
    degraded_missing: NotRequired[list]
    result: NotRequired[dict]
    cancelled: NotRequired[bool]
    progress: int
    current_node: str
    error: Optional[str]
    # LangGraph chỉ giữ field có trong TypedDict — _t0 tính elapsed_sec ở AssemblePersist.
    _t0: NotRequired[float]


class MindmapState(TypedDict):
    job_id: str
    source_names: list
    user_id: NotRequired[Optional[str]]  # Phase D: record owner (bound at persist)
    mm_input: NotRequired[dict]
    content_hash: NotRequired[str]
    skeleton: NotRequired[list]
    skeleton_method: NotRequired[str]
    nodes: NotRequired[list]
    relations: NotRequired[list]
    degraded_missing: NotRequired[list]
    result: NotRequired[dict]
    cancelled: NotRequired[bool]
    progress: int
    current_node: str
    error: Optional[str]
    # LangGraph chỉ giữ field có trong TypedDict — _t0 dùng để tính elapsed_sec ở AssemblePersist.
    _t0: NotRequired[float]

