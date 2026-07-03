# Mindmap Skeleton-first v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thay pipeline sinh mindmap (3 mode × 7 strategies, 2113 dòng) bằng pipeline skeleton-first 4 stage trên LangGraph 5 node, schema v2 một artifact + sqlite store + cache thật + cancel thật, và FE "Bản đồ tri thức" với ngăn kéo bằng chứng.

**Architecture:** Skeleton dựng deterministic từ `heading_path` (đã persist trong index metadata), LLM chỉ enrich từng nhánh (song song) + trích quan hệ chéo (1 call). Monolith gom input (`app/domains/mindmap/input_collector`), worker/pipeline stateless (`services/mindmap/pipeline/`), record v2 lưu `memory/mindmaps.sqlite`. LLM chết ở stage nào → record vẫn ra kèm `degraded`.

**Tech Stack:** Python/Flask, langgraph 0.2.x (PIN), pydantic 2.10 (PIN <2.11), sqlite3, sklearn TF-IDF (đã có), React 19 + ReactFlow 11 + ELK (đã có), vitest (mới, FE devDep), html-to-image (mới, FE dep).

**Spec:** `docs/superpowers/specs/2026-07-03-mindmap-redesign-design.md`

## Global Constraints

- KHÔNG nâng langgraph/langchain (pin 0.2.x/0.3.x — ormsgpack bị WDAC chặn); KHÔNG nâng pydantic ≥2.11 (vỡ StateGraph TypedDict NotRequired).
- Chạy pytest bằng **global `python`**, từ thư mục `BE/`: `python -m pytest tests/<file> -v`.
- Sau mọi thay đổi dependency BE: `python -c "import app.graphs.query_graph"` phải OK.
- Đổi `MindmapState` PHẢI có test dựng graph THẬT (bài học conftest-mock).
- Timeout LLM chỉ bao inference; KHÔNG dùng `with ThreadPoolExecutor` cho block timeout (dùng executor thủ công + `shutdown(wait=False)`).
- JSON repair phải string-aware — tái dùng `_repair_json_text` (không viết regex mù mới).
- Định danh source LUÔN qua `shared/source_id.py::canonical_source_stem`.
- Text chunk LUÔN qua `app.domains.vectorstore.chunk_text_store` (ưu tiên sqlite).
- `SKIP_MODEL_LOAD=1` trong test → mọi hàm chạm model thật phải no-op an toàn.
- Heading separator là `" > "` (khớp `chunking.py::_heading_path`).
- Sau khi xong: cập nhật `.playbook/` (mandatory rule của repo).
- Branch làm việc: tạo `feat/mindmap-skeleton-v2` từ `refactor/microservices-restructure`. Working tree đang có thay đổi dở của effort khác (summarize modes) — KHÔNG add các file đó vào commit của plan này.

## Codex dispatch

Task đánh dấu **[CODEX]** giao cho codex CLI, chạy song song khi dependency của nó đã xong:

```bash
codex exec -C E:/memvid_NCKH/MemVid_New -s workspace-write --skip-git-repo-check "<dán nguyên văn nội dung task, gồm code + lệnh test>"
```

Sau mỗi task codex: Claude review diff + chạy test trước khi tick. Codex KHÔNG được sửa file ngoài danh sách `Files` của task.

## File Structure

```
BE/services/mindmap/jsonrepair.py          # MỚI — _repair_json_text chuyển từ worker.py
BE/services/mindmap/pipeline/__init__.py   # MỚI — export run_* các stage
BE/services/mindmap/pipeline/schema.py     # MỚI — pydantic v2 + sanitize + content_hash
BE/services/mindmap/pipeline/skeleton.py   # MỚI — Stage 0 (0 LLM)
BE/services/mindmap/pipeline/enrich.py     # MỚI — Stage 1 (LLM song song theo nhánh)
BE/services/mindmap/pipeline/relations.py  # MỚI — Stage 2 (1 LLM call)
BE/app/domains/mindmap/__init__.py         # MỚI
BE/app/domains/mindmap/input_collector.py  # MỚI — monolith gom input (thay worker tự đọc đĩa)
BE/app/domains/mindmap/store.py            # MỚI [CODEX] — mindmaps.sqlite CRUD + migrate json
BE/app/domains/jobs/jobs_store.py          # SỬA [CODEX] — cancel_requested
BE/app/graphs/state.py                     # SỬA — MindmapState v2
BE/app/graphs/mindmap_graph.py             # VIẾT LẠI — 5 node
BE/app/wiring.py                           # SỬA — deps mindmap mới
BE/app/clients/mindmap_factory.py          # SỬA — get_mindmap_pipeline
BE/app/main.py                             # SỬA — endpoints + run_mindmap_job + bỏ dict in-memory
BE/services/mindmap/worker.py              # XOÁ PHẦN LỚN [CODEX] — sau khi suite xanh
BE/services/mindmap/server.py + proto      # SỬA [CODEX] — per-stage RPC (cuối)
FE/src/utils/mindmapNormalize.js           # MỚI — v1/v2 → model hiển thị (pure)
FE/src/utils/api.js                        # SỬA — force/cancel/chunk-text
FE/src/components/mindmap/*                # MỚI — tách từ MindMapModal.jsx 2622 dòng
docs/MINDMAP_WORKFLOW.md + .playbook/*     # SỬA — cuối
```

---

## Phase A — BE pipeline core

### Task 1: Tách `_repair_json_text` ra module dùng chung

**Files:**
- Create: `BE/services/mindmap/jsonrepair.py`
- Modify: `BE/services/mindmap/worker.py` (xoá hàm, import lại)
- Test: `BE/tests/test_jsonrepair.py`

**Interfaces:**
- Produces: `services.mindmap.jsonrepair.repair_json_text(raw: str) -> str` — pipeline mới và worker cũ cùng dùng.

- [ ] **Step 1: Viết test fail**

```python
# BE/tests/test_jsonrepair.py
from services.mindmap.jsonrepair import repair_json_text

def test_strips_code_fence_and_trailing_comma():
    raw = '```json\n{"a": [1, 2,], "b": "x,y",}\n```'
    out = repair_json_text(raw)
    import json
    data = json.loads(out)
    assert data["a"] == [1, 2]
    assert data["b"] == "x,y"  # comma TRONG chuỗi không bị đụng

def test_plain_json_unchanged():
    import json
    assert json.loads(repair_json_text('{"k": "v"}')) == {"k": "v"}
```

- [ ] **Step 2: Chạy fail** — `cd BE && python -m pytest tests/test_jsonrepair.py -v` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement** — tạo `BE/services/mindmap/jsonrepair.py`: MOVE nguyên văn thân hàm `_repair_json_text` từ `worker.py` (dòng ~427-486) sang, đổi tên public:

```python
"""JSON repair string-aware dùng chung cho mindmap pipeline (cũ + mới)."""
from __future__ import annotations

def repair_json_text(raw: str) -> str:
    # <DÁN NGUYÊN VĂN thân _repair_json_text hiện tại từ worker.py —
    #  logic quét ký tự, chỉ bỏ ',' trước '}'/']' khi NGOÀI chuỗi,
    #  strip ```json fence. KHÔNG viết lại logic.>
    ...
```

Trong `worker.py`: xoá định nghĩa `_repair_json_text`, thêm `from services.mindmap.jsonrepair import repair_json_text as _repair_json_text` (giữ tên cũ cho mọi call-site trong worker).

- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_jsonrepair.py tests/test_mindmap.py -v` → PASS (worker cũ vẫn xanh).

- [ ] **Step 5: Commit** — `git add BE/services/mindmap/jsonrepair.py BE/services/mindmap/worker.py BE/tests/test_jsonrepair.py && git commit -m "refactor(mindmap): extract string-aware repair_json_text to shared module"`

### Task 2: Schema v2 + sanitize + content_hash

**Files:**
- Create: `BE/services/mindmap/pipeline/__init__.py` (rỗng), `BE/services/mindmap/pipeline/schema.py`
- Test: `BE/tests/test_mindmap_schema_v2.py`

**Interfaces:**
- Produces (mọi task sau dựa vào):
  - `PIPELINE_VERSION = "skeleton_v1"`, `MAX_NODES = 120`, `MAX_RELATIONS = 20`
  - `KINDS = ("root", "section", "idea", "detail")`, `REL_TYPES = ("relates_to","leads_to","causes","supports","contrasts","contains")`
  - `class NodeV2(BaseModel)`: `id: str`, `parent: Optional[str]`, `kind: str`, `title: str`, `note: str = ""`, `chunk_refs: list[str] = []`, `order: int = 0`
  - `class RelationV2(BaseModel)`: `source: str`, `target: str`, `type: str = "relates_to"`, `label: str = ""`
  - `content_hash(source_stems: list[str], chunk_texts: list[str]) -> str`
  - `sanitize_nodes(nodes: list[dict]) -> list[dict]`
  - `validate_relations(relations: list[dict], nodes: list[dict]) -> list[dict]`
  - `build_record(*, title, sources, nodes, relations, content_hash_value, model, elapsed_sec, degraded_missing: list[str]) -> dict` — record v2 đúng spec §3.1

- [ ] **Step 1: Viết test fail**

```python
# BE/tests/test_mindmap_schema_v2.py
from services.mindmap.pipeline import schema as s

def _nodes():
    return [
        {"id": "n1", "parent": None, "kind": "root", "title": "Doc"},
        {"id": "n2", "parent": "n1", "kind": "section", "title": "A", "chunk_refs": ["1"]},
        {"id": "n3", "parent": "n2", "kind": "idea", "title": "a1"},
    ]

def test_content_hash_stable_and_order_insensitive():
    h1 = s.content_hash(["b", "a"], ["t1", "t2"])
    h2 = s.content_hash(["a", "b"], ["t1", "t2"])
    assert h1 == h2 and len(h1) == 64
    assert s.content_hash(["a"], ["t1"]) != s.content_hash(["a"], ["KHÁC"])

def test_sanitize_orphan_reparented_to_root_and_dedup():
    nodes = _nodes() + [
        {"id": "n9", "parent": "KHONG_TON_TAI", "kind": "idea", "title": "mồ côi"},
        {"id": "n2", "parent": "n1", "kind": "section", "title": "A trùng id"},
    ]
    out = s.sanitize_nodes(nodes)
    ids = [n["id"] for n in out]
    assert ids.count("n2") == 1
    orphan = next(n for n in out if n["id"] == "n9")
    assert orphan["parent"] == "n1"  # về root

def test_sanitize_caps_total_keeps_root_sections_first():
    nodes = [{"id": "root", "parent": None, "kind": "root", "title": "R"}]
    for i in range(5):
        nodes.append({"id": f"s{i}", "parent": "root", "kind": "section", "title": f"S{i}"})
    for i in range(300):
        nodes.append({"id": f"i{i}", "parent": f"s{i % 5}", "kind": "idea", "title": f"I{i}"})
    out = s.sanitize_nodes(nodes)
    assert len(out) <= s.MAX_NODES
    kinds = {n["kind"] for n in out}
    assert "root" in kinds and "section" in kinds

def test_validate_relations_drops_bad_and_caps():
    nodes = _nodes()
    rels = [
        {"source": "n2", "target": "n3", "type": "leads_to", "label": "dẫn tới"},   # trùng cạnh cây (n3.parent=n2) → bỏ
        {"source": "n2", "target": "n2", "type": "relates_to", "label": ""},        # self-loop → bỏ
        {"source": "n2", "target": "XX", "type": "relates_to", "label": ""},        # id lạ → bỏ
        {"source": "n3", "target": "n1", "type": "kind_la", "label": ""},           # type lạ → relates_to
    ]
    out = s.validate_relations(rels, nodes)
    assert len(out) == 1 and out[0]["type"] == "relates_to"

def test_build_record_shape():
    rec = s.build_record(title="T", sources=["a"], nodes=_nodes(), relations=[],
                         content_hash_value="x" * 64, model="m", elapsed_sec=1.5,
                         degraded_missing=["relations"])
    assert rec["schema_version"] == 2
    assert rec["generator"]["degraded"] is True
    assert rec["generator"]["missing"] == ["relations"]
    assert rec["content_hash"] == "x" * 64
    assert rec["id"] and rec["created_at"].endswith("Z")
```

- [ ] **Step 2: Chạy fail** — `python -m pytest tests/test_mindmap_schema_v2.py -v` → FAIL.

- [ ] **Step 3: Implement `schema.py`**

```python
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
```

- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_mindmap_schema_v2.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add BE/services/mindmap/pipeline BE/tests/test_mindmap_schema_v2.py && git commit -m "feat(mindmap): schema v2 — unified nodes+relations, sanitize, content_hash"`

### Task 3: Stage 0 — Skeleton builder

**Files:**
- Create: `BE/services/mindmap/pipeline/skeleton.py`
- Test: `BE/tests/test_mindmap_skeleton.py`

**Interfaces:**
- Consumes: `schema.sanitize_nodes`.
- Produces: `build_skeleton(mm_input: dict) -> tuple[list[dict], str]` — (nodes v2, method) với method ∈ {"headings","tree_sections","clusters","single"}. `mm_input` shape (Task 4 sản xuất): `{"title": str, "sources": [str], "chunks": [{"key": str, "text": str, "heading_path": str, "chunk_keys": [str]}], "tree_sections": [{"title": str, "chunk_refs": [str]}]}`.

- [ ] **Step 1: Viết test fail**

```python
# BE/tests/test_mindmap_skeleton.py
from services.mindmap.pipeline.skeleton import build_skeleton

def _mk(chunks=None, sections=None, title="Tài liệu X"):
    return {"title": title, "sources": ["x"], "chunks": chunks or [], "tree_sections": sections or []}

def test_headings_build_tree_in_document_order():
    chunks = [
        {"key": "0", "text": "mở đầu", "heading_path": "1. Giới thiệu", "chunk_keys": ["0"]},
        {"key": "1", "text": "chi tiết pp", "heading_path": "2. Phương pháp > 2.1 Thu thập", "chunk_keys": ["1"]},
        {"key": "2", "text": "so khớp", "heading_path": "2. Phương pháp > 2.2 Xử lý", "chunk_keys": ["2"]},
        {"key": "3", "text": "kết quả", "heading_path": "3. Kết quả", "chunk_keys": ["3"]},
    ]
    nodes, method = build_skeleton(_mk(chunks))
    assert method == "headings"
    root = next(n for n in nodes if n["kind"] == "root")
    assert root["title"] == "Tài liệu X"
    secs = [n for n in nodes if n["kind"] == "section" and n["parent"] == root["id"]]
    assert [s["title"] for s in secs] == ["1. Giới thiệu", "2. Phương pháp", "3. Kết quả"]
    pp = next(s for s in secs if s["title"] == "2. Phương pháp")
    kids = [n for n in nodes if n["parent"] == pp["id"]]
    assert [k["title"] for k in kids] == ["2.1 Thu thập", "2.2 Xử lý"]
    thu_thap = kids[0]
    assert thu_thap["chunk_refs"] == ["1"]  # provenance từ chunk_keys

def test_fallback_tree_sections_when_no_headings():
    chunks = [{"key": "0", "text": "abc", "heading_path": "", "chunk_keys": ["0"]}]
    sections = [{"title": "Tổng quan tài liệu", "chunk_refs": ["0"]}]
    nodes, method = build_skeleton(_mk(chunks, sections))
    assert method == "tree_sections"
    assert any(n["title"] == "Tổng quan tài liệu" and n["kind"] == "section" for n in nodes)

def test_fallback_clusters_when_nothing_else(monkeypatch):
    chunks = [{"key": str(i), "text": f"máy học mô hình dữ liệu huấn luyện số {i}", "heading_path": "", "chunk_keys": [str(i)]} for i in range(8)]
    nodes, method = build_skeleton(_mk(chunks))
    assert method in ("clusters", "single")
    assert any(n["kind"] == "root" for n in nodes)
    assert any(n["kind"] == "section" for n in nodes)

def test_empty_input_returns_root_only():
    nodes, method = build_skeleton(_mk())
    assert method == "single"
    assert len(nodes) == 1 and nodes[0]["kind"] == "root"
```

- [ ] **Step 2: Chạy fail** — `python -m pytest tests/test_mindmap_skeleton.py -v` → FAIL.

- [ ] **Step 3: Implement `skeleton.py`**

```python
"""Stage 0 — skeleton deterministic (0 LLM): heading_path → tree_sections → TF-IDF clusters."""
from __future__ import annotations

from services.mindmap.pipeline.schema import sanitize_nodes

_SEP = " > "  # khớp chunking.py::_heading_path


def _root(title: str) -> dict:
    return {"id": "n0", "parent": None, "kind": "root", "title": title or "Mind Map",
            "note": "", "chunk_refs": [], "order": 0}


def _from_headings(title: str, chunks: list[dict]) -> list[dict] | None:
    if not any((c.get("heading_path") or "").strip() for c in chunks):
        return None
    root = _root(title)
    nodes = [root]
    by_path: dict[tuple, dict] = {}
    counter = 0
    for c in chunks:
        hp = (c.get("heading_path") or "").strip()
        parts = tuple(p.strip() for p in hp.split(_SEP) if p.strip()) if hp else ("Nội dung khác",)
        parent_id = root["id"]
        for depth in range(1, len(parts) + 1):
            key = parts[:depth]
            node = by_path.get(key)
            if node is None:
                counter += 1
                node = {"id": f"n{counter}", "parent": parent_id,
                        "kind": "section" if depth == 1 else "idea",
                        "title": parts[depth - 1], "note": "", "chunk_refs": [],
                        "order": len([n for n in nodes if n["parent"] == parent_id])}
                by_path[key] = node
                nodes.append(node)
            parent_id = node["id"]
        # chunk provenance gắn vào node SÂU NHẤT của path
        by_path[parts]["chunk_refs"].extend(c.get("chunk_keys") or [])
    return nodes


def _from_tree_sections(title: str, sections: list[dict]) -> list[dict] | None:
    sections = [s for s in (sections or []) if (s.get("title") or "").strip()]
    if not sections:
        return None
    root = _root(title)
    nodes = [root]
    for i, s in enumerate(sections):
        nodes.append({"id": f"n{i + 1}", "parent": root["id"], "kind": "section",
                      "title": s["title"].strip(), "note": "",
                      "chunk_refs": [str(r) for r in (s.get("chunk_refs") or [])], "order": i})
    return nodes


def _from_clusters(title: str, chunks: list[dict]) -> list[dict] | None:
    texts = [(c.get("text") or "").strip() for c in chunks]
    texts_idx = [i for i, t in enumerate(texts) if t]
    if len(texts_idx) < 4:
        return None
    try:
        from sklearn.cluster import KMeans
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(max_features=2000)
        X = vec.fit_transform([texts[i] for i in texts_idx])
        k = min(6, max(2, len(texts_idx) // 3))
        km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(X)
        terms = vec.get_feature_names_out()
        root = _root(title)
        nodes = [root]
        for ci in range(k):
            top = km.cluster_centers_[ci].argsort()[::-1][:3]
            label = " / ".join(terms[t] for t in top) or f"Chủ đề {ci + 1}"
            refs: list[str] = []
            for j, lab in enumerate(km.labels_):
                if lab == ci:
                    refs.extend(chunks[texts_idx[j]].get("chunk_keys") or [])
            nodes.append({"id": f"n{ci + 1}", "parent": root["id"], "kind": "section",
                          "title": label, "note": "", "chunk_refs": refs, "order": ci})
        return nodes
    except Exception:
        return None


def build_skeleton(mm_input: dict) -> tuple[list[dict], str]:
    title = (mm_input.get("title") or "Mind Map").strip()
    chunks = mm_input.get("chunks") or []
    for fn, method in ((_from_headings, "headings"),
                       (lambda t, _c: _from_tree_sections(t, mm_input.get("tree_sections")), "tree_sections"),
                       (_from_clusters, "clusters")):
        nodes = fn(title, chunks)
        if nodes and len(nodes) > 1:
            return sanitize_nodes(nodes), method
    return sanitize_nodes([_root(title)]), "single"
```

- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_mindmap_skeleton.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(mindmap): stage-0 skeleton builder (headings → tree-sections → tfidf clusters)"` (chỉ add file của task).

### Task 4: Input collector (monolith gom input)

**Files:**
- Create: `BE/app/domains/mindmap/__init__.py` (rỗng), `BE/app/domains/mindmap/input_collector.py`
- Test: `BE/tests/test_mindmap_input_collector.py`

**Interfaces:**
- Consumes: `chunk_text_store.get_text(int)`, `canonical_source_stem`, `app.domains.memory.tree._load_memory_trees()`.
- Produces: `collect_mindmap_input(index_meta_path: Path, source_names: list[str]) -> dict` trả `mm_input` đúng shape Task 3 + key `"sources"` = canonical stems. Sub-chunk merge theo `parent_id` (sort `sub_order`), `chunk_keys` gom mọi id gốc. Logic khớp nguồn MIRROR `worker.collect_chunks_for_sources` (ưu tiên `source_stem`, fallback `video`).

- [ ] **Step 1: Viết test fail**

```python
# BE/tests/test_mindmap_input_collector.py
import json
from app.domains.mindmap import input_collector as ic
from app.domains.vectorstore import chunk_text_store

def _write_meta(tmp_path, meta):
    p = tmp_path / "index.json"
    p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return p

def test_collects_matching_source_with_store_text(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: f"text-{cid}")
    meta = {
        "0": {"source_stem": "bao_cao_docx", "video": "v.mp4", "heading_path": "1. Mở đầu"},
        "1": {"source_stem": "khac_docx", "video": "k.mp4"},
    }
    out = ic.collect_mindmap_input(_write_meta(tmp_path, meta), ["bao_cao_docx"])
    assert out["sources"] == ["bao_cao_docx"]
    assert len(out["chunks"]) == 1
    c = out["chunks"][0]
    assert c["text"] == "text-0" and c["heading_path"] == "1. Mở đầu" and c["chunk_keys"] == ["0"]

def test_merges_subchunks_by_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: f"t{cid}")
    meta = {
        "10": {"source_stem": "a_docx", "heading_path": "H"},
        "11": {"source_stem": "a_docx", "is_subchunk": True, "parent_id": "10", "sub_order": 2},
        "12": {"source_stem": "a_docx", "is_subchunk": True, "parent_id": "10", "sub_order": 1},
    }
    out = ic.collect_mindmap_input(_write_meta(tmp_path, meta), ["a_docx"])
    assert len(out["chunks"]) == 1
    c = out["chunks"][0]
    assert c["text"] == "t10\n\nt12\n\nt11"          # cha + sub theo sub_order
    assert c["chunk_keys"] == ["10", "12", "11"]

def test_tree_sections_included(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: "t")
    monkeypatch.setattr(ic, "_load_tree_sections", lambda stems: [{"title": "Tổng quan", "chunk_refs": ["0"]}])
    meta = {"0": {"source_stem": "a_docx"}}
    out = ic.collect_mindmap_input(_write_meta(tmp_path, meta), ["a_docx"])
    assert out["tree_sections"] == [{"title": "Tổng quan", "chunk_refs": ["0"]}]

def test_title_single_vs_multi(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: "t")
    p = _write_meta(tmp_path, {"0": {"source_stem": "bao_cao_docx"}})
    assert ic.collect_mindmap_input(p, ["bao_cao.docx"])["title"] == "bao_cao"
    out = ic.collect_mindmap_input(p, ["a.docx", "b.docx", "c.docx", "d.docx"])
    assert out["title"].startswith("Tổng hợp:")
```

- [ ] **Step 2: Chạy fail** → FAIL.

- [ ] **Step 3: Implement `input_collector.py`**

```python
"""Gom input mindmap TẠI MONOLITH — worker/service không tự đọc đĩa (spec §4.1)."""
from __future__ import annotations

import json
from pathlib import Path

from shared.source_id import canonical_source_stem


def _load_tree_sections(stems: set[str]) -> list[dict]:
    """Section node từ memory tree của các source (fallback skeleton)."""
    try:
        from app.domains.memory.tree import _load_memory_trees
        out: list[dict] = []
        for tree in _load_memory_trees() or []:
            if canonical_source_stem(tree.get("source_stem") or "") not in stems:
                continue
            for n in tree.get("nodes") or []:
                if n.get("type") == "section" and (n.get("title") or "").strip():
                    out.append({"title": n["title"].strip(),
                                "chunk_refs": [str(r) for r in (n.get("chunk_refs") or [])]})
        return out
    except Exception:
        return []


def _title_for(source_names: list[str]) -> str:
    stems = [Path(s).stem for s in source_names if Path(s).stem]
    if not stems:
        return "Mind Map tổng hợp"
    if len(source_names) == 1:
        return stems[0]
    preview = ", ".join(stems[:3])
    if len(stems) > 3:
        preview += f" + {len(stems) - 3} nguồn"
    return f"Tổng hợp: {preview}"


def collect_mindmap_input(index_meta_path: Path, source_names: list[str]) -> dict:
    from app.domains.vectorstore import chunk_text_store

    wanted = {canonical_source_stem(s) for s in (source_names or []) if (s or "").strip()}
    wanted.discard("")
    with open(index_meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    def _text(key: str, m: dict) -> str:
        try:
            t = (chunk_text_store.get_text(int(key)) or "").strip()
        except Exception:
            t = ""
        return t or (m.get("text") or "").strip()

    parents: dict[str, dict] = {}
    subs: dict[str, list[tuple[int, str, str]]] = {}
    order: list[str] = []
    for key, m in (meta or {}).items():
        if not isinstance(m, dict):
            continue
        stem = canonical_source_stem(m.get("source_stem") or m.get("video") or "")
        if not stem or stem not in wanted:
            continue
        if m.get("is_subchunk") and m.get("parent_id"):
            pk = str(m["parent_id"]).strip()
            subs.setdefault(pk, []).append((int(m.get("sub_order") or 0), str(key), _text(key, m)))
        else:
            parents[str(key)] = {"key": str(key), "text": _text(key, m),
                                 "heading_path": (m.get("heading_path") or "").strip(),
                                 "chunk_keys": [str(key)]}
            order.append(str(key))

    chunks: list[dict] = []
    for key in order:
        c = parents[key]
        for _so, sk, st in sorted(subs.get(key, [])):
            if st:
                c["text"] = (c["text"] + "\n\n" + st).strip()
                c["chunk_keys"].append(sk)
        if c["text"]:
            chunks.append(c)
    # sub-group mồ côi (parent không nằm trong selection) → chunk logic riêng
    for pk, group in subs.items():
        if pk in parents:
            continue
        group = sorted(group)
        text = "\n\n".join(t for _o, _k, t in group if t).strip()
        if text:
            chunks.append({"key": pk, "text": text, "heading_path": "",
                           "chunk_keys": [k for _o, k, _t in group]})

    return {"title": _title_for(source_names), "sources": sorted(wanted),
            "chunks": chunks, "tree_sections": _load_tree_sections(wanted)}
```

- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_mindmap_input_collector.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(mindmap): input collector — monolith gathers chunks/headings/tree-sections"` (add đúng file task).

### Task 5 [CODEX]: sqlite store `mindmaps.sqlite` + migrate json

**Files:**
- Create: `BE/app/domains/mindmap/store.py`
- Test: `BE/tests/test_mindmap_store.py`

**Interfaces:**
- Produces (main.py + graph dùng):
  - `init_db() -> None` — tạo bảng nếu chưa có; DB path = `(env MEMORY_DIR hoặc BE_ROOT/"memory") / "mindmaps.sqlite"`; override được bằng env `MINDMAPS_DB_PATH` (test dùng).
  - `save_record(record: dict) -> None` (INSERT OR REPLACE theo `id`)
  - `get_by_hash(content_hash: str) -> dict | None` (record mới nhất trùng hash)
  - `list_records() -> list[dict]` (mới nhất trước)
  - `delete_record(mindmap_id: str) -> bool`
  - `delete_by_source(stem: str) -> int` — xoá record có `stem` (canonical) trong `sources`; trả số record xoá
  - `migrate_from_json(json_path: Path) -> int` — đọc `mindmaps.json` cũ, mỗi record gắn `schema_version: 1` nếu thiếu, `content_hash: ""`, insert-if-absent theo `id`; idempotent; trả số record thêm mới. KHÔNG xoá file json.

Bảng: `mindmaps(id TEXT PRIMARY KEY, content_hash TEXT, sources_json TEXT, created_at TEXT, record_json TEXT)` + `CREATE INDEX IF NOT EXISTS idx_mm_hash ON mindmaps(content_hash)`. Pattern connection/lock: MIRROR `app/domains/jobs/jobs_store.py` (threading.Lock, WAL, mkdir parent). `sources_json` lưu JSON list stems đã canonical (`canonical_source_stem`); `delete_by_source` so khớp bằng cách load list và so phần tử — KHÔNG dùng `LIKE` trên chuỗi thô.

- [ ] **Step 1: Viết test fail**

```python
# BE/tests/test_mindmap_store.py
import json
from app.domains.mindmap import store

def _rec(i="id1", h="h" * 64, sources=("a_docx",)):
    return {"id": i, "schema_version": 2, "title": "T", "sources": list(sources),
            "content_hash": h, "created_at": "2026-07-03T00:00:00Z",
            "nodes": [{"id": "n0", "parent": None, "kind": "root", "title": "T"}],
            "relations": [], "generator": {"pipeline": "skeleton_v1", "degraded": False, "missing": []}}

def _use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mm.sqlite"))

def test_save_get_by_hash_roundtrip(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    store.save_record(_rec())
    got = store.get_by_hash("h" * 64)
    assert got and got["id"] == "id1" and got["nodes"][0]["kind"] == "root"
    assert store.get_by_hash("x" * 64) is None

def test_list_newest_first_and_delete(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    store.save_record(_rec("a", created := "h1" * 32) | {"created_at": "2026-01-01T00:00:00Z"})
    store.save_record(_rec("b", "h2" * 32) | {"created_at": "2026-02-01T00:00:00Z"})
    ids = [r["id"] for r in store.list_records()]
    assert ids == ["b", "a"]
    assert store.delete_record("a") is True
    assert store.delete_record("a") is False
    assert [r["id"] for r in store.list_records()] == ["b"]

def test_delete_by_source_canonical(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    store.save_record(_rec("a", "h1" * 32, sources=["bao cao_docx"]))
    store.save_record(_rec("b", "h2" * 32, sources=["khac_docx"]))
    n = store.delete_by_source("bao_cao_docx")  # canonical: space → _
    assert n == 1
    assert [r["id"] for r in store.list_records()] == ["b"]

def test_migrate_from_json_idempotent(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    legacy = [{"id": "old1", "title": "L", "nodes": [], "sources": ["s"], "createdAt": "2025-04-29T00:00:00"}]
    p = tmp_path / "mindmaps.json"
    p.write_text(json.dumps(legacy), encoding="utf-8")
    assert store.migrate_from_json(p) == 1
    assert store.migrate_from_json(p) == 0  # idempotent
    rec = store.list_records()[0]
    assert rec["schema_version"] == 1 and rec["id"] == "old1"
```

- [ ] **Step 2: Chạy fail** → FAIL.
- [ ] **Step 3: Implement** theo interface trên (mirror jobs_store pattern; `get_by_hash` bỏ qua hash rỗng; `list_records` ORDER BY created_at DESC; migrate map `createdAt`→`created_at` nếu thiếu).
- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_mindmap_store.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(mindmap): sqlite store + one-time migration from mindmaps.json"`.

### Task 6 [CODEX]: jobs_store — cờ cancel

**Files:**
- Modify: `BE/app/domains/jobs/jobs_store.py`
- Test: `BE/tests/test_jobs_cancel.py`

**Interfaces:**
- Produces: `request_cancel(job_id: str) -> None`, `is_cancel_requested(job_id: str) -> bool`. Column mới `cancel_requested INT DEFAULT 0` thêm qua `_ensure_job_columns` (ALTER TABLE nếu thiếu — mirror `token_buffer`). `get_job` trả thêm key `"cancel_requested": bool`.

- [ ] **Step 1: Viết test fail**

```python
# BE/tests/test_jobs_cancel.py
from app.domains.jobs import jobs_store as js

def test_cancel_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.create_job("j1", job_type="mindmap")
    assert js.is_cancel_requested("j1") is False
    js.request_cancel("j1")
    assert js.is_cancel_requested("j1") is True
    assert js.get_job("j1")["cancel_requested"] is True

def test_cancel_unknown_job_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.request_cancel("khong_ton_tai")          # không ném
    assert js.is_cancel_requested("khong_ton_tai") is False
```

- [ ] **Step 2: Chạy fail** → FAIL.
- [ ] **Step 3: Implement** — thêm cột trong `_ensure_job_columns`; `request_cancel` = UPDATE `cancel_requested=1`; `is_cancel_requested` = SELECT, None → False; `get_job` SELECT thêm cột.
- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_jobs_cancel.py -v` → PASS (và `tests/test_query.py` vẫn xanh — get_job đổi shape additive).
- [ ] **Step 5: Commit** — `git commit -m "feat(jobs): cooperative cancel flag (cancel_requested)"`.

### Task 7: Stage 1 — Enrich (LLM song song theo nhánh)

**Files:**
- Create: `BE/services/mindmap/pipeline/enrich.py`
- Test: `BE/tests/test_mindmap_enrich.py`

**Interfaces:**
- Consumes: `jsonrepair.repair_json_text`, `schema.NodeV2`, `ask_ai` (từ `app.clients.llm_factory`, monkeypatch được ở module enrich).
- Produces: `enrich_branches(mm_input, skeleton_nodes, *, model, timeout_sec=120.0, max_workers=2, progress_cb=None, cancel_cb=None) -> tuple[list[dict], bool]` — trả (nodes hoàn chỉnh, `degraded`). Nhánh = con trực tiếp của root có `kind=="section"`. Mỗi nhánh 1 LLM call; fail/timeout nhánh nào → GIỮ nguyên skeleton nhánh đó và set degraded=True; `cancel_cb()` True → dừng ngay trả (nodes hiện có, degraded hiện có). Env đọc tại call-site: `MINDMAP_MODEL` (default `qwen2.5:14b`), `MINDMAP_LLM_TIMEOUT_SEC` (default 120), `MINDMAP_ENRICH_PARALLEL` (default 2).

- [ ] **Step 1: Viết test fail**

```python
# BE/tests/test_mindmap_enrich.py
import json
import pytest
from services.mindmap.pipeline import enrich as en
from services.mindmap.pipeline.skeleton import build_skeleton

def _input_and_skeleton():
    chunks = [
        {"key": "0", "text": "định nghĩa khái niệm A rất dài", "heading_path": "1. Khái niệm", "chunk_keys": ["0"]},
        {"key": "1", "text": "các bước của phương pháp B", "heading_path": "2. Phương pháp", "chunk_keys": ["1"]},
    ]
    mm = {"title": "Doc", "sources": ["d"], "chunks": chunks, "tree_sections": []}
    nodes, _ = build_skeleton(mm)
    return mm, nodes

def test_enrich_merges_llm_children_with_valid_chunk_refs(monkeypatch):
    def fake_ask(prompt, system_prompt=None, model=None, feature=None, options=None, **kw):
        return json.dumps({"title": "Khái niệm A", "note": "Tóm 1 câu.",
                           "children": [{"title": "Định nghĩa", "note": "n", "chunk_keys": ["0", "BỊA"]}]})
    monkeypatch.setattr(en, "ask_ai", fake_ask)
    mm, skeleton = _input_and_skeleton()
    nodes, degraded = en.enrich_branches(mm, skeleton, model="m", timeout_sec=5)
    assert degraded is False
    enriched_branch = next(n for n in nodes if n["title"] == "Khái niệm A")
    assert enriched_branch["note"] == "Tóm 1 câu."
    kid = next(n for n in nodes if n["title"] == "Định nghĩa")
    assert kid["parent"] == enriched_branch["id"]
    assert kid["chunk_refs"] == ["0"]           # "BỊA" bị lọc — chỉ giữ key thuộc nhánh

def test_enrich_branch_failure_keeps_skeleton_sets_degraded(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("llm chết")
    monkeypatch.setattr(en, "ask_ai", boom)
    mm, skeleton = _input_and_skeleton()
    nodes, degraded = en.enrich_branches(mm, skeleton, model="m", timeout_sec=5)
    assert degraded is True
    assert {n["title"] for n in nodes} >= {"1. Khái niệm", "2. Phương pháp"}  # skeleton còn nguyên

def test_enrich_respects_cancel(monkeypatch):
    calls = {"n": 0}
    def fake_ask(*a, **kw):
        calls["n"] += 1
        return json.dumps({"title": "X", "note": "", "children": []})
    monkeypatch.setattr(en, "ask_ai", fake_ask)
    mm, skeleton = _input_and_skeleton()
    nodes, _ = en.enrich_branches(mm, skeleton, model="m", timeout_sec=5, cancel_cb=lambda: True)
    assert calls["n"] == 0                       # huỷ trước khi gọi
```

- [ ] **Step 2: Chạy fail** → FAIL.

- [ ] **Step 3: Implement `enrich.py`**

```python
"""Stage 1 — enrich từng nhánh top-level bằng LLM (song song, mỗi nhánh 1 call)."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from app.clients.llm_factory import ask_ai
from services.mindmap.jsonrepair import repair_json_text
from services.mindmap.pipeline.schema import sanitize_nodes

_SYSTEM = """Bạn là trợ lý dựng sơ đồ tư duy tiếng Việt.
Cho MỘT nhánh (tiêu đề + nội dung các đoạn), trả về DUY NHẤT JSON:
{"title": "tiêu đề nhánh gọn 2-8 từ", "note": "tóm ý nhánh trong 1-2 câu",
 "children": [{"title": "ý con 2-8 từ", "note": "1 câu", "chunk_keys": ["id đoạn làm bằng chứng"]}]}
Quy tắc: 2-5 children; chunk_keys CHỈ chọn từ danh sách id được cấp; không markdown; không giải thích."""

_MAX_BRANCH_CHARS = 6000


def _branch_context(mm_input: dict, refs: list[str]) -> str:
    parts, total = [], 0
    refset = set(refs)
    for c in mm_input.get("chunks") or []:
        if refset & set(c.get("chunk_keys") or []):
            t = f"[id={','.join(c['chunk_keys'])}] {c['text']}"
            if total + len(t) > _MAX_BRANCH_CHARS:
                t = t[: _MAX_BRANCH_CHARS - total]
            parts.append(t)
            total += len(t)
            if total >= _MAX_BRANCH_CHARS:
                break
    return "\n\n".join(parts)


def _descendant_refs(branch_id: str, nodes: list[dict]) -> list[str]:
    kids = {branch_id}
    changed = True
    while changed:
        changed = False
        for n in nodes:
            if n.get("parent") in kids and n["id"] not in kids:
                kids.add(n["id"])
                changed = True
    refs: list[str] = []
    for n in nodes:
        if n["id"] in kids:
            refs.extend(n.get("chunk_refs") or [])
    return refs


def _enrich_one(mm_input: dict, branch: dict, allowed: list[str], model: str, timeout_sec: float) -> dict:
    ctx = _branch_context(mm_input, allowed)
    user = f"Nhánh: {branch['title']}\nDanh sách id hợp lệ: {', '.join(sorted(set(allowed)))}\n\nNội dung:\n{ctx}"
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(ask_ai, user, system_prompt=_SYSTEM, model=model,
                        feature="mindmap", options={"temperature": 0.15})
        raw = fut.result(timeout=timeout_sec)
    finally:
        ex.shutdown(wait=False)          # timeout phải TRẢ NGAY (bài học warmup)
    data = json.loads(repair_json_text(str(raw)))
    allowed_set = set(allowed)
    children = []
    for i, ch in enumerate((data.get("children") or [])[:5]):
        title = (ch.get("title") or "").strip()
        if not title:
            continue
        children.append({"title": title, "note": (ch.get("note") or "").strip(),
                         "chunk_refs": [k for k in (ch.get("chunk_keys") or []) if str(k) in allowed_set],
                         "order": i})
    return {"title": (data.get("title") or branch["title"]).strip() or branch["title"],
            "note": (data.get("note") or "").strip(), "children": children}


def enrich_branches(mm_input: dict, skeleton_nodes: list[dict], *, model: str,
                    timeout_sec: float = 120.0, max_workers: int = 2,
                    progress_cb: Optional[Callable[[int, str], None]] = None,
                    cancel_cb: Optional[Callable[[], bool]] = None) -> tuple[list[dict], bool]:
    if os.getenv("SKIP_MODEL_LOAD") == "1":
        return skeleton_nodes, False
    nodes = [dict(n) for n in skeleton_nodes]
    root = next((n for n in nodes if n["kind"] == "root"), None)
    if root is None:
        return nodes, True
    branches = [n for n in nodes if n.get("parent") == root["id"] and n["kind"] == "section"]
    degraded = False
    next_id = max((int(n["id"][1:]) for n in nodes if n["id"][1:].isdigit()), default=0) + 1

    def _run(branch: dict):
        allowed = _descendant_refs(branch["id"], nodes)
        return _enrich_one(mm_input, branch, allowed, model, timeout_sec)

    done = 0
    for i in range(0, len(branches), max_workers):
        if cancel_cb and cancel_cb():
            return nodes, degraded
        batch = branches[i:i + max_workers]
        ex = ThreadPoolExecutor(max_workers=max_workers)
        futs = {ex.submit(_run, b): b for b in batch}
        try:
            for fut, b in futs.items():
                try:
                    r = fut.result(timeout=timeout_sec + 10)
                    b["title"], b["note"] = r["title"], r["note"]
                    for ch in r["children"]:
                        nodes.append({"id": f"n{next_id}", "parent": b["id"], "kind": "idea", **ch})
                        next_id += 1
                except Exception:
                    degraded = True     # giữ skeleton nhánh này
                done += 1
                if progress_cb:
                    progress_cb(int(30 + 40 * done / max(1, len(branches))),
                                f"Đang làm giàu nhánh {done}/{len(branches)}...")
        finally:
            ex.shutdown(wait=False)
    return sanitize_nodes(nodes), degraded
```

- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_mindmap_enrich.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(mindmap): stage-1 branch enrichment (parallel, degrade-not-fail, cancel-aware)"`.

### Task 8: Stage 2 — Relations (1 LLM call)

**Files:**
- Create: `BE/services/mindmap/pipeline/relations.py`
- Test: `BE/tests/test_mindmap_relations.py`

**Interfaces:**
- Consumes: `jsonrepair`, `schema.validate_relations`, `ask_ai`.
- Produces: `extract_relations(nodes: list[dict], *, model: str, timeout_sec: float = 120.0, cancel_cb=None) -> tuple[list[dict], bool]` — (relations đã validate, degraded). `SKIP_MODEL_LOAD=1` hoặc <2 section → `([], False)`.

- [ ] **Step 1: Viết test fail**

```python
# BE/tests/test_mindmap_relations.py
import json
from services.mindmap.pipeline import relations as rel

NODES = [
    {"id": "n0", "parent": None, "kind": "root", "title": "R"},
    {"id": "n1", "parent": "n0", "kind": "section", "title": "Phương pháp", "note": "..."},
    {"id": "n2", "parent": "n0", "kind": "section", "title": "Kết quả", "note": "..."},
]

def test_relations_parsed_and_validated(monkeypatch):
    def fake_ask(prompt, system_prompt=None, model=None, feature=None, options=None, **kw):
        return json.dumps({"relations": [
            {"source": "n1", "target": "n2", "type": "leads_to", "label": "dẫn tới"},
            {"source": "n1", "target": "n1", "type": "causes", "label": "loop"},
        ]})
    monkeypatch.setattr(rel, "ask_ai", fake_ask)
    out, degraded = rel.extract_relations(NODES, model="m", timeout_sec=5)
    assert degraded is False
    assert out == [{"source": "n1", "target": "n2", "type": "leads_to", "label": "dẫn tới"}]

def test_relations_llm_failure_degrades(monkeypatch):
    monkeypatch.setattr(rel, "ask_ai", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out, degraded = rel.extract_relations(NODES, model="m", timeout_sec=5)
    assert out == [] and degraded is True

def test_relations_skipped_when_too_few_sections():
    out, degraded = rel.extract_relations(NODES[:2], model="m")
    assert out == [] and degraded is False
```

- [ ] **Step 2: Chạy fail** → FAIL.

- [ ] **Step 3: Implement `relations.py`**

```python
"""Stage 2 — trích quan hệ chéo giữa các nhánh (1 LLM call, degrade-not-fail)."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from app.clients.llm_factory import ask_ai
from services.mindmap.jsonrepair import repair_json_text
from services.mindmap.pipeline.schema import REL_TYPES, validate_relations

_SYSTEM = f"""Bạn là trợ lý phân tích quan hệ giữa các phần của tài liệu tiếng Việt.
Cho danh sách nhánh (id, tiêu đề, tóm ý), tìm các quan hệ NGỮ NGHĨA giữa các nhánh KHÁC nhau.
Trả về DUY NHẤT JSON: {{"relations": [{{"source": "id", "target": "id",
 "type": "{'|'.join(REL_TYPES)}", "label": "nhãn tiếng Việt 1-3 từ"}}]}}
Quy tắc: 0-10 quan hệ; CHỈ dùng id được cấp; không lặp cặp; không quan hệ cha-con hiển nhiên."""


def extract_relations(nodes: list[dict], *, model: str, timeout_sec: float = 120.0,
                      cancel_cb: Optional[Callable[[], bool]] = None) -> tuple[list[dict], bool]:
    sections = [n for n in nodes if n.get("kind") in ("section", "idea") and n.get("note")]
    top = [n for n in nodes if n.get("kind") == "section"]
    if os.getenv("SKIP_MODEL_LOAD") == "1" or len(top) < 2 or (cancel_cb and cancel_cb()):
        return [], False
    lines = [f"- id={n['id']} | {n['title']} | {n.get('note', '')}" for n in (sections or top)[:30]]
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(ask_ai, "Các nhánh:\n" + "\n".join(lines), system_prompt=_SYSTEM,
                        model=model, feature="mindmap", options={"temperature": 0.15})
        raw = fut.result(timeout=timeout_sec)
        data = json.loads(repair_json_text(str(raw)))
        return validate_relations(data.get("relations") or [], nodes), False
    except Exception:
        return [], True
    finally:
        ex.shutdown(wait=False)
```

- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_mindmap_relations.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(mindmap): stage-2 cross-branch relations extraction"`.

### Task 9: MindmapState v2 + graph 5 node + wiring + factory

**Files:**
- Modify: `BE/app/graphs/state.py` (MindmapState), `BE/app/graphs/mindmap_graph.py` (viết lại), `BE/app/wiring.py`, `BE/app/clients/mindmap_factory.py`
- Test: `BE/tests/test_mindmap_graph.py` (VIẾT LẠI — file cũ test mode/strategy sẽ xoá nội dung)

**Interfaces:**
- Consumes: Task 2-8 (schema/skeleton/enrich/relations/store/cancel), `collect_mindmap_input`.
- Produces:
  - `MindmapState` mới:
    ```python
    class MindmapState(TypedDict):
        job_id: str
        source_names: list
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
    ```
  - `build_mindmap_graph(*, data_dir, index_meta_path, jobs_update, collect_input, pipeline, persist_record) -> compiled` — node: `CollectInput → Skeleton → Enrich → Relations → AssemblePersist`, mỗi node check `jobs_store.is_cancel_requested(job_id)` trước khi chạy (True → set `cancelled`, nhảy `Cancelled` node: job status="cancelled", KHÔNG persist); lỗi hệ thống → `ErrorHandler`; Enrich/Relations degraded KHÔNG phải error.
  - `pipeline` object: `class LocalMindmapPipeline` (đặt trong `mindmap_factory.py`) với method `skeleton(mm_input)`, `enrich(mm_input, skeleton_nodes, progress_cb, cancel_cb)`, `relations(nodes, cancel_cb)` — đọc env `MINDMAP_MODEL`/`MINDMAP_LLM_TIMEOUT_SEC`/`MINDMAP_ENRICH_PARALLEL` bên trong; `get_mindmap_pipeline()` trả Local (gRPC thay sau ở Task 15).
  - `persist_record(record) -> None` = `store.save_record` (inject từ main).
  - Skeleton node ghi preview: `jobs_update(job_id, result={"partial": {"nodes": skeleton, "title": mm_input["title"]}})`.
  - `wiring.build_graphs`: THAY 2 tham số `run_mindmap_generation`, `append_mindmap` bằng `collect_mindmap_input`, `mindmap_pipeline`, `persist_mindmap`.

- [ ] **Step 1: Viết lại `tests/test_mindmap_graph.py` (test dựng graph THẬT + chạy với pipeline stub)**

```python
# BE/tests/test_mindmap_graph.py — graph THẬT, pipeline stub (bài học conftest-mock)
import json
from pathlib import Path
import pytest

def _build(tmp_path, pipeline=None, persist=None, jobs_updates=None):
    from app.graphs.mindmap_graph import build_mindmap_graph
    meta_path = tmp_path / "index.json"
    meta_path.write_text(json.dumps({"0": {"source_stem": "a_docx", "heading_path": "1. Mở đầu"}}), encoding="utf-8")

    def collect_input(index_meta_path, source_names):
        return {"title": "Doc", "sources": ["a_docx"],
                "chunks": [{"key": "0", "text": "t", "heading_path": "1. Mở đầu", "chunk_keys": ["0"]}],
                "tree_sections": []}

    class StubPipeline:
        def skeleton(self, mm):
            return ([{"id": "n0", "parent": None, "kind": "root", "title": "Doc"},
                     {"id": "n1", "parent": "n0", "kind": "section", "title": "1. Mở đầu", "chunk_refs": ["0"]}],
                    "headings")
        def enrich(self, mm, skeleton, progress_cb=None, cancel_cb=None):
            return skeleton, False
        def relations(self, nodes, cancel_cb=None):
            return [], False

    def _jobs_update(job_id, **kw):
        (jobs_updates if jobs_updates is not None else []).append(kw)

    return build_mindmap_graph(
        data_dir=tmp_path, index_meta_path=meta_path,
        jobs_update=_jobs_update, collect_input=collect_input,
        pipeline=pipeline or StubPipeline(), persist_record=persist or (lambda r: None),
    )

def test_real_graph_compiles_and_produces_v2_record(tmp_path):
    saved = []
    g = _build(tmp_path, persist=saved.append)
    out = g.invoke({"job_id": "j1", "source_names": ["a_docx"], "progress": 0,
                    "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "j1"}})
    assert out.get("error") is None
    rec = out["result"]
    assert rec["schema_version"] == 2 and rec["nodes"] and "relations" in rec
    assert saved and saved[0]["id"] == rec["id"]

def test_cancel_before_enrich_stops_without_persist(tmp_path, monkeypatch):
    from app.domains.jobs import jobs_store as js
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    js.create_job("j2", job_type="mindmap")
    js.request_cancel("j2")
    saved = []
    g = _build(tmp_path, persist=saved.append)
    out = g.invoke({"job_id": "j2", "source_names": ["a_docx"], "progress": 0,
                    "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "j2"}})
    assert out.get("cancelled") is True
    assert saved == []

def test_degraded_stage_flows_to_result(tmp_path):
    class DegradedPipeline:
        def skeleton(self, mm):
            return ([{"id": "n0", "parent": None, "kind": "root", "title": "Doc"},
                     {"id": "n1", "parent": "n0", "kind": "section", "title": "S"}], "headings")
        def enrich(self, mm, sk, progress_cb=None, cancel_cb=None):
            return sk, True
        def relations(self, nodes, cancel_cb=None):
            return [], True
    g = _build(tmp_path, pipeline=DegradedPipeline())
    out = g.invoke({"job_id": "j3", "source_names": ["a_docx"], "progress": 0,
                    "current_node": "", "error": None},
                   config={"configurable": {"thread_id": "j3"}})
    assert out["result"]["generator"]["degraded"] is True
    assert set(out["result"]["generator"]["missing"]) == {"enrich", "relations"}
```

- [ ] **Step 2: Chạy fail** → FAIL (build_mindmap_graph signature cũ).

- [ ] **Step 3: Implement** — sửa `state.py` (MindmapState như Interfaces; XOÁ `strategy`, `generation_mode`, `strategy_requested`); viết lại `mindmap_graph.py`:

```python
# BE/app/graphs/mindmap_graph.py — 5 node skeleton-first (spec §4)
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from app.graphs.logger import _Timer, log_node_event
from app.graphs.sqlite_checkpointer import sqlite_saver_from_path
from app.graphs.state import MindmapState
from services.mindmap.pipeline import schema as mm_schema


def build_mindmap_graph(*, data_dir: Path, index_meta_path: Path,
                        jobs_update: Callable[..., None] | None,
                        collect_input: Callable[..., dict],
                        pipeline: Any,
                        persist_record: Callable[[dict], None]) -> Any:
    def _set_job(job_id: str, **kw: Any) -> None:
        if jobs_update is None:
            return
        try:
            jobs_update(job_id, **kw)
        except Exception:
            pass

    def _cancelled(job_id: str) -> bool:
        try:
            from app.domains.jobs.jobs_store import is_cancel_requested
            return is_cancel_requested(job_id)
        except Exception:
            return False

    def _guard(node_name: str):
        """Decorator-ish: check cancel trước node; lỗi hệ thống → error state."""
        def wrap(fn):
            def inner(state: dict) -> dict:
                if _cancelled(state["job_id"]):
                    return {**state, "cancelled": True, "current_node": node_name}
                t = _Timer()
                try:
                    out = fn(state)
                    log_node_event(state["job_id"], node_name, "ok", t.ms())
                    return out
                except Exception as e:
                    log_node_event(state["job_id"], node_name, "error", t.ms(), {"error": str(e)})
                    return {**state, "error": str(e), "current_node": node_name}
            return inner
        return wrap

    @_guard("CollectInput")
    def collect_node(state: dict) -> dict:
        _set_job(state["job_id"], status="running", progress=5, current_node="CollectInput")
        mm = state.get("mm_input") or collect_input(index_meta_path, state.get("source_names") or [])
        ch = state.get("content_hash") or mm_schema.content_hash(
            mm.get("sources") or [], [c["text"] for c in mm.get("chunks") or []])
        if not mm.get("chunks"):
            raise ValueError("Không có chunk nào cho các nguồn đã chọn.")
        return {**state, "mm_input": mm, "content_hash": ch, "progress": 10,
                "current_node": "CollectInput", "_t0": time.time(), "error": None}

    @_guard("Skeleton")
    def skeleton_node(state: dict) -> dict:
        _set_job(state["job_id"], progress=15, current_node="Skeleton")
        nodes, method = pipeline.skeleton(state["mm_input"])
        # preview cho FE render ngay (spec §4.2.2)
        _set_job(state["job_id"], progress=20,
                 result={"partial": {"title": state["mm_input"]["title"], "nodes": nodes}})
        return {**state, "skeleton": nodes, "skeleton_method": method,
                "progress": 20, "current_node": "Skeleton"}

    @_guard("Enrich")
    def enrich_node(state: dict) -> dict:
        _set_job(state["job_id"], progress=30, current_node="Enrich")
        def _prog(p: int, msg: str) -> None:
            _set_job(state["job_id"], progress=p, current_node=msg)
        nodes, degraded = pipeline.enrich(state["mm_input"], state["skeleton"],
                                          progress_cb=_prog,
                                          cancel_cb=lambda: _cancelled(state["job_id"]))
        missing = list(state.get("degraded_missing") or [])
        if degraded:
            missing.append("enrich")
        return {**state, "nodes": nodes, "degraded_missing": missing,
                "progress": 70, "current_node": "Enrich"}

    @_guard("Relations")
    def relations_node(state: dict) -> dict:
        _set_job(state["job_id"], progress=75, current_node="Relations")
        rels, degraded = pipeline.relations(state["nodes"],
                                            cancel_cb=lambda: _cancelled(state["job_id"]))
        missing = list(state.get("degraded_missing") or [])
        if degraded:
            missing.append("relations")
        return {**state, "relations": rels, "degraded_missing": missing,
                "progress": 85, "current_node": "Relations"}

    @_guard("AssemblePersist")
    def assemble_node(state: dict) -> dict:
        import os
        elapsed = time.time() - (state.get("_t0") or time.time())
        record = mm_schema.build_record(
            title=state["mm_input"]["title"], sources=state["mm_input"]["sources"],
            nodes=mm_schema.sanitize_nodes(state["nodes"]),
            relations=mm_schema.validate_relations(state.get("relations") or [], state["nodes"]),
            content_hash_value=state["content_hash"],
            model=os.getenv("MINDMAP_MODEL", "qwen2.5:14b"),
            elapsed_sec=elapsed, degraded_missing=state.get("degraded_missing") or [])
        persist_record(record)
        _set_job(state["job_id"], status="done", progress=100,
                 current_node="AssemblePersist", result=record)
        return {**state, "result": record, "progress": 100, "current_node": "AssemblePersist"}

    def cancelled_node(state: dict) -> dict:
        _set_job(state["job_id"], status="cancelled", progress=0, current_node="Cancelled")
        return {**state, "cancelled": True, "current_node": "Cancelled"}

    def error_node(state: dict) -> dict:
        err = (str(state.get("error") or "").strip()) or "unknown error"
        _set_job(state["job_id"], status="error", progress=0,
                 current_node="ErrorHandler", error_text=err)
        return {**state, "current_node": "ErrorHandler"}

    def _route(s: dict) -> str:
        if s.get("cancelled"):
            return "Cancelled"
        if s.get("error"):
            return "ErrorHandler"
        return "Continue"

    g = StateGraph(MindmapState)
    g.add_node("CollectInput", collect_node)
    g.add_node("Skeleton", skeleton_node)
    g.add_node("Enrich", enrich_node)
    g.add_node("Relations", relations_node)
    g.add_node("AssemblePersist", assemble_node)
    g.add_node("Cancelled", cancelled_node)
    g.add_node("ErrorHandler", error_node)
    g.set_entry_point("CollectInput")
    routes = {"Cancelled": "Cancelled", "ErrorHandler": "ErrorHandler"}
    g.add_conditional_edges("CollectInput", _route, {**routes, "Continue": "Skeleton"})
    g.add_conditional_edges("Skeleton", _route, {**routes, "Continue": "Enrich"})
    g.add_conditional_edges("Enrich", _route, {**routes, "Continue": "Relations"})
    g.add_conditional_edges("Relations", _route, {**routes, "Continue": "AssemblePersist"})
    g.add_conditional_edges("AssemblePersist", _route, {**routes, "Continue": END})
    g.add_edge("Cancelled", END)
    g.add_edge("ErrorHandler", END)
    return g.compile(checkpointer=sqlite_saver_from_path(data_dir / "checkpoints.sqlite"))
```

LƯU Ý: `_t0` không nằm trong TypedDict → langgraph 0.2.x sẽ DROP nó giữa các node. Thêm `_t0: NotRequired[float]` vào `MindmapState` (đúng bài học "LangGraph chỉ giữ field có trong schema").

`mindmap_factory.py` mới:

```python
from __future__ import annotations

import os

from shared.config import get_settings


class LocalMindmapPipeline:
    def _model(self) -> str:
        return os.getenv("MINDMAP_MODEL", "qwen2.5:14b").strip() or "qwen2.5:14b"

    def _timeout(self) -> float:
        return float(os.getenv("MINDMAP_LLM_TIMEOUT_SEC", "120"))

    def skeleton(self, mm_input):
        from services.mindmap.pipeline.skeleton import build_skeleton
        return build_skeleton(mm_input)

    def enrich(self, mm_input, skeleton_nodes, progress_cb=None, cancel_cb=None):
        from services.mindmap.pipeline.enrich import enrich_branches
        return enrich_branches(mm_input, skeleton_nodes, model=self._model(),
                               timeout_sec=self._timeout(),
                               max_workers=int(os.getenv("MINDMAP_ENRICH_PARALLEL", "2")),
                               progress_cb=progress_cb, cancel_cb=cancel_cb)

    def relations(self, nodes, cancel_cb=None):
        from services.mindmap.pipeline.relations import extract_relations
        return extract_relations(nodes, model=self._model(),
                                 timeout_sec=self._timeout(), cancel_cb=cancel_cb)


def get_mindmap_pipeline():
    settings = get_settings()
    if settings.mindmap_service_addr:
        try:
            from app.clients.mindmap_client import GrpcMindmapPipeline  # Task 15
            return GrpcMindmapPipeline(settings.mindmap_service_addr)
        except Exception:
            pass
    return LocalMindmapPipeline()
```

`wiring.py`: đổi tham số mindmap trong `build_graphs(...)` thành `collect_mindmap_input`, `mindmap_pipeline`, `persist_mindmap` và gọi `build_mindmap_graph(data_dir=..., index_meta_path=..., jobs_update=..., collect_input=collect_mindmap_input, pipeline=mindmap_pipeline, persist_record=persist_mindmap)`. (main.py cập nhật ở Task 10 — wiring đổi trước sẽ làm main.py đỏ; nên Task 9 và 10 commit CÙNG NHAU nếu suite yêu cầu, hoặc giữ tham số cũ optional cho tới Task 10. Chọn: đổi dứt điểm, chạy suite ở Task 10.)

- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_mindmap_graph.py -v` → PASS. (`tests/test_mindmap.py` cũ CHƯA đụng — worker còn nguyên.)
- [ ] **Step 5: Commit** — `git commit -m "feat(mindmap): 5-node skeleton-first LangGraph + MindmapState v2 + pipeline factory"`.

### Task 10: main.py — endpoints, job runner, bỏ dict in-memory

**Files:**
- Modify: `BE/app/main.py` (khối mindmap: dòng ~222-245 dict jobs, ~630-680 helpers, ~700-712 build_graphs, ~1767-1810 run_mindmap_job, ~1812-1970 endpoints), `BE/tests/conftest.py` (MockMindmapGraph giữ nguyên interface)
- Test: `BE/tests/test_mindmap_routes.py` (MỚI)

**Interfaces:**
- Consumes: `store` (Task 5), `jobs_store.request_cancel` (Task 6), graph mới (Task 9), `collect_mindmap_input`, `schema.content_hash`.
- Produces (FE dùng):
  - `POST /generate-mindmap` body `{sources: [...], force?: bool}` → cache hit + !force: `200 {"status":"done","result":{...record v2...}}`; ngược lại `202 {"job_id","status":"started"}`. `mode`/`strategy`/`q` bị bỏ qua.
  - `GET /mindmap-status/<job_id>` → thêm `partial` khi running (từ job result), record done là v2.
  - `POST /mindmap-cancel/<job_id>` → `{"ok": true}`.
  - `GET /mindmaps` → `{"mindmaps": [record...]}` từ sqlite (v1+v2 lẫn lộn OK).
  - `DELETE /mindmaps/<id>` → sqlite.
  - `GET /chunk-text/<int:chunk_id>` → `{"chunk_id", "text"}`, 404 nếu không có (evidence drawer dùng).
  - `/delete-source` + `DELETE /sources/<id>`: gọi thêm `store.delete_by_source(stem)`.
  - Startup: `store.migrate_from_json(MINDMAPS_PATH)` một lần (best-effort try/except).
  - XOÁ: `mindmap_jobs` dict + lock + `_cleanup_old_mindmap_jobs` + `_load_mindmaps/_save_mindmaps/_append_mindmap` (thay bằng store) + `_mindmap_response` (trả record nguyên trạng — FE normalize).
  - `run_mindmap_job(job_id, source_names, force)` mới: chỉ jobs.sqlite, invoke graph với `{"job_id", "source_names", "mm_input", "content_hash", "progress": 0, "current_node": "", "error": None}`.

- [ ] **Step 1: Viết test fail**

```python
# BE/tests/test_mindmap_routes.py
import json
import pytest

def test_generate_cache_hit_returns_done(client, monkeypatch):
    import app.main as be_main
    from app.domains.mindmap import store
    rec = {"id": "r1", "schema_version": 2, "title": "T", "sources": ["a_docx"],
           "content_hash": "h" * 64, "created_at": "2026-07-03T00:00:00Z",
           "nodes": [], "relations": [], "generator": {"degraded": False, "missing": []}}
    monkeypatch.setattr(be_main, "_mindmap_input_and_hash", lambda sources: ({"chunks": [1]}, "h" * 64))
    monkeypatch.setattr(store, "get_by_hash", lambda h: rec if h == "h" * 64 else None)
    r = client.post("/generate-mindmap", json={"sources": ["a_docx"]})
    assert r.status_code == 200
    assert r.get_json()["status"] == "done"
    assert r.get_json()["result"]["id"] == "r1"

def test_generate_force_bypasses_cache(client, monkeypatch):
    import app.main as be_main
    from app.domains.mindmap import store
    monkeypatch.setattr(be_main, "_mindmap_input_and_hash", lambda sources: ({"chunks": [1]}, "h" * 64))
    monkeypatch.setattr(store, "get_by_hash", lambda h: {"id": "r1"})
    started = {}
    monkeypatch.setattr(be_main, "_start_mindmap_job", lambda *a, **k: started.setdefault("yes", True) or "jid")
    r = client.post("/generate-mindmap", json={"sources": ["a_docx"], "force": True})
    assert r.status_code == 202 and started.get("yes")

def test_cancel_endpoint_sets_flag(client, monkeypatch):
    from app.domains.jobs import jobs_store as js
    called = {}
    monkeypatch.setattr(js, "request_cancel", lambda jid: called.setdefault("jid", jid))
    r = client.post("/mindmap-cancel/abc")
    assert r.status_code == 200 and called["jid"] == "abc"

def test_chunk_text_endpoint(client, monkeypatch):
    from app.domains.vectorstore import chunk_text_store
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: "nội dung" if cid == 7 else None)
    assert client.get("/chunk-text/7").get_json()["text"] == "nội dung"
    assert client.get("/chunk-text/999").status_code == 404

def test_list_and_delete_use_store(client, monkeypatch):
    from app.domains.mindmap import store
    monkeypatch.setattr(store, "list_records", lambda: [{"id": "x"}])
    monkeypatch.setattr(store, "delete_record", lambda mid: mid == "x")
    assert client.get("/mindmaps").get_json()["mindmaps"] == [{"id": "x"}]
    assert client.delete("/mindmaps/x").status_code == 200
    assert client.delete("/mindmaps/nope").status_code == 404
```

- [ ] **Step 2: Chạy fail** → FAIL.

- [ ] **Step 3: Implement trong `main.py`**

Helpers mới (đặt cạnh khối mindmap cũ):

```python
from app.domains.mindmap import store as mindmap_store
from app.domains.mindmap.input_collector import collect_mindmap_input
from services.mindmap.pipeline import schema as mindmap_schema

def _mindmap_input_and_hash(source_names: list[str]) -> tuple[dict, str]:
    mm = collect_mindmap_input(INDEX_META_JSON_PATH, source_names)
    h = mindmap_schema.content_hash(mm.get("sources") or [],
                                    [c["text"] for c in mm.get("chunks") or []])
    return mm, h

def _start_mindmap_job(source_names: list[str], mm_input: dict, content_hash: str) -> str:
    job_id = str(uuid.uuid4())
    from app.domains.jobs.jobs_store import create_job
    create_job(job_id, job_type="mindmap", status="pending", progress=0, current_node="Queued")
    threading.Thread(target=run_mindmap_job,
                     args=(job_id, source_names, mm_input, content_hash), daemon=True).start()
    return job_id

def run_mindmap_job(job_id: str, source_names: list[str], mm_input: dict, content_hash: str) -> None:
    try:
        if MINDMAP_GRAPH is None:
            raise RuntimeError("MINDMAP_GRAPH chưa khởi tạo — kiểm tra logs khởi động.")
        _langgraph_invoke(MINDMAP_GRAPH, {
            "job_id": job_id, "source_names": source_names, "mm_input": mm_input,
            "content_hash": content_hash, "progress": 0, "current_node": "", "error": None,
        }, thread_id=job_id)
    except Exception as e:
        from app.domains.jobs.jobs_store import update_job
        update_job(job_id, status="error", error_text=str(e))
```

Route `generate_mindmap` mới (GIỮ khối parse `raw_sources` hiện tại nguyên văn):

```python
@app.post("/generate-mindmap")
def generate_mindmap():
    data = request.json or {}
    # ... khối parse source_names giữ nguyên như hiện tại ...
    if not source_names:
        return jsonify({"error": "No sources selected"}), 400
    force = bool(data.get("force"))
    try:
        mm_input, content_hash = _mindmap_input_and_hash(source_names)
    except Exception as e:
        return jsonify({"error": f"Không đọc được dữ liệu nguồn: {e}"}), 500
    if not mm_input.get("chunks"):
        return jsonify({"error": "Nguồn chưa có dữ liệu đã index"}), 400
    if not force:
        cached = mindmap_store.get_by_hash(content_hash)
        if cached:
            return jsonify({"status": "done", "result": cached, "cached": True}), 200
    job_id = _start_mindmap_job(source_names, mm_input, content_hash)
    return jsonify({"job_id": job_id, "status": "started"}), 202
```

Các endpoint còn lại theo Interfaces (cancel/chunk-text/list/delete/status-partial). `mindmap-status`: bỏ nhánh dict; đọc jobs_store; nếu `status=="running"` và `result` có key `"partial"` → passthrough. Wiring gọi:

```python
_graphs = _build_graphs(
    ...,
    collect_mindmap_input=collect_mindmap_input,
    mindmap_pipeline=_get_mindmap_pipeline(),
    persist_mindmap=mindmap_store.save_record,
)
```

với `from app.clients.mindmap_factory import get_mindmap_pipeline as _get_mindmap_pipeline`. Startup: `try: mindmap_store.migrate_from_json(MINDMAPS_PATH)\nexcept Exception: pass`. Xoá code chết theo Interfaces (dict, cleanup, helpers json).

- [ ] **Step 4: Chạy TOÀN suite** — `python -m pytest tests/ -v` → các test mindmap cũ đụng route/graph cũ sẽ đỏ: sửa/xoá NGAY trong task này: `test_mindmap_timeout.py` (test job-timeout logic đã xoá → XOÁ file), phần route trong `test_mindmap.py` nếu tham chiếu `_append_mindmap`/`mindmap_jobs` (cập nhật sang store). `test_mindmap.py::collect_chunks_for_sources`-tests và `test_mindmap_source_match.py` vẫn xanh (worker chưa đụng). Suite phải XANH trước khi commit.
- [ ] **Step 5: Commit** — `git commit -m "feat(mindmap): cache-first generate, real cancel, sqlite-backed routes, single job store"`.

### Task 11: Smoke thủ công LLM thật (gate trước khi xoá code cũ)

**Files:**
- Create: `C:\Users\VUANH~1\...\scratchpad\smoke_mindmap_v2.py` (NGOÀI repo)

- [ ] **Step 1:** Viết script: gọi `collect_mindmap_input` trên index thật + `LocalMindmapPipeline` đủ 3 stage với Ollama thật (`MINDMAP_MODEL` đang cài), in record: số node, số relation, degraded, elapsed.
- [ ] **Step 2:** Chạy với 1 doc CÓ heading và 1 doc KHÔNG heading (PDF cũ). Kiểm bằng mắt: skeleton đúng thứ tự mục; enrich không bịa chunk_refs; relations hợp lý. Nếu Ollama không chạy → ghi nhận degraded hoạt động đúng (record vẫn ra).
- [ ] **Step 3:** Ghi kết quả đo (thời gian/nhánh, model) vào `.playbook/lessons-learned.md` phần mới (Task 18 sẽ hoàn thiện).

### Task 12 [CODEX]: Xoá máy móc cũ trong worker + tests mồ côi

**ĐIỀU KIỆN:** Task 10 xong, suite xanh, Task 11 smoke OK.

**Files:**
- Modify: `BE/services/mindmap/worker.py` — XOÁ: mode constants + `get_*_for_mode` + `LlmCallBudget` + `TimeoutTracker` + `TimingLogger` + 7 hàm `_build_mindmap_*`/`deterministic_*` + `select_mindmap_strategy` + visual diagram (`VisualDiagram*`, `_build_visual_diagram_llm`, `build_visual_diagram_by_mode`, `_flat_nodes_to_visual_diagram`, `_diagram_quality_low`) + `run_mindmap_generation` + `_content_hash` + `sanitize_mindmap_nodes`/`cap_mindmap_nodes` + pydantic models cũ. GIỮ: `collect_chunks_for_sources` (main.py `/summarize-documents` còn dùng qua import? — KIỂM TRA `grep -rn collect_chunks_for_sources BE/app BE/services` trước; nếu chỉ mindmap cũ dùng thì XOÁ luôn và sửa import), `attach_mindmap_job_context`/`_notify_progress` nếu còn ai import (grep trước khi xoá).
- Modify: `BE/app/main.py` — dọn import từ worker không còn tồn tại (`run_mindmap_generation`, `get_mindmap_model_for_mode`, `VALID_STRATEGIES`...).
- Delete tests: `BE/tests/test_mindmap_timeout.py` (nếu còn), phần test strategy/mode trong `BE/tests/test_mindmap.py`, `BE/tests/test_mindmap_source_match.py` NẾU hàm nó test đã xoá (nếu `collect_chunks_for_sources` giữ thì GIỮ test).
- Modify: `BE/app/clients/mindmap_client.py` — xoá `run_mindmap_generation_via_grpc` cũ (Task 15 thay bằng GrpcMindmapPipeline; tạm để file trống comment "sẽ thay ở per-stage RPC").

- [ ] **Step 1:** `grep -rn "from services.mindmap.worker import\|worker\." BE/app BE/services BE/tests` — liệt kê call-site trước khi xoá.
- [ ] **Step 2:** Xoá theo danh sách trên; mỗi lần xoá 1 cụm chạy `python -c "import app.main"`.
- [ ] **Step 3:** `python -m pytest tests/ -v` → XANH toàn suite; `python -c "import app.graphs.query_graph"` OK.
- [ ] **Step 4:** Commit — `git commit -m "refactor(mindmap): remove mode/strategy/budget/visual-LLM machinery (superseded by skeleton-first)"`.

---

## Phase B — FE "Bản đồ tri thức"

> FE chưa có test runner. Task 13 thêm vitest tối thiểu (devDep `vitest`, script `"test": "vitest run"`). Mọi task FE kết bằng `npm run build` (bắt lỗi import/JSX) + vitest.

### Task 13: `mindmapNormalize.js` (pure) + vitest

**Files:**
- Create: `FE/src/utils/mindmapNormalize.js`, `FE/src/utils/mindmapNormalize.test.js`
- Modify: `FE/package.json` (devDep vitest + script test)

**Interfaces:**
- Produces: `normalizeMindmapRecord(record) -> {title, nodes: [{id, parent, title, note, kind, chunkRefs, order}], relations: [{source, target, type, label}], degraded: bool, missing: string[]}`.
  - v2 (`schema_version === 2`): map field trực tiếp (`chunk_refs`→`chunkRefs`).
  - v1/legacy: dùng logic gộp `nodes`+`diagram` HIỆN CÓ — PORT từ `MindMapModal.jsx::normalizeHierarchyFromData` (dòng ~194-270): unify id, parent map, root detect; `relations` = diagram semantic edges (nếu có); note/chunkRefs rỗng.
  - record rác/null → `{title:"", nodes:[], relations:[], degraded:false, missing:[]}`.

- [ ] **Step 1:** `cd FE && npm i -D vitest` + thêm `"test": "vitest run"` vào scripts.
- [ ] **Step 2: Viết test fail**

```js
// FE/src/utils/mindmapNormalize.test.js
import { describe, it, expect } from "vitest";
import { normalizeMindmapRecord } from "./mindmapNormalize";

describe("normalizeMindmapRecord", () => {
  it("maps v2 record fields", () => {
    const rec = {
      schema_version: 2, title: "T",
      nodes: [
        { id: "n0", parent: null, kind: "root", title: "T", note: "", chunk_refs: [], order: 0 },
        { id: "n1", parent: "n0", kind: "section", title: "S", note: "tóm", chunk_refs: ["3"], order: 0 },
      ],
      relations: [{ source: "n1", target: "n0", type: "relates_to", label: "" }],
      generator: { degraded: true, missing: ["relations"] },
    };
    const out = normalizeMindmapRecord(rec);
    expect(out.nodes[1].chunkRefs).toEqual(["3"]);
    expect(out.relations).toHaveLength(1);
    expect(out.degraded).toBe(true);
    expect(out.missing).toEqual(["relations"]);
  });

  it("handles legacy v1 nodes-only record", () => {
    const rec = { title: "L", nodes: [{ id: "root", parent: null, title: "L" }] };
    const out = normalizeMindmapRecord(rec);
    expect(out.nodes).toHaveLength(1);
    expect(out.nodes[0].kind).toBe("root");
    expect(out.relations).toEqual([]);
  });

  it("returns empty model for garbage", () => {
    expect(normalizeMindmapRecord(null).nodes).toEqual([]);
  });
});
```

- [ ] **Step 3:** `npm test` → FAIL. Implement `mindmapNormalize.js` theo Interfaces (port logic v1 từ modal — copy, đừng viết mới).
- [ ] **Step 4:** `npm test` → PASS; `npm run build` OK.
- [ ] **Step 5:** Commit — `git commit -m "feat(fe): mindmap normalize v1/v2 + vitest bootstrap"`.

### Task 14: Tách `MindMapModal.jsx` → `components/mindmap/` + render v2

**Files:**
- Create: `FE/src/components/mindmap/MindmapView.jsx` (container + ReactFlow), `FE/src/components/mindmap/MindmapNodeCard.jsx`, `FE/src/components/mindmap/RelationEdge.jsx`, `FE/src/components/mindmap/MindmapToolbar.jsx`, `FE/src/components/mindmap/useElkLayout.js`
- Modify: `FE/src/components/Layout/MindMapModal.jsx` → shell mỏng render `<MindmapView/>` (giữ export cũ để SidebarRight không vỡ), `FE/src/components/Layout/SidebarRight.jsx`

**Nội dung (mechanical move + thêm mới):**
- MOVE các khối từ modal: BRANCH_COLORS, LAYOUT_OPTIONS/DISPLAY_MODES/EDGE_MODES, buildChildrenMap/ParentMap, overview/focus logic, ELK_CONFIGS + layout hook → file tương ứng. Dùng `normalizeMindmapRecord` (Task 13) thay `normalizeHierarchyFromData` nội bộ.
- MỚI `RelationEdge.jsx`: ReactFlow custom edge — nét đứt (`strokeDasharray: "6 4"`), màu seal accent (lấy token màu son đang dùng trong FE — grep `--seal` / accent trong `index.css`), label chữ nhỏ giữa cạnh. Relations render thành edges `type: "relation"` TÁCH khỏi tree edges; toolbar có toggle "Quan hệ" (mặc định BẬT, tắt = ẩn relation edges).
- MỚI trong toolbar: badge degraded — khi `record.generator.degraded`: dải mỏng "Bản đồ chưa đầy đủ (thiếu: <missing>) — Tạo lại" (nút gọi lại generate với `force: true`).
- Quality floor: keyboard focus ring cho node, `prefers-reduced-motion` tắt animation.

- [ ] **Step 1:** Tách file, modal thành shell. `npm run build` xanh sau MỖI file move.
- [ ] **Step 2:** Chạy app (`npm run dev` + BE), mở mindmap cũ (v1 legacy trong sqlite sau migrate) → render như trước (kiểm bằng mắt).
- [ ] **Step 3:** Tạo mindmap mới (BE v2) → thấy relations nét đứt + label; toggle ẩn/hiện; degraded banner khi tắt Ollama.
- [ ] **Step 4:** Commit — `git commit -m "feat(fe): mindmap module split + labeled relation edges + degraded banner"`.

### Task 15 [CODEX]: gRPC per-stage (proto + server + client)

**ĐIỀU KIỆN:** Task 12 xong. Chỉ chạm khi `MINDMAP_SERVICE_ADDR` được set — default in-proc không đổi.

**Files:**
- Modify: `BE/shared/proto/mindmap.proto` — THAY service cũ bằng:

```proto
service MindmapPipeline {
  rpc Skeleton(MindmapInput) returns (SkeletonReply);
  rpc EnrichBranches(EnrichRequest) returns (stream EnrichEvent); // event: progress hoặc final nodes
  rpc Relations(RelationsRequest) returns (RelationsReply);
}
message MindmapInput { string mm_input_json = 1; }
message SkeletonReply { string nodes_json = 1; string method = 2; }
message EnrichRequest { string mm_input_json = 1; string skeleton_json = 2; }
message EnrichEvent { int32 progress = 1; string message = 2; string nodes_json = 3; bool degraded = 4; bool final = 5; }
message RelationsRequest { string nodes_json = 1; }
message RelationsReply { string relations_json = 1; bool degraded = 2; }
```

(JSON-over-proto: state nhỏ, tránh nở message; nhất quán record là JSON ở mọi tầng.)
- Modify: `BE/services/mindmap/server.py` — servicer mới gọi `LocalMindmapPipeline` (KHÔNG đọc đĩa; mọi input qua request). Cancel: client hủy stream → context.is_active() check trong enrich cancel_cb.
- Modify: `BE/app/clients/mindmap_client.py` — `class GrpcMindmapPipeline` cùng interface `skeleton/enrich/relations` (map JSON qua wire; enrich consume stream, gọi progress_cb, trả final).
- Regen: `python scripts/build_proto.py` (gen vào `shared/proto/gen`, gitignored).
- Test: `BE/tests/test_mindmap_service.py` — VIẾT LẠI: in-process grpc (mirror test cũ pattern) với `SKIP_MODEL_LOAD=1`: Skeleton trả nodes; EnrichBranches stream final event; Relations trả rỗng không degraded.

- [ ] Steps: test fail → implement → `python -m pytest tests/test_mindmap_service.py -v` PASS → cập nhật docker-compose env nếu cần (không đổi service list) → commit `feat(mindmap): per-stage gRPC pipeline (stateless service, no disk access)`.

### Task 16: Evidence drawer + skeleton preview + cancel (FE)

**Files:**
- Create: `FE/src/components/mindmap/EvidenceDrawer.jsx`
- Modify: `FE/src/utils/api.js`, `FE/src/components/mindmap/MindmapView.jsx`, `FE/src/components/Layout/SidebarRight.jsx`

**Interfaces:**
- `api.js` thêm: `fetchChunkText(chunkId)` → GET `/chunk-text/<id>`; `cancelMindmap(jobId)` → POST `/mindmap-cancel/<id>`; `generateMindmap(sources, {force})` gửi body mới; poll đọc `partial`.
- EvidenceDrawer: panel trượt phải (overlay trong MindmapView), nhận `node` đang chọn → hiện `note` + list trích đoạn (fetch từng `chunkRefs`, cache Map trong component, cắt 600 ký tự — khớp hợp đồng evidence hiện có); mỗi trích đoạn có nút "Hỏi về đoạn này" → callback `onAskAbout(text)` do MainLayout truyền xuống (prefill input ChatArea — dùng cùng cơ chế SidebarRight đang giao tiếp ChatArea; nếu chưa có, thêm prop callback qua MainLayout).
- Skeleton preview: khi poll `mindmap-status` trả `partial` → render ngay bằng normalize (nodes-only); node có class "đang thở" (CSS pulse nhẹ, tắt khi `prefers-reduced-motion`). Khi `status done` → render record đầy đủ.
- Cancel: nút Huỷ trong progress UI gọi `cancelMindmap(jobId)` rồi dừng polling; status "cancelled" → toast "Đã huỷ tạo sơ đồ".

- [ ] **Step 1:** Viết `EvidenceDrawer` + api helpers; `npm run build` xanh.
- [ ] **Step 2:** Manual: click node có chunkRefs → thấy trích đoạn thật; click node skeleton (chưa enrich) → drawer ghi "Chưa có bằng chứng — đang làm giàu".
- [ ] **Step 3:** Manual: bấm Tạo → khung xương hiện ~1-2s; Huỷ giữa chừng → job cancelled ở BE (`sqlite3 BE/jobs.sqlite "select status from jobs order by updated_at desc limit 1"` = cancelled), KHÔNG có record mới trong mindmaps.sqlite.
- [ ] **Step 4:** Commit — `git commit -m "feat(fe): evidence drawer + skeleton preview + real cancel"`.

### Task 17 [CODEX]: Fullscreen overlay + export PNG

**Files:**
- Modify: `FE/src/components/mindmap/MindmapView.jsx`, `MindmapToolbar.jsx`, `FE/src/components/Layout/MindMapModal.jsx`
- Add dep: `html-to-image`

- Overlay: modal container đổi thành fixed inset-0 z-50, nền theo theme Phòng đọc (dùng token màu nền hiện có của app, không hardcode hex mới), nút đóng góc phải, `Esc` đóng.
- Export PNG: nút trong toolbar — dùng `html-to-image.toPng` trên viewport ReactFlow (pattern chính thức ReactFlow v11: `getRectOfNodes` + `getTransformForBounds`), tên file `mindmap-<title>-<yyyymmdd>.png`, nền đặc (không transparent).
- [ ] Steps: implement → `npm run build` xanh → manual export ra file mở được → commit `feat(fe): fullscreen mindmap overlay + png export`.

### Task 18: Docs + playbook (đóng dự án)

**Files:**
- Modify: `docs/MINDMAP_WORKFLOW.md`, `docs/QUY_TRINH_TAO_SO_DO_TU_DUY.md` (viết lại phần pipeline theo skeleton-first; XOÁ mô tả cache cũ không tồn tại), `.playbook/lessons-learned.md` (mục mới: skeleton-first — root cause pipeline cũ, số đo smoke Task 11, prevention: đổi prompt/logic → bump `PIPELINE_VERSION`), `.playbook/known-issues.md` (xoá/annotate các mục hết hiệu lực về mindmap timeout TEMP nếu đã lỗi thời).
- [ ] Viết → `python -m pytest tests/ -v` xanh lần cuối toàn suite → commit `docs(mindmap): rewrite workflow docs + playbook for skeleton-first v2`.

---

## Thứ tự & song song hoá

```
Task 1 → 2 → 3 → 4 ──────────────┐
         (codex // từ sau T2):    │
Task 5 [CODEX] (cần T2 merge) ────┤
Task 6 [CODEX] (độc lập) ─────────┼→ Task 9 → 10 → 11(smoke) → 12 [CODEX]
Task 7, 8 (sau T2, // nhau) ──────┘                    │
FE: Task 13 (độc lập, bắt đầu bất kỳ lúc nào) → 14 → 16 → 17 [CODEX]
Task 15 [CODEX] sau 12.  Task 18 cuối cùng.
```

## Self-review đã chạy

- Spec coverage: §3.1→T2, §3.2→T5+T10, §3.3→T2, §4.1→T3/T4/T7/T8/T9, §4.2.1-4→T7/T8/T9/T10, §4.2.5→T15, §4.2.6→T12, §4.2.7→T9(test graph thật), §5→T10, §6→T13/T14/T16/T17, §7→test từng task + T11 smoke, §8→bảng codex, docs→T18. Bổ sung ngoài spec: `GET /chunk-text/<id>` (cần cho evidence drawer — spec §6 ngụ ý), vitest bootstrap (spec §7.5 yêu cầu FE test mà FE chưa có runner).
- Placeholder: Task 1 Step 3 cố ý "DÁN NGUYÊN VĂN" (move code có thật, không phải TBD); Task 5/12/15/17 là task codex với interface + test đầy đủ, code body mirror pattern có sẵn được nêu đích danh.
- Type consistency: `mm_input` shape thống nhất T3/T4/T7/T9; pipeline interface `skeleton/enrich/relations` thống nhất T9/T15; `content_hash` (stems, texts) thống nhất T2/T9/T10.
