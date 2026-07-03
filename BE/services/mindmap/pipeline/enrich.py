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
