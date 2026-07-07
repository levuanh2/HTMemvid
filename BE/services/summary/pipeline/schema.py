"""Schema Summary v2: record section-first + content_hash cache key."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

PIPELINE_VERSION = "summary_sections_v1"
LENGTH_MODES = ("short", "medium", "detailed")
MAX_SECTIONS = 30
MAX_KEY_POINTS = 8


def content_hash(source_stems: list[str], chunk_texts: list[str],
                 chunk_headings: list[str] | None, length_mode: str) -> str:
    """Cache key: hash MỌI input ảnh hưởng output (bài học mindmap content_hash).

    length_mode nằm trong hash — đổi độ dài là bản tóm tắt khác, không được
    trả cache của mode khác. Đổi PIPELINE_VERSION tự vô hiệu cache cũ.
    """
    h = hashlib.sha256()
    h.update(PIPELINE_VERSION.encode("utf-8"))
    h.update(b"\x03" + (length_mode or "medium").encode("utf-8"))
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
        out.append({
            "id": sid,
            "title": title,
            "summary": (s.get("summary") or "").strip(),
            "key_points": [str(p).strip() for p in (s.get("key_points") or [])[:MAX_KEY_POINTS]
                           if str(p).strip()],
            "chunk_refs": refs,
            "order": int(s.get("order", i)),
        })
        if len(out) >= MAX_SECTIONS:
            break
    return out


def build_record(*, title: str, sources: list[str], length_mode: str, overview: str,
                 sections: list[dict], entities: list[str], content_hash_value: str,
                 model: str, elapsed_sec: float, degraded_missing: list[str],
                 skeleton_method: str = "") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "schema_version": 2,
        "title": title,
        "sources": list(sources or []),
        "content_hash": content_hash_value,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "length_mode": length_mode if length_mode in LENGTH_MODES else "medium",
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
