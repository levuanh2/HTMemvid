"""Coverage judge (Summary v3 Phase 5) — chấm coverage/faithfulness của bản tóm tắt.

JUDGE-ONLY. Chỉ SINH chẩn đoán có cấu trúc từ artifact đã có (facts/key_points/summary/
overview/study); KHÔNG viết lại summary, KHÔNG auto-repair, KHÔNG thêm pass tóm tắt,
KHÔNG đổi overview/sections/study. Flag SUMMARY_COVERAGE (mặc định OFF).

An toàn:
- Judge lỗi / JSON hỏng / model rỗng → trả None (không chẩn đoán = không lưu), KHÔNG raise
  → không bao giờ làm hỏng job tóm tắt.
- Chỉ gửi artifact source-backed (không raw chunk); cap list để record không phình.
- sanitize: list ép str + cap, vague ép bool, chỉ giữ COVERAGE_KEYS.
- KHÔNG bịa id nguồn/trang: judge chỉ mô tả text, mọi field pointer vẫn thuộc sections.
"""
from __future__ import annotations

import json

from services.mindmap.jsonrepair import repair_json_text

COVERAGE_KEYS = ("covered", "missing", "unsupported", "vague", "notes")
_LIST_KEYS = ("covered", "missing", "unsupported", "notes")

_MAX_LIST = 20            # trần số mục mỗi list chẩn đoán (chống record phình)
_MAX_ITEM_CHARS = 300     # trần độ dài mỗi mục
_MAX_SECTIONS = 30        # trần số section đưa vào payload
_MAX_FACTS = 12           # trần số fact mỗi list/section
_MAX_KEY_POINTS = 8

# Prompt: chỉ chấm coverage/support, cấm viết lại, trả JSON. Đưa vào cả build_coverage_prompt
# (để judge nhận đủ luật) lẫn system prompt của adapter (belt-and-suspenders).
COVERAGE_SYSTEM = (
    "You are evaluating a draft summary against extracted source-backed facts.\n"
    "Only judge coverage and support.\n"
    "Do not rewrite the summary.\n"
    "Do not add new facts.\n"
    "Return JSON only:\n"
    "{\n"
    '  "covered": [],\n'
    '  "missing": [],\n'
    '  "unsupported": [],\n'
    '  "vague": false,\n'
    '  "notes": []\n'
    "}"
)


def _coerce_str_list(value: object, cap: int = _MAX_LIST) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        s = str(v).strip()[:_MAX_ITEM_CHARS]
        if s:
            out.append(s)
        if len(out) >= cap:
            break
    return out


def sanitize_coverage(value: object) -> dict | None:
    """Chuẩn hoá chẩn đoán judge → dict với đúng COVERAGE_KEYS. list ép str + cap;
    vague ép bool (an toàn với mọi kiểu). Không phải dict → None (không chẩn đoán)."""
    if not isinstance(value, dict):
        return None
    return {
        "covered": _coerce_str_list(value.get("covered")),
        "missing": _coerce_str_list(value.get("missing")),
        "unsupported": _coerce_str_list(value.get("unsupported")),
        "vague": bool(value.get("vague")),
        "notes": _coerce_str_list(value.get("notes")),
    }


def build_coverage_payload(record_or_parts: dict) -> dict:
    """Trích artifact SOURCE-BACKED từ record đã build để judge — KHÔNG gửi raw chunk.
    Gồm overview + mỗi section (title/summary/key_points/facts) + study.key_concepts nếu có."""
    rec = record_or_parts if isinstance(record_or_parts, dict) else {}
    sections: list[dict] = []
    for s in (rec.get("sections") or [])[:_MAX_SECTIONS]:
        if not isinstance(s, dict):
            continue
        facts = {}
        for k, vals in (s.get("facts") or {}).items():
            if isinstance(vals, list):
                cleaned = [str(v).strip() for v in vals if str(v).strip()][:_MAX_FACTS]
                if cleaned:
                    facts[k] = cleaned
        sections.append({
            "title": str(s.get("title") or ""),
            "summary": str(s.get("summary") or ""),
            "key_points": _coerce_str_list(s.get("key_points"), cap=_MAX_KEY_POINTS),
            "facts": facts,
        })
    payload = {"overview": str(rec.get("overview") or ""), "sections": sections}
    study = rec.get("study")
    if isinstance(study, dict):
        kc = _coerce_str_list(study.get("key_concepts"))
        if kc:
            payload["study"] = {"key_concepts": kc}
    return payload


def build_coverage_prompt(payload: dict) -> str:
    """User message cho judge: luật (JSON keys + 'do not rewrite') + artifact JSON.
    Artifact bọc giữa <<<DATA>>>/<<<END>>> = DỮ LIỆU cần chấm, không phải lệnh."""
    body = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False, indent=2)
    return (COVERAGE_SYSTEM + "\n\n"
            "Draft summary artifacts to evaluate (JSON):\n"
            "<<<DATA>>>\n" + body + "\n<<<END>>>")


def judge_coverage(record: dict, *, ask_fn, enabled: bool = False) -> dict | None:
    """Chạy judge khi enabled. ask_fn(prompt) -> raw text (adapter lo model/timeout/threadpool).
    Trả chẩn đoán đã sanitize, hoặc None khi: tắt / model rỗng / JSON hỏng / bất kỳ lỗi nào.
    KHÔNG raise — coverage là chẩn đoán phụ, không được chặn job tóm tắt thành công."""
    if not enabled:
        return None
    try:
        payload = build_coverage_payload(record)
        prompt = build_coverage_prompt(payload)
        raw = ask_fn(prompt)
        data = json.loads(repair_json_text(str(raw)))
        return sanitize_coverage(data)
    except Exception as e:      # noqa: BLE001 — degrade an toàn, không làm hỏng job
        print(f"[summary] coverage judge failed: {str(e)[:80]}")
        return None
