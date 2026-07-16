"""Dedup + polish (Summary v3 Phase 4) — bỏ ý lặp giữa các mục/khối study.

THUẦN, 0 LLM, KHÔNG sinh chữ mới, KHÔNG viết lại. Chỉ so khớp CHÍNH XÁC theo bản
chuẩn hoá (normalize_text) — bảo thủ: hai câu khác nhau (dù na ná) đều GIỮ, không gộp
ý không liên quan, không xoá fact duy nhất. Khi trùng chuẩn hoá → giữ bản ĐẦY ĐỦ hơn
(dài hơn); bằng nhau → giữ bản gặp đầu. Giữ nguyên thứ tự lần gặp đầu. Không đụng
summary/overview/chunk_refs/pointers (pointer cần cho review)."""
from __future__ import annotations

import re

from services.summary.pipeline.schema import FACTS_KEYS, MAX_FACT_ITEMS, MAX_KEY_POINTS

# Punctuation nhẹ — bỏ CHỈ để so khớp (không đụng chữ hiển thị). GIỮ dấu tiếng Việt
# (không bỏ diacritics → "bàn" ≠ "bán"), chỉ gộp khoảng trắng + bỏ dấu câu.
_PUNCT = re.compile(r"""[.,;:!?…"'`()\[\]{}/\\|]+""")
_WS = re.compile(r"\s+")

_STUDY_LIST_KEYS = ("key_concepts", "definitions", "formulas", "examples", "common_mistakes")
_MAX_STUDY_SELF_CHECK = 10
_MAX_STUDY_REVIEW = 12


def normalize_text(value: object) -> str:
    """Bản CHUẨN HOÁ dùng để SO KHỚP (không phải để hiển thị): lower + strip + gộp
    khoảng trắng + bỏ dấu câu nhẹ. Không stem, không dịch, không bỏ diacritics."""
    s = str(value or "").lower().strip()
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def dedupe_strings(items: list, *, max_items: int | None = None) -> list[str]:
    """Bỏ trùng theo normalize_text; giữ bản dài hơn (đầy đủ hơn) khi trùng, giữ thứ tự
    lần gặp đầu. Bỏ chuỗi rỗng. Cap nếu truyền max_items (dedupe chỉ co lại nên cap ≈ no-op)."""
    best: dict[str, list] = {}   # norm -> [order, display_text]
    order = 0
    for it in items or []:
        text = str(it).strip()
        n = normalize_text(text)
        if not n:
            continue
        if n in best:
            if len(text) > len(best[n][1]):
                best[n][1] = text          # bản đầy đủ hơn (giữ nguyên slot thứ tự)
        else:
            best[n] = [order, text]
            order += 1
    out = [t for _, t in sorted(best.values(), key=lambda x: x[0])]
    return out[:max_items] if max_items is not None else out


def dedupe_facts(facts: object) -> dict:
    """Dedupe từng list trong facts ledger (giữ FACTS_KEYS, cap như schema, bỏ key rỗng)."""
    if not isinstance(facts, dict):
        return {}
    out: dict[str, list[str]] = {}
    for k in FACTS_KEYS:
        vals = facts.get(k)
        if not isinstance(vals, list):
            continue
        cleaned = dedupe_strings(vals, max_items=MAX_FACT_ITEMS)
        if cleaned:
            out[k] = cleaned
    return out


def dedupe_sections(sections: list) -> list[dict]:
    """Dedupe key_points + facts NỘI BỘ mỗi section. KHÔNG đụng chunk_refs/pointers/summary."""
    out: list[dict] = []
    for s in sections or []:
        if not isinstance(s, dict):
            out.append(s)
            continue
        s = dict(s)
        if isinstance(s.get("key_points"), list):
            s["key_points"] = dedupe_strings(s["key_points"], max_items=MAX_KEY_POINTS)
        if "facts" in s:
            facts = dedupe_facts(s.get("facts"))
            if facts:
                s["facts"] = facts
            else:
                s.pop("facts", None)     # dedupe làm rỗng hết → bỏ key (nhất quán sanitize)
        out.append(s)
    return out


def _dedupe_self_check(items: list) -> list[dict]:
    """Dedupe theo câu hỏi (normalize q). Item dạng {q, a_hint} (hoặc chuỗi → coi là q)."""
    out: list[dict] = []
    seen: set[str] = set()
    for it in items or []:
        if isinstance(it, dict):
            q = str(it.get("q") or "").strip()
            hint = str(it.get("a_hint") or "").strip()
        else:
            q, hint = str(it).strip(), ""
        n = normalize_text(q)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append({"q": q, "a_hint": hint})
        if len(out) >= _MAX_STUDY_SELF_CHECK:
            break
    return out


def _dedupe_review(items: list) -> list[dict]:
    """Dedupe theo key deterministic (chunk_id, section_title, reason). Giữ bản đầu +
    MỌI field pointer (không mất nguồn để review)."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        key = (str(it.get("chunk_id")), normalize_text(it.get("section_title")),
               normalize_text(it.get("reason")))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= _MAX_STUDY_REVIEW:
            break
    return out


def dedupe_study(study: object) -> dict:
    """Dedupe các list study; self_check theo câu hỏi; recommended_review theo key."""
    if not isinstance(study, dict):
        return {}
    out = dict(study)
    for k in _STUDY_LIST_KEYS:
        if isinstance(out.get(k), list):
            out[k] = dedupe_strings(out[k])
    if isinstance(out.get("self_check"), list):
        out["self_check"] = _dedupe_self_check(out["self_check"])
    if isinstance(out.get("recommended_review"), list):
        out["recommended_review"] = _dedupe_review(out["recommended_review"])
    return out


def dedupe_record(record: object) -> dict:
    """Pass dedup trên record đã build (in-place, trả lại). Chỉ đụng sections + study —
    summary/overview/entities/pointers/chunk_refs giữ nguyên."""
    if not isinstance(record, dict):
        return record
    record["sections"] = dedupe_sections(record.get("sections") or [])
    if isinstance(record.get("study"), dict):
        record["study"] = dedupe_study(record["study"])
    return record
