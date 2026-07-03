"""Schema v2 mindmap: MỘT artifact nodes(tree) + relations(cross-edges) + provenance."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

PIPELINE_VERSION = "skeleton_v1"
MAX_NODES = 120
MAX_RELATIONS = 20
KINDS = ("root", "section", "idea", "detail")
REL_TYPES = ("relates_to", "leads_to", "causes", "supports", "contrasts", "contains")
_KIND_PRIORITY = {"root": 0, "section": 1, "idea": 2, "detail": 3}


class NodeV2(BaseModel):
    id: str
    parent: Optional[str] = None
    kind: str = "idea"
    title: str
    note: str = ""
    chunk_refs: list[str] = Field(default_factory=list)
    order: int = 0


class RelationV2(BaseModel):
    source: str
    target: str
    type: str = "relates_to"
    label: str = ""


def content_hash(source_stems: list[str], chunk_texts: list[str]) -> str:
    """Cache key: đổi PIPELINE_VERSION là tự vô hiệu cache cũ."""
    h = hashlib.sha256()
    h.update(PIPELINE_VERSION.encode("utf-8"))
    for s in sorted(source_stems or []):
        h.update(b"\x00" + s.encode("utf-8"))
    for t in chunk_texts or []:
        h.update(b"\x01" + (t or "").encode("utf-8"))
    return h.hexdigest()


def sanitize_nodes(nodes: list[dict]) -> list[dict]:
    """Dedupe id, kind lạ → idea, mồ côi → về root, cap MAX_NODES (root/section ưu tiên giữ)."""
    seen: set[str] = set()
    clean: list[dict] = []
    for n in nodes or []:
        try:
            m = NodeV2(**{**n, "kind": n.get("kind") if n.get("kind") in KINDS else "idea"})
        except Exception:
            continue
        if not m.id or m.id in seen or not (m.title or "").strip():
            continue
        seen.add(m.id)
        clean.append(m.model_dump())
    root = next((n for n in clean if n["parent"] is None or n["kind"] == "root"), None)
    if root is None:
        return []
    root["parent"], root["kind"] = None, "root"
    ids = {n["id"] for n in clean}
    for n in clean:
        if n["id"] != root["id"] and (n["parent"] not in ids or n["parent"] == n["id"]):
            n["parent"] = root["id"]
    if len(clean) > MAX_NODES:
        clean.sort(key=lambda n: (_KIND_PRIORITY.get(n["kind"], 9), n["order"]))
        kept = clean[:MAX_NODES]
        kept_ids = {n["id"] for n in kept}
        kept = [n for n in kept if n["parent"] is None or n["parent"] in kept_ids]
        clean = kept
    return clean


def validate_relations(relations: list[dict], nodes: list[dict]) -> list[dict]:
    ids = {n["id"] for n in nodes or []}
    tree_edges = {(n["parent"], n["id"]) for n in nodes or [] if n.get("parent")}
    out: list[dict] = []
    seen: set[tuple] = set()
    for r in relations or []:
        try:
            m = RelationV2(**{**r, "type": r.get("type") if r.get("type") in REL_TYPES else "relates_to"})
        except Exception:
            continue
        key = (m.source, m.target)
        if (m.source not in ids or m.target not in ids or m.source == m.target
                or key in tree_edges or (key[1], key[0]) in tree_edges or key in seen):
            continue
        seen.add(key)
        out.append(m.model_dump())
        if len(out) >= MAX_RELATIONS:
            break
    return out


def build_record(*, title: str, sources: list[str], nodes: list[dict], relations: list[dict],
                 content_hash_value: str, model: str, elapsed_sec: float,
                 degraded_missing: list[str]) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "schema_version": 2,
        "title": title,
        "sources": list(sources or []),
        "content_hash": content_hash_value,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "nodes": nodes,
        "relations": relations,
        "generator": {
            "pipeline": PIPELINE_VERSION,
            "model": model,
            "elapsed_sec": round(float(elapsed_sec), 1),
            "degraded": bool(degraded_missing),
            "missing": list(degraded_missing or []),
        },
    }
