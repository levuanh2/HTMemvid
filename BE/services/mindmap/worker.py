"""
Logic sinh mindmap (tách khỏi main.py để async job gọi, tránh import vòng).
Tối ưu với 3 generation modes: fast, balanced, quality.
Hard deadline thật - không bị kẹt 30 phút.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import re
import time as time_module
import unicodedata
import uuid
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

import os

from pydantic import BaseModel, Field

from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer

from app.clients.llm_factory import ask_ai, get_embedding_model
from services.mindmap.utils import generate_mindmap_flat, generate_mindmap_cmgn, get_main_branches
from app.domains.vectorstore.embedding_utils import safe_stack_vectors, normalize_embeddings_array
try:
    from shared.env_loader import load_project_env
    load_project_env(override=False)
except Exception:
    pass

# ========== CONSTANTS ==========
# Model theo mode - cho phép model nhỏ hơn cho fast/balanced
# Cấu hình mong muốn:
# MINDMAP_MODEL_FAST=qwen3.5:9b
# MINDMAP_MODEL_BALANCED=qwen3.5:9b
# MINDMAP_MODEL_QUALITY=qwen2.5:14b
# MINDMAP_MODEL_FALLBACK=gemma4:e4b

def get_mindmap_model_for_mode(mode: str) -> str:
    """Get model theo mode."""
    mode = (mode or "balanced").lower()

    if mode == "fast":
        return os.getenv("MINDMAP_MODEL_FAST", "qwen3.5:9b").strip() or "qwen3.5:9b"

    if mode == "balanced":
        return os.getenv("MINDMAP_MODEL_BALANCED", "qwen3.5:9b").strip() or "qwen3.5:9b"

    if mode == "quality":
        return os.getenv("MINDMAP_MODEL_QUALITY", "qwen2.5:14b").strip() or "qwen2.5:14b"

    return os.getenv("MINDMAP_MODEL_FALLBACK", "gemma4:e4b").strip() or "gemma4:e4b"


def get_fallback_model() -> str:
    """Get fallback model khi primary model fail."""
    return os.getenv("MINDMAP_MODEL_FALLBACK", "gemma4:e4b").strip() or "gemma4:e4b"


# Cache model instances để tránh tạo lại nhiều lần
_model_cache: dict[str, Any] = {}
_model_cache_lock = None  # Lazy init

MINDMAP_OPTIONS = {"temperature": 0.2}

# Generation modes
MODE_FAST = "fast"
MODE_BALANCED = "balanced"
MODE_QUALITY = "quality"
VALID_MODES = {MODE_FAST, MODE_BALANCED, MODE_QUALITY}
DEFAULT_MODE = MODE_BALANCED

# ========== CONTEXT LIMITS (GIẢM THÊM) ==========
CONTEXT_LIMIT_FAST = 8000       # Giảm từ 12000
CONTEXT_LIMIT_BALANCED = 12000  # Giảm từ 20000
CONTEXT_LIMIT_QUALITY = 40000   # Giữ nguyên

# ========== NODE LIMITS ==========
NODE_LIMITS = {
    MODE_FAST: {"max_total": 35, "max_depth": 2, "max_children": 6},
    MODE_BALANCED: {"max_total": 55, "max_depth": 3, "max_children": 7},
    MODE_QUALITY: {"max_total": 90, "max_depth": 4, "max_children": 10},
}

# ========== TIMEOUT PER LLM CALL (seconds) ==========
LLM_TIMEOUT_FAST = 60
LLM_TIMEOUT_BALANCED = 30  # TEMP TESTING: was 90
LLM_TIMEOUT_QUALITY = 120

# ========== TOTAL JOB TIMEOUT (seconds) ==========
# - Fast: 90s du cho 1 LLM call + deterministic visual
# - Balanced: 180s du cho 1 LLM call + deterministic visual
# - Quality: 600s cho phep cmgn/iterative nhieu LLM calls
JOB_TIMEOUT_FAST = 90
JOB_TIMEOUT_BALANCED = 60  # TEMP TESTING: was 180
JOB_TIMEOUT_QUALITY = 600

# ========== LLM CALL BUDGET (MỚI) ==========
# Giới hạn số LLM calls thật sự
LLM_CALL_BUDGET_FAST = 1       # Tối đa 1 LLM call
LLM_CALL_BUDGET_BALANCED = 1   # Tối đa 1 LLM call
LLM_CALL_BUDGET_QUALITY = 8    # Quality cho phép nhiều calls hơn


def get_llm_timeout_for_mode(mode: str) -> float:
    """Get per-call LLM timeout for the given mode."""
    if mode == MODE_FAST:
        return float(LLM_TIMEOUT_FAST)
    if mode == MODE_QUALITY:
        return float(LLM_TIMEOUT_QUALITY)
    return float(LLM_TIMEOUT_BALANCED)


def get_job_timeout_for_mode(mode: str) -> float:
    """Get total job timeout for the given mode."""
    if mode == MODE_FAST:
        return float(JOB_TIMEOUT_FAST)
    if mode == MODE_QUALITY:
        return float(JOB_TIMEOUT_QUALITY)
    return float(JOB_TIMEOUT_BALANCED)


def get_llm_call_budget_for_mode(mode: str) -> int:
    """Get LLM call budget for the given mode."""
    if mode == MODE_FAST:
        return LLM_CALL_BUDGET_FAST
    if mode == MODE_QUALITY:
        return LLM_CALL_BUDGET_QUALITY
    return LLM_CALL_BUDGET_BALANCED


def get_max_retries_for_mode(mode: str) -> int:
    """Get max retries for LLM calls based on mode."""
    if mode == MODE_FAST:
        return 0  # Không retry
    if mode == MODE_BALANCED:
        return 0  # Hoặc 1 nếu còn budget và remaining > 45s
    return 2  # Quality cho phép retry


# Cache
CACHE_VERSION = "v2"

_mindmap_job_id_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mindmap_job_id", default=None
)


def attach_mindmap_job_context(job_id: Optional[str]) -> None:
    _mindmap_job_id_ctx.set(job_id)


def _notify_progress(
    progress_cb: Optional[Callable[[int], None]],
    p: int,
    msg_vi: str,
) -> None:
    if progress_cb is not None:
        progress_cb(int(p))
    jid = _mindmap_job_id_ctx.get(None)
    if jid:
        try:
            from app.domains.jobs.jobs_store import update_job
            update_job(jid, progress=int(p), current_node=msg_vi)
        except Exception:
            pass


# ========== TIMING ==========
class TimingLogger:
    def __init__(self, mode: str, strategy: str):
        self.mode = mode
        self.strategy = strategy
        self.timers = {}
        self.start_total = time_module.time()

    def start(self, name: str):
        self.timers[name] = time_module.time()

    def elapsed(self, name: str) -> float:
        if name not in self.timers:
            return 0
        return time_module.time() - self.timers[name]

    def log(self):
        total = time_module.time() - self.start_total
        parts = [f"mode={self.mode}", f"strategy={self.strategy}"]
        for name, start in self.timers.items():
            elapsed = time_module.time() - start
            parts.append(f"{name}={elapsed:.1f}s")
        parts.append(f"total={total:.1f}s")
        print(f"[MindMap Timing] {' '.join(parts)}")


# ========== LLM CALL BUDGET TRACKER (MỚI) ==========
class LlmCallBudget:
    """Track số LLM calls đã dùng, không cho phép vượt budget."""

    def __init__(self, mode: str):
        self.mode = mode
        self.used = 0
        self.max_calls = get_llm_call_budget_for_mode(mode)
        self.calls: list[dict] = []

    def can_call(self) -> bool:
        return self.used < self.max_calls

    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)

    def register(self, call_name: str, model: str, prompt_chars: int, timeout: float, elapsed: float, error: str | None = None):
        """Register một LLM call đã hoàn thành."""
        self.used += 1
        self.calls.append({
            "name": call_name,
            "model": model,
            "promptChars": prompt_chars,
            "timeout": timeout,
            "elapsed": elapsed,
            "error": error,
        })

    def summary(self) -> dict:
        return {
            "mode": self.mode,
            "llmCallsUsed": self.used,
            "llmCallBudget": self.max_calls,
            "llmCalls": self.calls,
        }


# ========== TIMEOUT TRACKER ==========
class TimeoutTracker:
    """Track job timeout và per-call timeout với hard deadline."""

    def __init__(self, mode: str, job_timeout: float, llm_timeout: float):
        self.mode = mode
        self.job_timeout = job_timeout
        self.llm_timeout = llm_timeout
        self.job_start = time_module.time()
        self.deadline = self.job_start + job_timeout
        self.llm_calls_made = 0
        self.llm_call_names: list[str] = []
        self._llm_call_start: float | None = None

    def time_remaining(self) -> float:
        """Returns seconds remaining until job deadline."""
        return max(0, self.deadline - time_module.time())

    def is_near_deadline(self, threshold: float = 15.0) -> bool:
        """Check if we're within threshold seconds of deadline."""
        return self.time_remaining() < threshold

    def should_skip_llm_visual(self) -> bool:
        """Check if should skip LLM visual diagram (fast/balanced near deadline)."""
        if self.mode == MODE_QUALITY:
            return False  # Quality mode always tries LLM visual
        return self.is_near_deadline(threshold=20.0)

    def check_deadline(self, operation: str = "operation") -> None:
        """Raise TimeoutError if past deadline."""
        if time_module.time() > self.deadline:
            raise TimeoutError(
                f"[MindMap Timeout] Job exceeded deadline during {operation}. "
                f"mode={self.mode} elapsed={self.elapsed():.1f}s timeout={self.job_timeout}s"
            )

    def elapsed(self) -> float:
        """Returns elapsed seconds since job start."""
        return time_module.time() - self.job_start

    def record_llm_call(self, call_name: str) -> None:
        """Record a LLM call for logging."""
        self.llm_calls_made += 1
        self.llm_call_names.append(call_name)

    def log_timeout_info(self) -> None:
        """Log timeout configuration info."""
        print(
            f"[MindMap Timeout] mode={self.mode} "
            f"llmTimeoutPerCall={self.llm_timeout:.0f}s "
            f"jobTimeout={self.job_timeout:.0f}s "
            f"jobElapsed={self.elapsed():.1f}s "
            f"llmCalls={self.llm_calls_made} "
            f"remaining={self.time_remaining():.1f}s"
        )

    def start_llm_call(self) -> None:
        """Mark start of LLM call for timing."""
        self._llm_call_start = time_module.time()

    def end_llm_call(self) -> float:
        """Mark end of LLM call, return elapsed seconds."""
        if self._llm_call_start is None:
            return 0.0
        elapsed = time_module.time() - self._llm_call_start
        self._llm_call_start = None
        return elapsed


# ========== PYDANTIC MODELS ==========
class MindmapLeaf(BaseModel):
    label: str
    children: list[str] = Field(default_factory=list)


class MindmapBranch(BaseModel):
    label: str
    children: list[MindmapLeaf] = Field(default_factory=list)


class MindmapOutput(BaseModel):
    title: str
    branches: list[MindmapBranch] = Field(default_factory=list)


class VisualDiagramNode(BaseModel):
    id: str
    title: str
    subtitle: str | None = None
    type: str = "concept"
    group: str = "other"
    level: int = 1
    icon: str | None = None
    order: int = 0


class VisualDiagramEdge(BaseModel):
    id: str
    source: str
    target: str
    label: str | None = None
    type: str = "relates_to"


class VisualDiagramGroup(BaseModel):
    id: str
    label: str
    color: str = "slate"


class VisualDiagramOutput(BaseModel):
    diagramType: str = "concept_map"
    title: str
    summary: str | None = None
    nodes: list[VisualDiagramNode] = Field(default_factory=list)
    edges: list[VisualDiagramEdge] = Field(default_factory=list)
    groups: list[VisualDiagramGroup] = Field(default_factory=list)


# ========== UTILITY FUNCTIONS ==========
def _mindmap_output_to_flat_nodes(out: MindmapOutput, root_title_fallback: str) -> list[dict]:
    title = (out.title or "").strip() or root_title_fallback
    nodes: list[dict] = [{"id": "root", "parent": None, "title": title}]
    for bi, br in enumerate(out.branches):
        bid = f"b-{bi}"
        blabel = (br.label or "").strip() or f"Nhánh {bi + 1}"
        nodes.append({"id": bid, "parent": "root", "title": blabel})
        for lj, leaf in enumerate(br.children):
            lid = f"b-{bi}-l-{lj}"
            llabel = (leaf.label or "").strip() or f"Mục {lj + 1}"
            nodes.append({"id": lid, "parent": bid, "title": llabel})
            for ck, s in enumerate(leaf.children):
                st = (s or "").strip()
                if not st:
                    continue
                sid = f"b-{bi}-l-{lj}-c-{ck}"
                nodes.append({"id": sid, "parent": lid, "title": st})
    return nodes


def _concat_cluster_summary(texts: list[str], max_chars: int = 900) -> str:
    parts: list[str] = []
    total = 0
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        chunk = t[:350].strip()
        if total + len(chunk) + 2 > max_chars:
            break
        parts.append(chunk)
        total += len(chunk) + 2
    return " | ".join(parts) if parts else "(trống)"


def _parse_mindmap_output_json(raw: str) -> MindmapOutput:
    raw = (raw or "").strip()
    try:
        return MindmapOutput.model_validate_json(raw)
    except Exception:
        pass
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```\s*$", "", raw)
        raw = raw.strip()
        try:
            return MindmapOutput.model_validate_json(raw)
        except Exception:
            pass
    m = re.search(r"\{[\s\S]*\}\s*$", raw)
    if m:
        return MindmapOutput.model_validate_json(m.group(0))
    raise ValueError("Không parse được JSON MindmapOutput")


def _parse_visual_diagram_json(raw: str) -> VisualDiagramOutput:
    raw = (raw or "").strip()
    try:
        return VisualDiagramOutput.model_validate_json(raw)
    except Exception:
        pass
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```\s*$", "", raw)
        raw = raw.strip()
        try:
            return VisualDiagramOutput.model_validate_json(raw)
        except Exception:
            pass
    m = re.search(r"\{[\s\S]*\}\s*$", raw)
    if m:
        try:
            return VisualDiagramOutput.model_validate_json(m.group(0))
        except Exception:
            pass
    raise ValueError("Không parse được JSON VisualDiagramOutput")


def _depth_for_node(node_id: str, parent_map: dict[str, str | None]) -> int:
    depth = 0
    current = node_id
    seen = set()
    while parent_map.get(current) and current not in seen:
        seen.add(current)
        depth += 1
        current = parent_map[current]
    return depth


def _flat_nodes_to_visual_diagram(flat_nodes: list[dict], root_title: str, sources: list[str] = None) -> dict:
    safe_nodes = flat_nodes if flat_nodes and isinstance(flat_nodes, list) and len(flat_nodes) > 0 else []
    parent_map = {str(n.get("id")): n.get("parent") for n in safe_nodes if n.get("id")}
    visual_nodes = []
    for index, n in enumerate(safe_nodes or []):
        node_id = str(n.get("id"))
        node_title = str(n.get("title") or "Untitled")
        depth = _depth_for_node(node_id, parent_map)
        if depth == 0:
            node_type = "root"
            group = "main"
            icon = "brain"
        elif depth == 1:
            node_type = "concept"
            group = f"group-{index % 6}"
            icon = "lightbulb"
        elif depth == 2:
            node_type = "insight"
            group = f"group-{index % 6}"
            icon = "sparkles"
        else:
            node_type = "example"
            group = f"group-{index % 6}"
            icon = "check"
        visual_nodes.append({
            "id": node_id,
            "title": node_title,
            "subtitle": None,
            "type": node_type,
            "group": group,
            "level": depth,
            "icon": icon,
            "order": index,
        })
    valid_ids = {v["id"] for v in visual_nodes}
    visual_edges = []
    for index, n in enumerate(safe_nodes or []):
        node_id = str(n.get("id"))
        parent_id = n.get("parent")
        if not parent_id:
            continue
        parent_id_str = str(parent_id)
        if parent_id_str not in valid_ids or node_id not in valid_ids:
            continue
        visual_edges.append({
            "id": f"ve-{index}-{parent_id_str}-{node_id}",
            "source": parent_id_str,
            "target": node_id,
            "label": "gồm" if parent_id_str == "root" else "chi tiết",
            "type": "contains",
        })
    return {
        "diagramType": "concept_map",
        "title": root_title or "Visual Diagram",
        "summary": "Sơ đồ trực quan được tạo từ mindmap hiện có.",
        "nodes": visual_nodes,
        "edges": visual_edges,
        "groups": [
            {"id": "main", "label": "Chủ đề chính", "color": "violet"},
            {"id": "other", "label": "Nội dung liên quan", "color": "slate"},
        ],
    }


def sanitize_mindmap_nodes(flat_nodes: list[dict]) -> list[dict]:
    """
    Sanitize flat_nodes: đảm bảo:
    - Có đúng 1 root (parent=None)
    - Node nào không có id thì tạo id
    - Parent id phải tồn tại, nếu không thì gắn về root
    - Remove duplicate title cùng parent
    - Không có cycle
    """
    if not flat_nodes:
        return [{"id": "root", "parent": None, "title": "Mind Map"}]

    # Tạo map để track
    nodes_by_id: dict[str, dict] = {}
    root_nodes: list[dict] = []
    child_nodes: list[dict] = []

    for n in flat_nodes:
        nid = n.get("id")
        if nid:
            nodes_by_id[str(nid)] = n
        if n.get("parent") is None:
            root_nodes.append(n)
        else:
            child_nodes.append(n)

    # Nếu không có root hoặc nhiều hơn 1 root
    if not root_nodes:
        if flat_nodes:
            root_nodes = [flat_nodes[0]]
            child_nodes = flat_nodes[1:]
        else:
            return [{"id": "root", "parent": None, "title": "Mind Map"}]

    # Chỉ giữ root đầu tiên
    main_root = root_nodes[0]
    main_root["id"] = "root"
    main_root["parent"] = None
    if not main_root.get("title"):
        main_root["title"] = "Mind Map"

    # Rebuild nodes với deduplication
    result: list[dict] = [main_root]
    seen_titles_by_parent: dict[str | None, set[str]] = {None: {main_root.get("title", "").lower()}}
    valid_ids: set[str] = {"root"}

    for n in child_nodes:
        nid = n.get("id")
        parent = n.get("parent")
        parent_str = str(parent) if parent else None

        # Tạo id nếu không có
        if not nid:
            nid = f"node_{len(result)}"
            n["id"] = nid

        # Kiểm tra parent tồn tại
        if parent_str not in valid_ids:
            parent_str = None
            n["parent"] = None

        title = n.get("title", "").lower()

        # Check duplicate
        parent_key = parent_str
        if parent_key not in seen_titles_by_parent:
            seen_titles_by_parent[parent_key] = set()

        if title in seen_titles_by_parent[parent_key]:
            continue

        seen_titles_by_parent[parent_key].add(title)
        valid_ids.add(nid)
        result.append(n)

    # Remove cycles (parent trỏ tới ancestor)
    for n in result:
        nid = n.get("id")
        parent = n.get("parent")
        if not parent:
            continue
        parent_str = str(parent)

        # Kiểm tra nếu parent trỏ về chính nó
        if parent_str == nid:
            n["parent"] = None
            continue

        # Kiểm tra cycle bằng cách follow chain
        visited: set[str] = set()
        current = parent_str
        while current:
            if current == nid or current in visited:
                n["parent"] = None
                break
            visited.add(current)
            parent_node = nodes_by_id.get(current)
            if not parent_node:
                break
            current = str(parent_node.get("parent")) if parent_node.get("parent") else ""

    return result


def _unique_chain(items: list[str]) -> list[str]:
    """Remove duplicates while preserving order (first occurrence wins)."""
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _get_candidates_for_mode(mode: str) -> list[str]:
    """Get base candidates for a mode (without iterative for fast/balanced, no cmgn_light for balanced)."""
    if mode == MODE_FAST:
        return ["mindmap_v2", "single_call_schema", "deterministic_basic_branches"]
    elif mode == MODE_BALANCED:
        # Balanced KHÔNG có cmgn_light mặc định - chỉ dùng mindmap_v2, multilevel_fast, single_call
        return ["mindmap_v2", "multilevel_fast", "single_call_schema", "deterministic_basic_branches"]
    else:  # quality
        return ["cmgn", "mindmap_v2", "multilevel", "iterative", "single_call_schema", "deterministic_basic_branches"]


def get_fallback_chain(failed_strategy: str, mode: str) -> list[str]:
    """
    Trả về chain fallback sau khi failed_strategy đã fail.
    - Chain KHÔNG bao gồm failed_strategy (đã thử rồi).
    - Unique, không duplicate.
    - Fast/balanced không có iterative, balanced không có cmgn_light.
    """
    candidates = _get_candidates_for_mode(mode)
    return _unique_chain([s for s in candidates if s != failed_strategy])


# ========== CONTEXT COMPRESSION ==========
def _compress_context(texts: list[str], max_chars: int) -> list[str]:
    """Compress context to fit within max_chars limit."""
    if not texts:
        return []
    compressed = []
    total = 0
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        if total + len(t) + 2 > max_chars:
            break
        compressed.append(t)
        total += len(t) + 2
    return compressed


def _cluster_and_label_no_llm(chunks: list[dict], n_clusters: int = 6) -> list[dict]:
    """KMeans trên embedding có sẵn + TF-IDF top-3 keyword / cụm."""
    rows: list[tuple[str, np.ndarray]] = []
    dim: int | None = None
    for c in chunks:
        tx = (c.get("text") or "").strip()
        emb = c.get("embedding")
        if not tx:
            continue
        if isinstance(emb, list) and len(emb) > 0:
            vec = np.asarray(emb, dtype=np.float32)
            if vec.ndim == 1:
                vec = vec.reshape(1, -1)
            if dim is None:
                dim = vec.shape[1]
            elif vec.shape[1] != dim:
                print(f"[mindmap] WARNING: embedding dim mismatch: got {vec.shape[1]}, expected {dim}. Skipping this chunk.")
                continue
            rows.append((tx, vec))
    if not rows:
        raise ValueError("Không có chunk kèm embedding")
    texts = [r[0] for r in rows]
    X = safe_stack_vectors([r[1] for r in rows], expected_dim=dim, context="mindmap_clustering")
    if X is None:
        raise ValueError("Không tạo được ma trận embedding từ chunks")
    n = X.shape[0]
    k = max(1, min(int(n_clusters), n))
    labels = KMeans(n_clusters=k, random_state=42, n_init=10).fit_predict(X)
    max_feat = min(1024, max(64, n * 16))
    vec = TfidfVectorizer(max_features=max_feat, token_pattern=r"(?u)\b\w\w+\b", lowercase=True)
    try:
        M = vec.fit_transform(texts)
    except ValueError:
        vec = TfidfVectorizer(max_features=64, token_pattern=r"(?u)\b\w+\b", lowercase=True)
        M = vec.fit_transform(texts)
    terms = vec.get_feature_names_out()
    out: list[dict] = []
    for cid in range(k):
        idxs = np.where(labels == cid)[0]
        if idxs.size == 0:
            continue
        centroid = np.asarray(M[idxs].mean(axis=0)).ravel()
        top_i = centroid.argsort()[-5:][::-1]
        keywords = [str(terms[i]) for i in top_i if centroid[i] > 1e-9][:3]
        summary = _concat_cluster_summary([texts[int(i)] for i in idxs.tolist()])
        topic = " · ".join(keywords) if keywords else f"Cụm {cid + 1}"
        out.append({
            "topic": topic,
            "keywords": keywords,
            "summary": summary,
            "chunk_count": int(idxs.size),
        })
    if not out:
        raise ValueError("Không tạo được cụm TF-IDF")
    return out


# ========== NODE CAP LIMITING ==========
def cap_mindmap_nodes(flat_nodes: list[dict], mode: str) -> list[dict]:
    """
    Giới hạn số nodes theo mode.
    - Không cắt root
    - Ưu tiên giữ level 1 và level 2
    """
    limits = NODE_LIMITS.get(mode, NODE_LIMITS[MODE_BALANCED])
    max_total = limits["max_total"]
    max_depth = limits["max_depth"]
    max_children = limits["max_children"]
    if len(flat_nodes) <= max_total:
        return flat_nodes
    parent_map: dict[str, str | None] = {}
    for n in flat_nodes:
        parent_map[n.get("id", "")] = n.get("parent")
    depth_map: dict[str, int] = {}
    for node in flat_nodes:
        depth_map[node.get("id", "")] = _depth_for_node(node.get("id", ""), parent_map)
    kept: list[dict] = []
    skipped = 0
    for n in flat_nodes:
        nid = n.get("id", "")
        depth = depth_map.get(nid, 0)
        if depth == 0:
            kept.append(n)
            continue
        if depth > max_depth:
            skipped += 1
            continue
        parent = n.get("parent")
        if parent and parent in {x.get("id") for x in kept}:
            siblings = [x for x in flat_nodes if x.get("parent") == parent]
            if len(siblings) > max_children:
                if n not in kept:
                    skipped += 1
                else:
                    kept.append(n)
        else:
            kept.append(n)
    print(f"[mindmap] Node cap: kept={len(kept)}, skipped={skipped}, original={len(flat_nodes)}")
    return kept


# ========== DETERMINISTIC SANITIZE ==========
def _sanitize_deterministic(tree: dict, max_children: int = 7, max_depth: int = 3) -> dict:
    """Sanitize mindmap output deterministically (không gọi LLM)."""
    if not tree or not isinstance(tree, dict):
        return {"name": "Mind Map", "children": []}
    root_name = tree.get("name") or tree.get("title") or "Mind Map"
    children = tree.get("children") or []
    sanitized_children = []
    for child in children[:max_children]:
        if not isinstance(child, dict):
            continue
        name = child.get("name") or child.get("title") or ""
        if not name:
            continue
        sub_children = child.get("children") or []
        sanitized_sub = []
        for sub in sub_children[:max_children]:
            if not isinstance(sub, dict):
                continue
            sub_name = sub.get("name") or sub.get("title") or ""
            if not sub_name:
                continue
            sub_sub = sub.get("children") or []
            final_subs = [s.get("name") or s.get("title") or "" for s in sub_sub[:5] if isinstance(s, dict)]
            new_sub = {"name": sub_name, "children": [{"name": n} for n in final_subs]}
            sanitized_sub.append(new_sub)
        sanitized_children.append({"name": name, "children": sanitized_sub})
    return {"name": root_name, "children": sanitized_children}


def _flatten_tree_to_nodes(tree: dict) -> list[dict]:
    """Convert nested tree dict to flat_nodes format."""
    nodes = []
    root_name = tree.get("name") or tree.get("title") or "Mind Map"
    nodes.append({"id": "root", "parent": None, "title": root_name})
    queue = [(tree.get("children") or [], "root", 1)]
    while queue:
        children, parent_id, depth = queue.pop(0)
        if not children:
            continue
        for idx, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            cid = f"{parent_id}-{idx}"
            title = child.get("name") or child.get("title") or f"Node {idx}"
            nodes.append({"id": cid, "parent": parent_id, "title": title})
            sub_children = child.get("children") or []
            if sub_children and depth < 3:
                queue.append((sub_children, cid, depth + 1))
    return nodes


# ========== MINDMAP BUILDERS ==========
def _build_mindmap_single_call(
    chunks: list[str],
    source_names: list[str],
    model: str,
    embed_fn: Callable[[list[str]], Any],
    root_title: str,
    progress_notify: Callable[[int, str], None],
    mode: str,
    timeout_tracker: TimeoutTracker,
    llm_budget: LlmCallBudget,
) -> tuple[list[dict], str]:
    texts = [c.strip() for c in chunks if c and c.strip()]
    if not texts:
        raise ValueError("Không có chunk")
    progress_notify(40, "Đang gom cụm đoạn văn...")
    emb_raw = embed_fn(texts)
    if not isinstance(emb_raw, np.ndarray):
        emb_raw = np.asarray(emb_raw, dtype=np.float32)
    emb = normalize_embeddings_array(emb_raw, expected_count=len(texts), context="mindmap_single_call")
    if emb is None or emb.shape[0] == 0:
        raise ValueError("Embedding không khớp số chunk")
    n = len(texts)
    if n == 1:
        labels = np.zeros(1, dtype=np.int32)
        n_clusters = 1
    else:
        n_clusters = min(max(2, int(round(np.sqrt(n)))), n, 12)
        km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = km.fit_predict(emb).astype(np.int32)
    clusters: dict[int, list[str]] = {}
    for lab, tx in zip(labels.tolist(), texts):
        clusters.setdefault(int(lab), []).append(tx)
    ordered_keys = sorted(clusters.keys())
    cluster_summaries: list[str] = []
    for ki, k in enumerate(ordered_keys):
        summary = _concat_cluster_summary(clusters[k])
        cluster_summaries.append(f"[Cụm {ki + 1}]\n{summary}")
    sources_line = ", ".join(source_names[:12])
    if len(source_names) > 12:
        sources_line += f" (+{len(source_names) - 12} nguồn)"
    progress_notify(55, "Đang gọi AI tạo mindmap: single_call_schema...")
    max_context = {
        MODE_FAST: CONTEXT_LIMIT_FAST,
        MODE_BALANCED: CONTEXT_LIMIT_BALANCED,
        MODE_QUALITY: CONTEXT_LIMIT_QUALITY,
    }.get(mode, CONTEXT_LIMIT_BALANCED)
    cluster_text = "\n\n".join(cluster_summaries[:min(len(cluster_summaries), 8)])
    if len(cluster_text) > max_context:
        cluster_text = cluster_text[:max_context]
    sys_prompt = (
        "Trả về JSON đúng schema: title + branches; mỗi branch có children (leaf: label + children: list string). "
        "Tiếng Việt. Chỉ JSON hợp lệ."
    )
    user_prompt = (
        f"Tiêu đề gốc: {root_title}\nNguồn: {sources_line}\n\n" + cluster_text
    )

    # Retry policy theo mode - fast/balanced không retry
    max_retries = get_max_retries_for_mode(mode)
    last_err: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            # Dùng call_mindmap_llm wrapper với budget và deadline tracking
            raw = call_mindmap_llm(
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                model=model,
                mode=mode,
                strategy="single_call_schema",
                call_name="single_call_schema",
                timeout_tracker=timeout_tracker,
                llm_budget=llm_budget,
                progress_notify=progress_notify,
            )
            out = _parse_mindmap_output_json(raw)
            if not out.branches:
                raise ValueError("MindmapOutput không có branches")
            flat = _mindmap_output_to_flat_nodes(out, root_title)
            if len(flat) < 3:
                raise ValueError("Quá ít node")
            return flat, "single_call_schema"
        except BaseException as e:
            last_err = e
            # Nếu đã hết budget thì không retry
            if not llm_budget.can_call():
                print(f"[MM single_call] Budget exhausted after attempt {attempt + 1}")
                break
            if attempt < max_retries:
                print(f"[MM single_call] attempt {attempt + 1}/{max_retries + 1} failed: {e}")
            continue
    raise RuntimeError(f"single_call_schema failed: {last_err}")


def _build_mindmap_v2(
    chunks: list[dict],
    source_names: list[str],
    model: str,
    embed_fn: Callable[[list[str]], Any],
    root_title: str,
    progress_notify: Callable[[int, str], None],
    mode: str,
    timeout_tracker: TimeoutTracker,
    llm_budget: LlmCallBudget,
) -> tuple[list[dict], str]:
    """TF-IDF + KMeans + 1 LLM call via wrapper."""
    texts_only = [(c.get("text") or "").strip() for c in chunks if (c.get("text") or "").strip()]
    sources_line = ", ".join(source_names[:12])
    if len(source_names) > 12:
        sources_line += f" (+{len(source_names) - 12} nguồn)"
    try:
        progress_notify(42, "Đang phân cụm TF-IDF...")
        cluster_rows = _cluster_and_label_no_llm(chunks, n_clusters=6)
        lines: list[str] = []
        for i, row in enumerate(cluster_rows):
            kw = ", ".join(row.get("keywords") or [])
            summ = (row.get("summary") or "")[:420]
            lines.append(f"[{i + 1}] {row.get('topic')!s} ({row.get('chunk_count')} đoạn)\nkeywords: {kw}\ntóm tắt: {summ}")
        prompt_body = "\n\n".join(lines)
        max_context = {
            MODE_FAST: CONTEXT_LIMIT_FAST,
            MODE_BALANCED: CONTEXT_LIMIT_BALANCED,
            MODE_QUALITY: CONTEXT_LIMIT_QUALITY,
        }.get(mode, CONTEXT_LIMIT_BALANCED)
        if len(prompt_body) > max_context:
            print(f"[MindMap Warning] prompt_body={len(prompt_body)} exceeds limit={max_context}")
            prompt_body = prompt_body[:max_context]
        sys_prompt = (
            "Từ các cụm đã gán nhãn TF-IDF (topic/keywords/tóm tắt), xây MindmapOutput JSON đúng schema: "
            "title ngắn; branches với label và children (MindmapLeaf: label + children là list string). "
            "Tiếng Việt nếu dữ liệu là tiếng Việt. Không thêm ý không có trong cụm."
        )
        user_prompt = (
            f"Tiêu đề gốc: {root_title}\nNguồn: {sources_line}\n\n"
            f"Cụm (đã xử lý offline, không LLM):\n\n{prompt_body}"
        )
        progress_notify(55, "Đang gọi AI tạo mindmap: mindmap_v2...")
        # Dùng call_mindmap_llm wrapper với budget và deadline tracking
        raw = call_mindmap_llm(
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            model=model,
            mode=mode,
            strategy="mindmap_v2",
            call_name="mindmap_v2",
            timeout_tracker=timeout_tracker,
            llm_budget=llm_budget,
            progress_notify=progress_notify,
        )
        out = _parse_mindmap_output_json(raw)
        if not out.branches:
            raise ValueError("MindmapOutput không có branches")
        flat = _mindmap_output_to_flat_nodes(out, root_title)
        if len(flat) < 3:
            raise ValueError("Quá ít node sau mindmap v2")
        return flat, "mindmap_v2"
    except Exception as exc:
        print(f"[MM v2] lỗi {exc} → fallback single_call")
        return _build_mindmap_single_call(
            texts_only, source_names, model, embed_fn, root_title, progress_notify, mode, timeout_tracker, llm_budget
        )


def _build_cmgn_light(
    chunks: list[dict],
    source_names: list[str],
    model: str,
    root_title: str,
    progress_notify: Callable[[int, str], None],
    mode: str,
    timeout_tracker: TimeoutTracker,
    llm_budget: LlmCallBudget,
) -> tuple[list[dict], str]:
    """
    CMGN không critics - gồm 2 LLM calls:
    1. _generate_coreference_graph: phân tích coreference relationships
    2. _generate_mindmap_from_coreference_graph: sinh mindmap từ graph

    CHỈ chạy khi mode == quality.
    """
    # cmgn_light chỉ chạy cho quality mode
    if mode != MODE_QUALITY:
        raise RuntimeError("cmgn_light chỉ chạy cho quality mode")

    from services.mindmap.utils import (
        _prepare_mindmap_chunks, _extract_sentences_from_segments,
        _generate_coreference_graph, _generate_mindmap_from_coreference_graph,
        _sanitize_tree, flatten_mindmap
    )
    texts_only = [(c.get("text") or "").strip() for c in chunks if (c.get("text") or "").strip()]
    prepared_chunks = _prepare_mindmap_chunks(texts_only)
    if not prepared_chunks:
        raise ValueError("Không có dữ liệu cho CMGN")
    sources_line = ", ".join(source_names[:12])
    progress_notify(40, "Đang phân tích coreference...")
    sentences = _extract_sentences_from_segments(prepared_chunks)
    if not sentences:
        raise ValueError("Không tạo được câu từ chunks")
    try:
        progress_notify(55, "Đang tạo mindmap từ graph...")
        # Note: CMGN sử dụng ask_ai trực tiếp từ mindmap_utils - cần cập nhật sau
        coref_graph = _generate_coreference_graph(sentences, model)
        tree = _generate_mindmap_from_coreference_graph(
            coref_graph, prepared_chunks, set(), model
        )
        sanitized = _sanitize_deterministic(tree, max_children=7, max_depth=3)
        flat = _flatten_tree_to_nodes(sanitized)
        return flat, "cmgn_light"
    except Exception as exc:
        print(f"[CMGN light] failed: {exc} → fallback mindmap_v2")
        raise


# ========== MULTILEVEL_FAST ==========
def _build_multilevel_fast(
    chunks: list[str],
    source_names: list[str],
    model: str,
    embed_fn: Callable[[list[str]], Any],
    root_title: str,
    progress_notify: Callable[[int, str], None],
    mode: str,
    timeout_tracker: TimeoutTracker,
    llm_budget: LlmCallBudget,
) -> tuple[list[dict], str]:
    """Multilevel mindmap nhanh - 1 LLM call chính, deterministic subtopics."""
    texts = [c.strip() for c in chunks if c and c.strip()]
    if not texts:
        raise ValueError("Không có chunk")
    max_context = {
        MODE_FAST: 6000,  # Giảm thêm cho fast
        MODE_BALANCED: 10000,  # Giảm cho balanced
        MODE_QUALITY: 25000,
    }.get(mode, 10000)
    sources_line = ", ".join(source_names[:12])
    progress_notify(40, "Đang gom context...")
    context_chunks = _compress_context(texts, max_context)
    context_text = "\n\n---\n\n".join(context_chunks)

    # Cảnh báo nếu prompt quá lớn
    if len(context_text) > max_context + 2000:
        print(f"[MindMap Warning] context_text={len(context_text)} exceeds max_context={max_context}")

    progress_notify(60, "Đang gọi AI tạo mindmap: multilevel_fast...")
    sys_prompt = (
        "Trả về JSON đúng schema MindmapOutput: title + branches[].label + branches[].children[].label + branches[].children[].children[] (list string).\n"
        "Tiếng Việt. Chỉ JSON hợp lệ. 4-6 nhánh chính, mỗi nhánh 2-4 leaf."
    )
    user_prompt = f"""Tiêu đề: {root_title}
Nguồn: {sources_line}
Nội dung:
{context_text}

Hãy tạo mindmap ngắn gọn, đủ ý chính.
"""
    max_retries = get_max_retries_for_mode(mode)
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            # Dùng call_mindmap_llm wrapper với budget và deadline tracking
            raw = call_mindmap_llm(
                system_prompt=sys_prompt,
                user_prompt=user_prompt,
                model=model,
                mode=mode,
                strategy="multilevel_fast",
                call_name="multilevel_fast",
                timeout_tracker=timeout_tracker,
                llm_budget=llm_budget,
                progress_notify=progress_notify,
            )
            out = _parse_mindmap_output_json(raw)
            if not out.branches:
                raise ValueError("No branches")
            flat = _mindmap_output_to_flat_nodes(out, root_title)
            if len(flat) < 3:
                raise ValueError("Too few nodes")
            return flat, "multilevel_fast"
        except BaseException as e:
            last_err = e
            # Nếu đã hết budget thì không retry
            if not llm_budget.can_call():
                print(f"[multilevel_fast] Budget exhausted after attempt {attempt + 1}")
                break
            if attempt < max_retries:
                print(f"[multilevel_fast] attempt {attempt+1}/{max_retries+1} failed: {e}")
            continue
    raise RuntimeError(f"multilevel_fast failed: {last_err}")


# ========== DETERMINISTIC BRANCHES (final fallback) ==========
def _get_deterministic_branches(chunks: list[str], root_title: str) -> list[dict]:
    """Fallback cuối cùng - deterministic branches không gọi LLM."""
    if not chunks:
        return [{"id": "root", "parent": None, "title": root_title}]
    n = min(len(chunks), 6)
    step = max(1, len(chunks) // n)
    nodes = [{"id": "root", "parent": None, "title": root_title}]
    for i in range(n):
        idx = i * step
        chunk = chunks[idx] if idx < len(chunks) else chunks[-1]
        title = (chunk or "")[:80].strip()
        if not title:
            title = f"Phần {i+1}"
        nodes.append({"id": f"branch-{i}", "parent": "root", "title": title})
    return nodes


def deterministic_basic_branches(chunks: list[str], root_title: str = "Mindmap") -> list[dict]:
    """
    Fallback không cần LLM - tạo mindmap từ keywords/simple extraction.
    Dùng TF-IDF nếu có, fallback đơn giản nếu không.
    """
    if not chunks:
        return [{"id": "root", "parent": None, "title": root_title}, {"id": "empty", "parent": "root", "title": "Chưa có đủ nội dung"}]

    # Trích keywords đơn giản từ chunks
    all_text = " ".join(chunks[:20])  # Chỉ dùng 20 chunks đầu
    words = re.findall(r'[\wÀ-ỹ]{4,}', all_text.lower())

    # Đếm tần suất
    word_freq: Counter = Counter(words)

    # Loại bỏ stopwords tiếng Việt đơn giản
    stopwords = {'và', 'của', 'là', 'có', 'được', 'trong', 'cho', 'với', 'để', 'từ', 'này', 'các', 'những', 'không', 'theo', 'cũng', 'đã', 'một', 'về', 'ra', 'hay', 'hoặc', 'nên', 'khi', 'nếu', 'thì', 'sẽ', 'đến', 'bởi', 'như', 'vào', 'trên', 'năm', 'quá', 'hơn', 'còn', 'chỉ', 'tại', 'sau', 'tới', 'lại', 'mà', 'đó'}
    for sw in stopwords:
        word_freq.pop(sw, None)

    # Lấy top keywords
    top_keywords = [word for word, _ in word_freq.most_common(30)]

    # Chọn 4-7 topic chính
    num_topics = min(7, max(4, len(top_keywords) // 4))
    selected_keywords = top_keywords[:num_topics] if top_keywords else []

    # Nếu không có keywords đủ, fallback
    if len(selected_keywords) < 3:
        return _get_deterministic_branches(chunks, root_title)

    nodes = [{"id": "root", "parent": None, "title": root_title}]
    for i, keyword in enumerate(selected_keywords):
        # Tìm chunk liên quan
        related_chunk = next((c for c in chunks if keyword in c.lower()), None)
        if related_chunk:
            # Lấy 1-3 câu đầu từ chunk liên quan
            sentences = re.split(r'[.!?]', related_chunk)
            child_titles = []
            for sent in sentences[:3]:
                sent = sent.strip()
                if len(sent) > 15:
                    child_titles.append(sent[:60])
            if not child_titles:
                child_titles = [keyword.title()]
        else:
            child_titles = [keyword.title()]

        topic_id = f"topic-{i}"
        nodes.append({"id": topic_id, "parent": "root", "title": keyword.title()})

        # Thêm children (2-3 children mỗi topic)
        for j, child_title in enumerate(child_titles[:3]):
            nodes.append({"id": f"{topic_id}-c-{j}", "parent": topic_id, "title": child_title})

    return nodes


# ========== LLM INVOCATION WRAPPER ==========
def call_mindmap_llm(
    system_prompt: str,
    user_prompt: str,
    model: str,
    mode: str,
    strategy: str,
    call_name: str,
    timeout_tracker: TimeoutTracker,
    llm_budget: LlmCallBudget,
    progress_notify: Callable[[int, str], None],
) -> str:
    """LLM wrapper voi timeout that, budget tracking, va heartbeat log.

    - requests.post timeout=(5, actual_timeout) dam bao timeout that.
    - Heartbeat log moi 30s khi cho LLM.
    - Khong dung ThreadPoolExecutor.
    """
    # Check budget trước
    if not llm_budget.can_call():
        raise RuntimeError(f"[MindMap] LLM call budget exceeded mode={mode} budget={llm_budget.max_calls}")

    # Tính actual timeout = min(per_call_timeout, remaining_job_seconds - 10)
    remaining = timeout_tracker.time_remaining()
    per_call_timeout = get_llm_timeout_for_mode(mode)
    actual_timeout = min(per_call_timeout, max(1, remaining - 10))

    # Nếu actual_timeout < 15s, không đủ thời gian cho LLM call
    if actual_timeout < 15:
        raise TimeoutError(
            f"[MindMap] Not enough time for LLM call. remaining={remaining:.1f}s, min_needed=15s"
        )

    prompt_chars = len(system_prompt) + len(user_prompt)

    # Thử primary model trước
    primary_model = model
    fallback_model = get_fallback_model()

    # Log trước call
    print(
        f"[MindMap LLM Call] mode={mode} strategy={strategy} call={call_name} "
        f"model={primary_model} prompt_chars={prompt_chars} timeout={actual_timeout:.0f}s "
        f"remaining={remaining:.1f}s llmCalls={llm_budget.used}"
    )

    # Update progress với strategy name
    progress_notify(55, f"Đang gọi AI tạo mindmap: {call_name}...")

    # Track timing
    timeout_tracker.start_llm_call()
    call_start = time_module.time()
    last_heartbeat = call_start
    HEARTBEAT_INTERVAL = 30  # Log heartbeat moi 30s

    def _check_heartbeat(elapsed: float) -> None:
        """Log heartbeat neu cho lAu."""
        nonlocal last_heartbeat
        if elapsed - last_heartbeat >= HEARTBEAT_INTERVAL:
            last_heartbeat = elapsed
            print(f"[MindMap LLM Waiting] call={call_name} elapsed={elapsed:.0f}s remaining={remaining:.0f}s")
            progress_notify(60, f"AI dang xu ly ({elapsed:.0f}s)...")

    # Thử primary model
    try:
        raw = _invoke_mindmap_ollama_once(
            system_prompt,
            user_prompt,
            primary_model,
            actual_timeout,
        )

        elapsed = timeout_tracker.end_llm_call()
        _check_heartbeat(elapsed)
        print(
            f"[MindMap LLM Done] call={call_name} model={primary_model} elapsed={elapsed:.2f}s output_chars={len(raw)}"
        )
        llm_budget.register(call_name, primary_model, prompt_chars, actual_timeout, elapsed)
        return raw

    except (TimeoutError, Exception) as primary_error:
        elapsed = timeout_tracker.end_llm_call()
        _check_heartbeat(elapsed)
        error_type = type(primary_error).__name__
        
        # Rõ ràng log khi timeout
        if isinstance(primary_error, TimeoutError):
            print(
                f"[MindMap LLM Error] call={call_name} error=TimeoutError "
                f"actual_timeout={actual_timeout:.0f}s elapsed={elapsed:.1f}s"
            )
        else:
            print(
                f"[MindMap LLM Error] call={call_name} model={primary_model} elapsed={elapsed:.1f}s "
                f"error={error_type}: {primary_error}"
            )

        # IMPORTANT: Fast/Balanced không retry/fallback khi timeout - dùng deterministic
        if isinstance(primary_error, TimeoutError):
            print(
                f"[MindMap Fallback] mode={mode} reason=llm_timeout "
                f"call={call_name} actual_timeout={actual_timeout:.0f}s "
                f"using=deterministic_basic_branches"
            )
            # Re-raise để caller xử lý fallback
            raise

        # Thử fallback model nếu:
        # 1. Còn budget
        # 2. Còn deadline (> 20s)
        # 3. Fallback model khác primary model
        if llm_budget.can_call() and remaining > 20 and fallback_model != primary_model:
            print(f"[MindMap] Trying fallback model: {fallback_model}")

            # Recalculate timeout cho fallback
            remaining_after_primary = timeout_tracker.time_remaining()
            fallback_timeout = min(per_call_timeout, max(1, remaining_after_primary - 10))

            if fallback_timeout >= 15:
                timeout_tracker.start_llm_call()
                try:
                    raw = _invoke_mindmap_ollama_once(
                        system_prompt,
                        user_prompt,
                        fallback_model,
                        fallback_timeout,
                    )

                    elapsed = timeout_tracker.end_llm_call()
                    print(
                        f"[MindMap LLM Done] call={call_name} model={fallback_model} (fallback) "
                        f"elapsed={elapsed:.2f}s output_chars={len(raw)}"
                    )
                    llm_budget.register(call_name, fallback_model, prompt_chars, fallback_timeout, elapsed)
                    return raw

                except (TimeoutError, Exception) as fallback_error:
                    elapsed = timeout_tracker.end_llm_call()
                    print(
                        f"[MindMap LLM Error] call={call_name} model={fallback_model} (fallback) "
                        f"elapsed={elapsed:.1f}s error={type(fallback_error).__name__}: {fallback_error}"
                    )
                    llm_budget.register(call_name, fallback_model, prompt_chars, fallback_timeout, elapsed, error=str(fallback_error))

        # Không còn deadline hoặc không thể fallback - re-raise
        llm_budget.register(call_name, primary_model, prompt_chars, actual_timeout, elapsed, error=str(primary_error))
        raise


def _invoke_mindmap_ollama_once(
    system_prompt: str,
    user_prompt: str,
    model: str,
    timeout_sec: float,
) -> MindmapOutput:
    """
    Goi Ollama de sinh mindmap voi timeout that.

    Dung requests.post truc tiep thay vi ChatOllama de dam bao timeout.
    Khong dung ThreadPoolExecutor voi 'with' vi se block khi shutdown(wait=True).
    """
    import requests as _requests
    import time as _time

    host = (os.getenv("OLLAMA_HOST") or "http://localhost:11434").strip().rstrip("/")
    url = f"{host}/api/chat"

    schema_dict = MindmapOutput.model_json_schema()

    payload = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt or ""},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": 0.1,
            "num_predict": 1200,
        },
        "format": schema_dict,
    }

    # timeout=(connect, read) - dam bao timeout that
    start = _time.time()
    try:
        resp = _requests.post(url, json=payload, timeout=(5, timeout_sec))
        resp.raise_for_status()
        data = resp.json()
        elapsed = _time.time() - start

        # Log success
        print(f"[Ollama Direct] model={model} elapsed={elapsed:.2f}s")

        content = data.get("message", {}).get("content", "")
        return _parse_mindmap_output_json(content)
    except _requests.exceptions.Timeout:
        elapsed = _time.time() - start
        raise TimeoutError(f"Ollama request timed out after {elapsed:.1f}s (timeout={timeout_sec}s)") from None
    except _requests.exceptions.RequestException as e:
        elapsed = _time.time() - start
        raise RuntimeError(f"Ollama request failed after {elapsed:.1f}s: {e}") from None


def _call_llm_schema(system_prompt: str, user_prompt: str, model: str, timeout_sec: float) -> MindmapOutput:
    return _invoke_mindmap_ollama_once(system_prompt, user_prompt, model, timeout_sec)


def _content_hash(source_names: list[str], chunks: list[dict], mode: str, strategy: str) -> str:
    """Generate cache key including mode for mindmap nodes."""
    key = "|".join(sorted(str(s).strip() for s in source_names if str(s).strip()))
    n = len(chunks)
    total_chars = sum(len(c.get("text", "") or "") for c in chunks)
    chunks_text = "|".join((c.get("text", "") or "") for c in chunks)
    chunks_hash = hashlib.sha256(chunks_text.encode("utf-8")).hexdigest()
    embedding_model = os.environ.get("EMBEDDING_MODEL_NAME", "unknown")
    embedding_dim = 0
    for c in chunks:
        emb = c.get("embedding")
        if isinstance(emb, list) and len(emb) > 0:
            embedding_dim = len(emb)
            break
    # Include mode-specific model in cache key
    model_for_mode = get_mindmap_model_for_mode(mode)
    raw = f"{key}|{n}|{total_chars}|{chunks_hash}|{strategy}|{model_for_mode}|{CACHE_VERSION}|{embedding_model}|{embedding_dim}|{mode}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ========== STRATEGY SELECTION (PHẦN 4) ==========
def select_mindmap_strategy(chunks: list[dict], force_strategy: Optional[str] = None, mode: str = MODE_BALANCED) -> str:
    """
    Chọn strategy phù hợp dựa trên mode và kích thước dữ liệu.

    fast:
    - < 8000 chars và <= 6 chunks => single_call_schema
    - else => mindmap_v2
    - NOT iterative, NOT cmgn_light

    balanced:
    - < 6000 chars và <= 4 chunks => single_call_schema
    - <= 25 chunks, < 30000 chars => mindmap_v2
    - > 30000 chars hoặc > 60 chunks => multilevel_fast
    - NOT cmgn_light mặc định, NOT iterative

    quality:
    - Giữ logic gốc với iterative, cmgn
    """

    n_chunks = len(chunks or [])
    total_chars = sum(len(c.get("text", "") or "") for c in chunks or [])

    # Nếu force_strategy được chỉ định, áp dụng mode guards
    if force_strategy and force_strategy != "auto":
        # Block slow strategies for non-quality modes
        if mode != MODE_QUALITY and force_strategy in {"cmgn", "cmgn_light", "iterative"}:
            print(f"[MindMap Strategy] force_strategy={force_strategy} blocked for mode={mode}, using mindmap_v2")
            return "mindmap_v2"
        return force_strategy

    # Auto-select based on mode
    if mode == MODE_FAST:
        if total_chars < 8000 and n_chunks <= 6:
            return "single_call_schema"
        return "mindmap_v2"

    if mode == MODE_BALANCED:
        # Balanced ưu tiên nhanh - single call cho nhỏ, mindmap_v2 cho trung bình
        if total_chars < 6000 and n_chunks <= 4:
            return "single_call_schema"
        if n_chunks <= 25 and total_chars < 30000:
            return "mindmap_v2"
        # Balanced dùng multilevel_fast thay vì cmgn_light
        return "multilevel_fast"

    # quality mode - giữ logic gốc, cho phép iterative và cmgn
    if total_chars < 2500:
        return "single_call_schema"
    if n_chunks <= 4 and total_chars < 8000:
        return "single_call_schema"
    if n_chunks <= 18 and total_chars < 18000:
        return "mindmap_v2"
    if n_chunks <= 45 and total_chars < 50000:
        return "cmgn"
    return "iterative"


# ========== VISUAL DIAGRAM (PHẦN 7) ==========
def build_visual_diagram_by_mode(
    flat_nodes: list[dict],
    final_chunks: list[str],
    root_title: str,
    source_names: list[str],
    mode: str,
    timeout_tracker: TimeoutTracker | None = None,
    llm_budget: LlmCallBudget | None = None,
) -> tuple[dict, str]:
    """
    Build visual diagram based on generation mode.

    PHẦN 7: Fast/Balanced = deterministic only, KHÔNG gọi LLM visual
    """
    # Fast và Balanced: deterministic only
    if mode == MODE_FAST or mode == MODE_BALANCED:
        diagram = _flat_nodes_to_visual_diagram(flat_nodes, root_title, source_names)
        return diagram, "deterministic"

    # Quality: LLM first, fallback deterministic
    # Nhưng vẫn check budget và deadline
    if mode == MODE_QUALITY:
        # Check budget
        if llm_budget and not llm_budget.can_call():
            print(f"[MindMap] Visual LLM skipped - budget exhausted")
            return _flat_nodes_to_visual_diagram(flat_nodes, root_title, source_names), "deterministic"

        # Check deadline
        if timeout_tracker and timeout_tracker.is_near_deadline(threshold=30):
            print(f"[MindMap] Visual LLM skipped - near deadline")
            return _flat_nodes_to_visual_diagram(flat_nodes, root_title, source_names), "deterministic"

        try:
            diagram = _build_visual_diagram_llm(
                final_chunks, flat_nodes, root_title, source_names,
                get_mindmap_model_for_mode(mode),
                lambda p, m: None
            )
            if llm_budget:
                llm_budget.register("visual_diagram_llm", get_mindmap_model_for_mode(mode), 0, 0, 0)
            return diagram, "llm"
        except Exception as e:
            print(f"[MindMap] Quality visual LLM failed, fallback deterministic: {e}")

    return _flat_nodes_to_visual_diagram(flat_nodes, root_title, source_names), "deterministic"


def _build_visual_diagram_llm(
    final_chunks: list[str],
    flat_nodes: list[dict],
    root_title: str,
    source_names: list[str],
    model: str,
    progress_notify: Callable[[int, str], None],
) -> dict:
    """Build visual diagram using LLM."""
    try:
        progress_notify(88, "Đang tạo visual diagram...")
    except Exception:
        pass
    try:
        outline_lines = []
        for n in (flat_nodes or [])[:80]:
            outline_lines.append(f"- id={n.get('id')} | parent={n.get('parent')} | title={n.get('title')}")
        chunks = [str(c).strip() for c in (final_chunks or []) if str(c).strip()]
        context = "\n\n---\n\n".join(chunks[:12])
        sources_line = ", ".join(source_names[:12]) if source_names else ""
        system_prompt = """
Bạn là AI visual diagram designer giống Napkin AI.
Nhiệm vụ: chuyển nội dung tài liệu và outline mindmap thành visual diagram dễ hiểu.

Hãy chọn loại biểu đồ phù hợp:
- concept_map: khi nội dung là khái niệm nhiều nhánh
- flowchart: khi có quy trình/các bước
- comparison: khi có so sánh
- cycle: khi có vòng lặp
- timeline: khi có trình tự thời gian
- cause_effect: khi có nguyên nhân-kết quả
- funnel: khi có quá trình lọc/thu hẹp/chuyển đổi

Trả về DUY NHẤT JSON hợp lệ:
{
  "diagramType": "concept_map|flowchart|comparison|cycle|timeline|cause_effect|funnel",
  "title": "...",
  "summary": "...",
  "nodes": [{"id": "v-1", "title": "...", "subtitle": "...", "type": "root|concept|process|input|output|problem|solution|example|risk|insight|timeline|metric", "group": "main|data|process|result|risk|example|other", "level": 0, "icon": "brain|database|workflow|target|alert|check|lightbulb|clock|sparkles", "order": 0}],
  "edges": [{"id": "e-1", "source": "v-1", "target": "v-2", "label": "...", "type": "relates_to|leads_to|causes|supports|contrasts|contains"}],
  "groups": [{"id": "process", "label": "Quy trình", "color": "blue"}]
}

Quy tắc:
- 6 đến 18 nodes là tốt nhất.
- Không quá 24 nodes.
- Luôn có 1 node type="root", level=0.
- Mỗi title 2-8 từ.
- subtitle tối đa 14 từ.
- Tiếng Việt tự nhiên.
- Không thêm markdown.
- Không giải thích ngoài JSON.
""".strip()
        user_prompt = f"""
Tiêu đề gốc: {root_title}
Nguồn: {sources_line}

Outline mindmap hiện có:
{chr(10).join(outline_lines)}

Nội dung tài liệu:
{context}

Hãy tạo visual diagram semantic kiểu Napkin AI.
""".strip()
        raw = ask_ai(
            user_prompt,
            system_prompt=system_prompt,
            model=model,
            options={"temperature": 0.15},
        )
        out = _parse_visual_diagram_json(raw)
        if len(out.nodes) < 3:
            raise ValueError("Visual diagram quá ít nodes")
        if not out.edges:
            raise ValueError("Visual diagram không có edges")
        valid_ids = {node.id for node in out.nodes}
        clean_edges = [
            edge for edge in out.edges
            if edge.source in valid_ids and edge.target in valid_ids and edge.source != edge.target
        ]
        if not clean_edges:
            raise ValueError("Visual diagram không có edge hợp lệ")
        out.edges = clean_edges[:40]
        out.nodes = out.nodes[:24]
        return out.model_dump()
    except Exception as exc:
        print(f"⚠️ Visual diagram LLM failed: {exc}")
        return _flat_nodes_to_visual_diagram(flat_nodes, root_title, source_names)


def _diagram_quality_low(diagram: dict) -> bool:
    """Check if diagram is too poor quality for balanced mode."""
    nodes = diagram.get("nodes", [])
    edges = diagram.get("edges", [])
    if len(nodes) < 5:
        return True
    if len(edges) < 3:
        return True
    root_nodes = [n for n in nodes if n.get("type") == "root"]
    if not root_nodes:
        return True
    return False


# ========== MAIN GENERATION FUNCTION ==========
def run_mindmap_generation(
    index_meta_path: Path,
    source_names: list[str],
    strategy_requested: str = "auto",
    append_mindmap: Callable[[dict], None] | None = None,
    progress_cb: Optional[Callable[[int], None]] = None,
    generation_mode: str | None = None,
) -> dict:
    """
    Sinh mindmap với 3 generation modes: fast, balanced, quality.

    PHẦN 1: Log chi tiết LLM calls
    PHẦN 2: Hard deadline áp vào request timeout
    PHẦN 3: LLM call budget
    PHẦN 4: Balanced không dùng cmgn_light
    PHẦN 5: Fallback chain ưu tiên 1-call
    PHẦN 6: Retry policy theo mode
    PHẦN 7: Visual LLM tắt ở fast/balanced
    PHẦN 8: Context limit giảm
    PHẦN 9: Progress update khi LLM lâu
    PHẦN 10: Performance debug trong response
    PHẦN 11: Model theo mode
    """
    def _prog_msg(p: int, msg_vi: str) -> None:
        _notify_progress(progress_cb, p, msg_vi)

    # ========== PHASE 1: Normalize mode ==========
    if generation_mode and generation_mode in VALID_MODES:
        mode = generation_mode
    else:
        if strategy_requested and strategy_requested in VALID_MODES:
            mode = strategy_requested
        else:
            mode = DEFAULT_MODE

    # ========== PHASE 2: Normalize strategy ==========
    if strategy_requested and strategy_requested in {
        "auto", "single_call_schema", "mindmap_v2", "cmgn_light",
        "cmgn", "multilevel_fast", "multilevel", "iterative"
    }:
        strategy = strategy_requested
    else:
        strategy = "auto"

    # ========== PHASE 3: Guard slow strategies ==========
    if strategy == "iterative" and mode != MODE_QUALITY:
        print(f"[MindMap Guard] iterative blocked for mode={mode}, using auto")
        strategy = "auto"

    if mode in {MODE_FAST, MODE_BALANCED} and strategy in {"cmgn", "cmgn_light", "iterative"}:
        print(f"[MindMap Guard] strategy={strategy} blocked for mode={mode}, using auto")
        strategy = "auto"

    # ========== PHASE 4: Initialize tracking ==========
    job_timeout = get_job_timeout_for_mode(mode)
    llm_timeout = get_llm_timeout_for_mode(mode)
    timeout_tracker = TimeoutTracker(mode=mode, job_timeout=job_timeout, llm_timeout=llm_timeout)
    llm_budget = LlmCallBudget(mode=mode)

    # Get model theo mode (PHẦN 11)
    model = get_mindmap_model_for_mode(mode)

    job_deadline = timeout_tracker.deadline
    print(
        f"[MindMap Config] mode={mode} strategy={strategy} "
        f"model={model} jobTimeout={job_timeout}s llmTimeout={llm_timeout}s "
        f"llmBudget={llm_budget.max_calls} contextLimit="
        f"{CONTEXT_LIMIT_FAST if mode == MODE_FAST else CONTEXT_LIMIT_BALANCED if mode == MODE_BALANCED else CONTEXT_LIMIT_QUALITY}"
    )

    timing = TimingLogger(mode, strategy)
    timing.start("load_chunks")

    if len(source_names) == 1:
        root_title = Path(source_names[0]).stem or "Mind Map"
    else:
        display_candidates = [Path(name).stem for name in source_names if Path(name).stem]
        if not display_candidates:
            root_title = "Mind Map tổng hợp"
        else:
            preview = ", ".join(display_candidates[:3])
            if len(display_candidates) > 3:
                preview += f" + {len(display_candidates) - 3} nguồn"
            root_title = f"Tổng hợp: {preview}"

    _prog_msg(5, "Đang đọc chỉ mục...")
    with open(index_meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    def normalize_video_name(name: str) -> str:
        if not name:
            return ""
        name = Path(name).name if '/' in name or '\\' in name else name
        cleaned = unicodedata.normalize('NFKD', name.strip()).replace('\u00a0', ' ')
        cleaned = cleaned.replace('.mp4', '')
        cleaned = re.sub(r'_\d{8}_\d{6}$', '', cleaned)
        return cleaned.strip().lower()

    normalized_sources = set()
    for s in source_names:
        normalized = normalize_video_name(s)
        if normalized:
            normalized_sources.add(normalized)

    all_chunks_with_meta = []
    for key, m in meta.items():
        video_raw = m.get("video", "").strip()
        if not video_raw:
            continue
        video_clean = normalize_video_name(video_raw)
        if video_clean in normalized_sources:
            all_chunks_with_meta.append({
                "text": m.get("text", ""),
                "parent_id": m.get("parent_id"),
                "sub_order": m.get("sub_order"),
                "total_parts": m.get("total_parts"),
                "is_subchunk": m.get("is_subchunk", False),
                "key": key,
                "embedding": m.get("embedding"),
            })

    timing.start("merge")
    _prog_msg(15, "Đang gộp sub-chunks...")
    if not all_chunks_with_meta:
        flat_nodes = _get_deterministic_branches([], root_title)
        strategy_used = "fallback"
    else:
        merged_logical: list[dict] = []
        sub_groups = {}
        logical_normal: list[dict] = []
        chunk_keys_by_parent = {}

        for item in all_chunks_with_meta:
            is_subchunk = item.get("is_subchunk", False)
            parent_id = item.get("parent_id")
            item_key = str(item.get("key", "")).strip()
            text = item.get("text", "").strip()
            if not text:
                continue
            if is_subchunk and parent_id:
                parent_key = str(parent_id).strip()
                if parent_key:
                    if parent_key not in sub_groups:
                        sub_groups[parent_key] = []
                    sub_groups[parent_key].append(item)
            else:
                logical_normal.append({"text": text, "embedding": item.get("embedding")})
                if item_key:
                    chunk_keys_by_parent[item_key] = item

        for parent_key, subs in sub_groups.items():
            if parent_key in chunk_keys_by_parent:
                continue
            subs.sort(key=lambda x: (x.get("sub_order") or 0, x.get("text", "")))
            merged_text = "\n\n".join(
                sub.get("text", "").strip() for sub in subs if sub.get("text", "").strip()
            )
            if merged_text.strip():
                emb_vecs = []
                for sub in subs:
                    e = sub.get("embedding")
                    if isinstance(e, list) and len(e) > 0:
                        vec = np.asarray(e, dtype=np.float32)
                        if vec.ndim == 1:
                            vec = vec.reshape(1, -1)
                        emb_vecs.append(vec)
                avg_emb = None
                if emb_vecs:
                    stacked = safe_stack_vectors(emb_vecs, context="mindmap_subchunk_merge")
                    if stacked is not None and stacked.shape[0] > 0:
                        avg_emb = np.mean(stacked, axis=0).astype(float).tolist()
                merged_logical.append({"text": merged_text.strip(), "embedding": avg_emb})

        final_logical_chunks = logical_normal + merged_logical
        final_chunks_text = [c["text"] for c in final_logical_chunks]

        timing.start("strategy")
        _prog_msg(20, f"Đang chọn strategy cho mode {mode}...")
        selected_strategy = select_mindmap_strategy(final_logical_chunks, None, mode)

        timing.start("mindmap_llm")
        flat_nodes = None
        strategy_used = selected_strategy

        embed_model = get_embedding_model()

        def _embed_fn(tx):
            return embed_model.encode(tx, convert_to_numpy=True, show_progress_bar=False)

        # Build mindmap based on strategy
        try:
            # Check deadline before starting
            timeout_tracker.check_deadline("strategy_selection")

            # Check budget trước khi gọi LLM (PHẦN 3)
            if not llm_budget.can_call():
                print(f"[MindMap] LLM budget exhausted before strategy={selected_strategy}, using deterministic")
                flat_nodes = deterministic_basic_branches(final_chunks_text, root_title)
                strategy_used = "deterministic_basic_branches"
            elif selected_strategy == "single_call_schema":
                _prog_msg(30, "Đang sinh mindmap single call...")
                timeout_tracker.check_deadline("single_call_schema")
                timeout_tracker.record_llm_call("single_call_schema")
                flat_nodes, strategy_used = _build_mindmap_single_call(
                    final_chunks_text, source_names, model, _embed_fn, root_title, _prog_msg, mode,
                    timeout_tracker, llm_budget
                )
            elif selected_strategy == "mindmap_v2":
                _prog_msg(30, "Đang sinh mindmap với TF-IDF clustering...")
                timeout_tracker.check_deadline("mindmap_v2")
                timeout_tracker.record_llm_call("mindmap_v2")
                flat_nodes, strategy_used = _build_mindmap_v2(
                    final_logical_chunks, source_names, model, _embed_fn, root_title, _prog_msg, mode,
                    timeout_tracker, llm_budget
                )
            elif selected_strategy == "cmgn_light" and mode == MODE_QUALITY:
                # cmgn_light chỉ chạy cho quality
                _prog_msg(30, "Đang sinh mindmap CMGN light...")
                timeout_tracker.check_deadline("cmgn_light")
                timeout_tracker.record_llm_call("cmgn_coreference")
                timeout_tracker.record_llm_call("cmgn_mindmap")
                flat_nodes, strategy_used = _build_cmgn_light(
                    final_logical_chunks, source_names, model, root_title, _prog_msg, mode, timeout_tracker, llm_budget
                )
            elif selected_strategy == "multilevel_fast":
                _prog_msg(30, "Đang sinh mindmap multilevel...")
                timeout_tracker.check_deadline("multilevel_fast")
                timeout_tracker.record_llm_call("multilevel_fast")
                flat_nodes, strategy_used = _build_multilevel_fast(
                    final_chunks_text, source_names, model, _embed_fn, root_title, _prog_msg, mode,
                    timeout_tracker, llm_budget
                )
            elif selected_strategy == "cmgn" and mode == MODE_QUALITY:
                # quality mode với cmgn full (có critics)
                _prog_msg(30, "Đang sinh mindmap CMGN với critics...")
                timeout_tracker.check_deadline("cmgn")
                timeout_tracker.record_llm_call("cmgn")
                from services.mindmap.utils import generate_mindmap_cmgn
                flat_nodes = generate_mindmap_cmgn(
                    final_chunks_text, model=model, enable_critics=True
                )
                strategy_used = "cmgn"
            elif selected_strategy == "iterative" and mode == MODE_QUALITY:
                # quality mode với iterative
                _prog_msg(30, "Đang sinh mindmap iterative...")
                timeout_tracker.check_deadline("iterative")
                timeout_tracker.record_llm_call("iterative")
                from services.mindmap.utils import get_nested_mindmap, flatten_mindmap
                tree = get_nested_mindmap(final_chunks_text, model=model, enable_critics=True)
                flat_nodes = flatten_mindmap(tree)
                strategy_used = "iterative"
            else:
                # Fallback: dùng mindmap_v2 làm base cho mọi strategy khác
                _prog_msg(30, f"Đang sinh mindmap với strategy: {selected_strategy}...")
                timeout_tracker.check_deadline(f"fallback_{selected_strategy}")
                timeout_tracker.record_llm_call(f"fallback_{selected_strategy}")
                flat_nodes, strategy_used = _build_mindmap_v2(
                    final_logical_chunks, source_names, model, _embed_fn, root_title, _prog_msg, mode,
                    timeout_tracker, llm_budget
                )
        except TimeoutError:
            # TimeoutError đã được log trong check_deadline
            raise
        except Exception as exc:
            print(f"[MindMap] Strategy {selected_strategy} failed: {exc}")
            # Fallback chain theo mode (đã loại bỏ failed strategy)
            fallback_chain = get_fallback_chain(selected_strategy, mode)
            chain_str = " -> ".join(fallback_chain)
            print(f"[MindMap Fallback] mode={mode} chain={chain_str}")

            for fallback_strategy in fallback_chain:
                try:
                    # Check deadline trước mỗi fallback
                    timeout_tracker.check_deadline(f"fallback_{fallback_strategy}")

                    # Check budget trước mỗi fallback (PHẦN 3)
                    if not llm_budget.can_call():
                        print(f"[MindMap] Budget exhausted, using deterministic fallback")
                        flat_nodes = deterministic_basic_branches(final_chunks_text, root_title)
                        strategy_used = "deterministic_basic_branches"
                        break

                    _prog_msg(35, f"Đang thử fallback: {fallback_strategy}...")
                    if fallback_strategy == "single_call_schema":
                        timeout_tracker.record_llm_call("fallback_single_call")
                        flat_nodes, strategy_used = _build_mindmap_single_call(
                            final_chunks_text, source_names, model, _embed_fn, root_title, _prog_msg, mode,
                            timeout_tracker, llm_budget
                        )
                    elif fallback_strategy == "mindmap_v2":
                        timeout_tracker.record_llm_call("fallback_mindmap_v2")
                        flat_nodes, strategy_used = _build_mindmap_v2(
                            final_logical_chunks, source_names, model, _embed_fn, root_title, _prog_msg, mode,
                            timeout_tracker, llm_budget
                        )
                    elif fallback_strategy == "multilevel_fast":
                        timeout_tracker.record_llm_call("fallback_multilevel_fast")
                        flat_nodes, strategy_used = _build_multilevel_fast(
                            final_chunks_text, source_names, model, _embed_fn, root_title, _prog_msg, mode,
                            timeout_tracker, llm_budget
                        )
                    elif fallback_strategy == "deterministic_basic_branches":
                        flat_nodes = deterministic_basic_branches(final_chunks_text, root_title)
                        strategy_used = "deterministic_basic_branches"
                        timeout_tracker.record_llm_call("deterministic_basic_branches")
                    else:
                        continue
                    print(f"[MindMap] Fallback {fallback_strategy} succeeded")
                    break
                except TimeoutError:
                    print(f"[MindMap] Fallback {fallback_strategy} timed out, trying next...")
                    continue
                except Exception as fallback_exc:
                    print(f"[MindMap] Fallback {fallback_strategy} failed: {fallback_exc}")
                    continue

            if flat_nodes is None:
                flat_nodes = _get_deterministic_branches(final_chunks_text, root_title)
                strategy_used = "deterministic"

        # Cap nodes theo mode
        flat_nodes = cap_mindmap_nodes(flat_nodes or [], mode)

    timing.start("visual_diagram")
    _prog_msg(85, f"Đang tạo visual diagram (mode={mode})...")
    visual_diagram_mode = "deterministic"

    # Check deadline trước visual diagram
    if timeout_tracker.is_near_deadline(threshold=10):
        print(f"[MindMap] Near deadline, using deterministic visual")
        visual_diagram = _flat_nodes_to_visual_diagram(flat_nodes or [], root_title, source_names)
    else:
        try:
            timeout_tracker.check_deadline("visual_diagram")
            visual_diagram, visual_diagram_mode = build_visual_diagram_by_mode(
                flat_nodes or [],
                [c.get("text", "") for c in all_chunks_with_meta],
                root_title,
                source_names,
                mode,
                timeout_tracker=timeout_tracker,
                llm_budget=llm_budget,
            )
        except TimeoutError:
            print(f"[MindMap] Visual diagram timeout, using deterministic")
            visual_diagram = _flat_nodes_to_visual_diagram(flat_nodes or [], root_title, source_names)
            visual_diagram_mode = "deterministic"
        except Exception as exc:
            print(f"[MindMap] Visual diagram failed: {exc}")
            visual_diagram = _flat_nodes_to_visual_diagram(flat_nodes or [], root_title, source_names)
            visual_diagram_mode = "deterministic"

    timing.start("cache")
    _prog_msg(95, "Đang lưu vào cache...")

    if flat_nodes:
        # Sanitize trước khi lưu
        flat_nodes = sanitize_mindmap_nodes(flat_nodes)
        # Cập nhật root title
        root_node = next((n for n in flat_nodes if n.get("parent") is None), flat_nodes[0])
        root_node["title"] = root_title or root_node.get("title") or "Mind Map"

    # PHẦN 10: Performance debug
    perf_summary = llm_budget.summary()
    perf_summary["totalSec"] = timing.start_total if hasattr(timing, 'start_total') else 0.0
    perf_summary["deadlineHit"] = timeout_tracker.time_remaining() < 5
    perf_summary["fallbackUsed"] = strategy_used != selected_strategy
    perf_summary["visualDiagramMode"] = visual_diagram_mode

    mindmap_record = {
        "id": str(uuid.uuid4()),
        "title": root_title,
        "nodes": flat_nodes,
        "diagram": visual_diagram,
        "sources": source_names,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "strategy": strategy_used,
        "mode": mode,
        "visualDiagramMode": visual_diagram_mode,
        # PHẦN 10: Performance debug trong response
        "performance": perf_summary,
    }

    append_mindmap(mindmap_record)
    timing.start("save")
    _prog_msg(100, "Hoàn thành sơ đồ tư duy.")

    # Final timing log
    total_elapsed = time_module.time() - timing.start_total
    timeout_tracker.log_timeout_info()

    # Timeout warning if job exceeded expected timeout
    if total_elapsed > job_timeout + 10:
        print(
            f"[MindMap Timeout Warning] job exceeded expected timeout: "
            f"actual={total_elapsed:.1f}s expected={job_timeout}s "
            f"mode={mode} strategy={strategy_used} llmCalls={timeout_tracker.llm_calls_made}"
        )

    print(
        f"[MindMap] mode={mode} strategy={strategy_used} "
        f"nodes={len(flat_nodes or [])} visual={visual_diagram_mode} "
        f"total={total_elapsed:.1f}s llmCalls={timeout_tracker.llm_calls_made} "
        f"llmBudgetUsed={llm_budget.used}/{llm_budget.max_calls}"
    )

    # PHẦN 10: Log performance chi tiết
    print(f"[MindMap Performance] {json.dumps(perf_summary, ensure_ascii=False)}")

    return mindmap_record
