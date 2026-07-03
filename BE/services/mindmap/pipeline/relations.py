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
