"""Schema Summary v2: record section-first + content_hash cache key."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

# v5: pass dedup/polish (Phase 4) đổi NỘI DUNG output (bỏ ý lặp) → bump để cache cũ
# (v4, chưa dedup) miss + tái sinh bản đã dedup. Dedup thuần, 0 LLM.
PIPELINE_VERSION = "summary_sections_v5"
LENGTH_MODES = ("short", "medium", "detailed")
# mode = MỤC ĐÍCH/định dạng (trực giao length_mode = độ dài). Mặc định "standard".
SUMMARY_MODES = ("standard", "study")
MAX_SECTIONS = 30
MAX_KEY_POINTS = 8

# Summary v3 facts ledger — canonical intermediate. Free-text lists (KHÔNG phải id
# chunk nên không lọc theo allowed_set như chunk_refs), chỉ coerce str + bỏ rỗng + cap.
FACTS_KEYS = ("key_points", "definitions", "formulas", "examples",
              "important_terms", "common_mistakes", "open_questions")
MAX_FACT_ITEMS = 12

# Source pointer (Phase 2) — bộ field cố định; field lạ bị bỏ ở sanitize_pointers.
POINTER_KEYS = ("chunk_id", "source_id", "source_stem", "page",
                "section_title", "heading_path", "chunk_index")


def sanitize_pointers(raw: object) -> list[dict]:
    """Chuẩn hoá list pointer: chỉ giữ POINTER_KEYS, cần chunk_id, dedupe theo chunk_id,
    heading_path ép list[str], section_title fallback = mục cuối heading_path. Field lạ bỏ.
    Không phải list → []. Pointer do build_pointers sinh vốn đã sạch; hàm này idempotent +
    phòng khi section đến kèm pointers (pass-through/legacy)."""
    out: list[dict] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for p in raw:
        if not isinstance(p, dict) or p.get("chunk_id") in (None, ""):
            continue
        cid = str(p["chunk_id"])
        if cid in seen:
            continue
        seen.add(cid)
        hp = p.get("heading_path")
        hp_list = [str(x).strip() for x in hp if str(x).strip()] if isinstance(hp, list) else []
        st = p.get("section_title")
        st = str(st).strip() if st not in (None, "") else (hp_list[-1] if hp_list else None)
        out.append({
            "chunk_id": cid,
            "source_id": str(p["source_id"]) if p.get("source_id") not in (None, "") else None,
            "source_stem": str(p["source_stem"]) if p.get("source_stem") not in (None, "") else None,
            "page": p.get("page"),
            "section_title": st or None,
            "heading_path": hp_list,
            "chunk_index": p.get("chunk_index"),
        })
    return out


def sanitize_facts(raw: object) -> dict:
    """Chuẩn hoá facts ledger: chỉ giữ FACTS_KEYS, ép list[str], bỏ mục rỗng, cap.
    Danh sách rỗng → bỏ hẳn key (không lưu key rỗng, không bịa). Trả {} nếu không có gì."""
    facts: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return facts
    for k in FACTS_KEYS:
        vals = raw.get(k)
        if not isinstance(vals, list):
            continue
        cleaned = [str(v).strip() for v in vals if str(v).strip()][:MAX_FACT_ITEMS]
        if cleaned:
            facts[k] = cleaned
    return facts


def content_hash(source_stems: list[str], chunk_texts: list[str],
                 chunk_headings: list[str] | None, length_mode: str,
                 mode: str = "standard") -> str:
    """Cache key: hash MỌI input ảnh hưởng output (bài học mindmap content_hash).

    length_mode + mode nằm trong hash — đổi độ dài HAY đổi mục đích (standard/study)
    là bản tóm tắt khác, không trả cache của combo khác. mode mặc định "standard" giữ
    hash tương thích cho caller cũ chưa truyền mode. Đổi PIPELINE_VERSION vô hiệu cache cũ.
    """
    h = hashlib.sha256()
    h.update(PIPELINE_VERSION.encode("utf-8"))
    h.update(b"\x03" + (length_mode or "medium").encode("utf-8"))
    h.update(b"\x04" + (mode or "standard").encode("utf-8"))
    for s in sorted(source_stems or []):
        h.update(b"\x00" + s.encode("utf-8"))
    for t in chunk_texts or []:
        h.update(b"\x01" + (t or "").encode("utf-8"))
    for hp in chunk_headings or []:
        h.update(b"\x02" + (hp or "").encode("utf-8"))
    return h.hexdigest()


def sanitize_sections(sections: list[dict], valid_chunk_ids: set[str]) -> list[dict]:
    """Bỏ section rỗng, dedupe id, ép chunk_refs về str + lọc theo id thật (chống bịa)."""
    seen: set[str] = set()
    out: list[dict] = []
    for i, s in enumerate(sections or []):
        sid = str(s.get("id") or f"s{i + 1}")
        title = (s.get("title") or "").strip()
        if not title or sid in seen:
            continue
        seen.add(sid)
        refs: list[str] = []
        for k in s.get("chunk_refs") or []:
            k = str(k)
            if k in valid_chunk_ids and k not in refs:
                refs.append(k)
        item = {
            "id": sid,
            "title": title,
            "summary": (s.get("summary") or "").strip(),
            "key_points": [str(p).strip() for p in (s.get("key_points") or [])[:MAX_KEY_POINTS]
                           if str(p).strip()],
            "chunk_refs": refs,
            "order": int(s.get("order", i)),
        }
        # Facts ledger (Summary v3): giữ khi có, bỏ hẳn key nếu rỗng → section v2
        # (không facts) vẫn hợp lệ, back-compat hoàn toàn.
        facts = sanitize_facts(s.get("facts"))
        if facts:
            item["facts"] = facts
        # Source pointers (Phase 2): giữ khi có, bỏ key nếu rỗng (nhất quán với facts).
        # Độc lập cờ SUMMARY_FACTS — pointer suy từ chunk_refs, hoạt động cả summary chuẩn.
        pointers = sanitize_pointers(s.get("pointers"))
        if pointers:
            item["pointers"] = pointers
        out.append(item)
        if len(out) >= MAX_SECTIONS:
            break
    return out


def build_record(*, title: str, sources: list[str], length_mode: str, overview: str,
                 sections: list[dict], entities: list[str], content_hash_value: str,
                 model: str, elapsed_sec: float, degraded_missing: list[str],
                 skeleton_method: str = "", mode: str = "standard",
                 study: dict | None = None) -> dict:
    rec = {
        "id": str(uuid.uuid4()),
        "schema_version": 2,
        "title": title,
        "sources": list(sources or []),
        "content_hash": content_hash_value,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "length_mode": length_mode if length_mode in LENGTH_MODES else "medium",
        # mode trực giao length_mode; record cũ thiếu key → FE default "standard".
        "mode": mode if mode in SUMMARY_MODES else "standard",
        "overview": overview or "",
        "sections": sections,
        "entities": [str(e).strip() for e in (entities or []) if str(e).strip()][:20],
        "generator": {
            "pipeline": PIPELINE_VERSION,
            "model": model,
            "elapsed_sec": round(float(elapsed_sec), 1),
            "degraded": bool(degraded_missing),
            "missing": list(degraded_missing or []),
            "skeleton_method": skeleton_method or "",
        },
    }
    # Block study CHỈ khi mode=study + có nội dung → additive, standard record y hệt cũ.
    if study:
        rec["study"] = study
    return rec
