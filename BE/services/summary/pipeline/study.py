"""Study block (Summary v3 Phase 3) — gom facts + pointers thành hướng ôn tập.

0 LLM, thuần, DETERMINISTIC:
- key_concepts/definitions/formulas/examples/common_mistakes: gom từ facts các section
  (dedupe không phân biệt hoa/thường, cap).
- self_check: SUY từ facts (open_questions + important_terms), KHÔNG hỏi model → không bịa
  câu ngoài tài liệu. Facts vắng (SUMMARY_FACTS=0) → fallback key_points; vẫn không bịa.
- recommended_review: CHỈ từ pointer thật của section (Phase 2) — không bịa trang/nguồn.

Degrade an toàn: facts rỗng → block facts rỗng; pointer rỗng → recommended_review rỗng."""
from __future__ import annotations

_MAX_ITEMS = 20
_MAX_SELF_CHECK = 10
_MAX_REVIEW = 12


def _dedupe(items: list, cap: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in items or []:
        s = str(it).strip()
        k = s.lower()
        if not s or k in seen:
            continue
        seen.add(k)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def _aggregate(sections: list[dict], fact_key: str) -> list:
    vals: list = []
    for s in sections or []:
        for v in (s.get("facts") or {}).get(fact_key) or []:
            vals.append(v)
    return vals


def _key_concepts(sections: list[dict]) -> list[str]:
    # ưu tiên important_terms; facts OFF (rỗng) → rơi về key_points (luôn có ở summary)
    terms = _aggregate(sections, "important_terms")
    if not terms:
        for s in sections or []:
            terms.extend(s.get("key_points") or [])
    return _dedupe(terms, _MAX_ITEMS)


def _self_check(sections: list[dict]) -> list[dict]:
    """Câu tự kiểm {q, a_hint} suy deterministic từ facts. Không hỏi model."""
    out: list[dict] = []
    seen: set[str] = set()

    def _add(q: str, hint: str) -> bool:
        q = str(q).strip()
        k = q.lower()
        if not q or k in seen:
            return False
        seen.add(k)
        out.append({"q": q, "a_hint": str(hint or "").strip()})
        return len(out) < _MAX_SELF_CHECK

    # 1) open_questions là câu hỏi sẵn của tài liệu → dùng thẳng (hint rỗng)
    for oq in _aggregate(sections, "open_questions"):
        if not _add(oq, ""):
            return out
    # 2) important_terms → "Giải thích khái niệm: X" (hint = định nghĩa khớp nếu có)
    defs = _aggregate(sections, "definitions")
    for term in _aggregate(sections, "important_terms"):
        hint = next((d for d in defs if str(term).lower() in str(d).lower()), term)
        if not _add(f"Giải thích khái niệm: {term}", hint):
            return out
    # 3) fallback: chưa có câu nào (facts OFF) → suy từ key_points
    if not out:
        for s in sections or []:
            for kp in s.get("key_points") or []:
                if not _add(f"Trình bày: {kp}", kp):
                    return out
    return out


def _review_reason(section: dict) -> str:
    facts = section.get("facts") or {}
    parts = []
    if facts.get("formulas"):
        parts.append("công thức")
    if facts.get("definitions"):
        parts.append("định nghĩa")
    if facts.get("common_mistakes"):
        parts.append("điểm dễ nhầm")
    if facts.get("examples"):
        parts.append("ví dụ")
    return "Ôn lại " + (", ".join(parts) if parts else "nội dung chính") + " của mục này"


def _recommended_review(sections: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in sections or []:
        pts = s.get("pointers") or []
        if not pts:
            continue                     # không có pointer thật → bỏ, không bịa trang/nguồn
        p = pts[0]                        # pointer đại diện mục
        out.append({
            "title": f"Ôn lại: {s.get('title') or ''}".strip(),
            "section_title": s.get("title") or None,
            "chunk_id": p.get("chunk_id"),
            "source_id": p.get("source_id"),
            "source_stem": p.get("source_stem"),
            "page": p.get("page"),
            "reason": _review_reason(s),
        })
        if len(out) >= _MAX_REVIEW:
            break
    return out


def build_study(sections: list[dict]) -> dict:
    """Trả block study từ sections đã sanitize (có facts/pointers khi bật). Thuần, 0 LLM."""
    return {
        "key_concepts": _key_concepts(sections),
        "definitions": _dedupe(_aggregate(sections, "definitions"), _MAX_ITEMS),
        "formulas": _dedupe(_aggregate(sections, "formulas"), _MAX_ITEMS),
        "examples": _dedupe(_aggregate(sections, "examples"), _MAX_ITEMS),
        "common_mistakes": _dedupe(_aggregate(sections, "common_mistakes"), _MAX_ITEMS),
        "self_check": _self_check(sections),
        "recommended_review": _recommended_review(sections),
    }
