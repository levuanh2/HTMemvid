"""
Phase 2A: Mindmap output validation (Pydantic) — tương thích API hiện tại (flat nodes).
Khi bật MINDMAP_SCHEMA_STRICT=1, mindmap graph sẽ validate record trước Finalize.
"""
from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field


class MindmapFlatNode(BaseModel):
    """Một nút mindmap phẳng (đúng format FE: id, parent, title)."""

    id: str = Field(..., min_length=1)
    parent: str | None = None
    title: str = Field(..., min_length=1)


class MindmapFlatRecord(BaseModel):
    """Record tối thiểu từ pipeline sinh mindmap."""

    nodes: list[MindmapFlatNode] = Field(default_factory=list)


def validate_mindmap_record(record: dict[str, Any]) -> dict[str, Any]:
    """
    Parse & normalize dict record; raise ValueError nếu không đạt schema.
    """
    if not isinstance(record, dict):
        raise ValueError("mindmap record must be a dict")
    nodes_raw = record.get("nodes")
    if not isinstance(nodes_raw, list):
        raise ValueError("mindmap.nodes must be a list")
    max_n = int(os.environ.get("MINDMAP_MAX_NODES", "200"))
    if max_n > 0 and len(nodes_raw) > max_n:
        nodes_raw = nodes_raw[:max_n]
    # pydantic sẽ ép kiểu từng phần tử dict -> MindmapFlatNode
    parsed = MindmapFlatRecord.model_validate({"nodes": nodes_raw})
    out = dict(record)
    out["nodes"] = [n.model_dump() for n in parsed.nodes]
    return out


def should_validate_schema() -> bool:
    return (os.getenv("MINDMAP_SCHEMA_STRICT", "0") or "").strip().lower() in ("1", "true", "yes", "on")
