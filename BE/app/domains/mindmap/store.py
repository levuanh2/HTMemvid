from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from shared.source_id import canonical_source_stem

_lock = threading.Lock()


def db_path() -> Path:
    override = (os.environ.get("MINDMAPS_DB_PATH") or "").strip()
    if override:
        return Path(override)
    memory_dir = (os.environ.get("MEMORY_DIR") or "").strip()
    if memory_dir:
        return Path(memory_dir) / "mindmaps.sqlite"
    from shared.paths import BE_ROOT
    return BE_ROOT / "memory" / "mindmaps.sqlite"


def get_conn() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mindmaps (
                    id TEXT PRIMARY KEY,
                    content_hash TEXT,
                    sources_json TEXT,
                    created_at TEXT,
                    record_json TEXT,
                    user_id TEXT
                );
                """
            )
            _ensure_columns(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mm_hash ON mindmaps(content_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mm_user ON mindmaps(user_id, content_hash)")
            conn.commit()
        finally:
            conn.close()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    # Additive migration (Auth Hardening Phase A): owner column, nullable.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(mindmaps)").fetchall()}
    if "user_id" not in cols:
        conn.execute("ALTER TABLE mindmaps ADD COLUMN user_id TEXT")


def _canonical_sources(record: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in record.get("sources") or []:
        stem = canonical_source_stem(item)
        if not stem or stem in seen:
            continue
        seen.add(stem)
        out.append(stem)
    return out


def _created_at(record: dict[str, Any]) -> str:
    return str(record.get("created_at") or record.get("createdAt") or "")


def _normalized_record(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    normalized["sources"] = _canonical_sources(normalized)
    if "created_at" not in normalized and normalized.get("createdAt") is not None:
        normalized["created_at"] = normalized.get("createdAt")
    return normalized


def _decode_record(raw: str | None) -> Optional[dict]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def save_record(record: dict, user_id: Optional[str] = None) -> None:
    init_db()
    normalized = _normalized_record(record)
    with _lock:
        conn = get_conn()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO mindmaps(id, content_hash, sources_json, created_at, record_json, user_id)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    str(normalized.get("id") or ""),
                    str(normalized.get("content_hash") or ""),
                    json.dumps(normalized.get("sources") or [], ensure_ascii=False),
                    _created_at(normalized),
                    json.dumps(normalized, ensure_ascii=False),
                    user_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def get_by_hash(content_hash: str, user_id: Optional[str] = None, enforce_owner: bool = False) -> dict | None:
    """Auth Hardening Phase D: enforce_owner=True binds the lookup to user_id so User B
    can never reuse User A's record on an identical content_hash. A legacy NULL owner is
    excluded under enforcement (user_id=? never matches NULL). Flag off → global lookup."""
    target = str(content_hash or "").strip()
    if not target:
        return None
    init_db()
    with _lock:
        conn = get_conn()
        try:
            if enforce_owner:
                cur = conn.execute(
                    "SELECT record_json FROM mindmaps WHERE content_hash=? AND user_id=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (target, user_id),
                )
            else:
                cur = conn.execute(
                    "SELECT record_json FROM mindmaps WHERE content_hash=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (target,),
                )
            row = cur.fetchone()
            return _decode_record(row[0]) if row else None
        finally:
            conn.close()


def list_records(user_id: Optional[str] = None, enforce_owner: bool = False) -> list[dict]:
    """enforce_owner=True → only the caller's records (legacy NULL-owner hidden)."""
    init_db()
    with _lock:
        conn = get_conn()
        try:
            if enforce_owner:
                cur = conn.execute(
                    "SELECT record_json FROM mindmaps WHERE user_id=? ORDER BY created_at DESC",
                    (user_id,),
                )
            else:
                cur = conn.execute("SELECT record_json FROM mindmaps ORDER BY created_at DESC")
            rows = cur.fetchall()
            records: list[dict] = []
            for row in rows:
                record = _decode_record(row[0])
                if record is not None:
                    records.append(record)
            return records
        finally:
            conn.close()


def get_record(mindmap_id: str, user_id: Optional[str] = None, enforce_owner: bool = False) -> Optional[dict]:
    """enforce_owner=True → None for another user's (or legacy NULL-owner) record."""
    init_db()
    with _lock:
        conn = get_conn()
        try:
            if enforce_owner:
                row = conn.execute(
                    "SELECT record_json FROM mindmaps WHERE id = ? AND user_id = ?",
                    (str(mindmap_id), user_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT record_json FROM mindmaps WHERE id = ?",
                    (str(mindmap_id),),
                ).fetchone()
        finally:
            conn.close()
    return _decode_record(row[0]) if row else None


def delete_record(mindmap_id: str, user_id: Optional[str] = None, enforce_owner: bool = False) -> bool:
    """enforce_owner=True → deletes only if owned by user_id (foreign → no-op → False)."""
    init_db()
    with _lock:
        conn = get_conn()
        try:
            if enforce_owner:
                cur = conn.execute(
                    "DELETE FROM mindmaps WHERE id=? AND user_id=?",
                    (str(mindmap_id or ""), user_id),
                )
            else:
                cur = conn.execute("DELETE FROM mindmaps WHERE id=?", (str(mindmap_id or ""),))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def _decode_sources(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        stem = canonical_source_stem(item)
        if stem:
            out.append(stem)
    return out


def delete_by_source(stem: str) -> int:
    target = canonical_source_stem(stem)
    if not target:
        return 0
    init_db()
    with _lock:
        conn = get_conn()
        try:
            cur = conn.execute("SELECT id, sources_json FROM mindmaps")
            ids = [
                row[0]
                for row in cur.fetchall()
                if target in _decode_sources(row[1])
            ]
            deleted = 0
            for mindmap_id in ids:
                res = conn.execute("DELETE FROM mindmaps WHERE id=?", (mindmap_id,))
                deleted += max(res.rowcount, 0)
            conn.commit()
            return deleted
        finally:
            conn.close()


def migrate_from_json(json_path: Path) -> int:
    init_db()
    p = Path(json_path)
    if not p.exists():
        return 0
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(payload, list):
        return 0

    inserted = 0
    with _lock:
        conn = get_conn()
        try:
            for item in payload:
                if not isinstance(item, dict):
                    continue
                record = dict(item)
                if not record.get("id"):
                    continue
                if "schema_version" not in record:
                    record["schema_version"] = 1
                if "content_hash" not in record:
                    record["content_hash"] = ""
                if "created_at" not in record and record.get("createdAt") is not None:
                    record["created_at"] = record.get("createdAt")
                normalized = _normalized_record(record)
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO mindmaps(id, content_hash, sources_json, created_at, record_json)
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        str(normalized.get("id") or ""),
                        str(normalized.get("content_hash") or ""),
                        json.dumps(normalized.get("sources") or [], ensure_ascii=False),
                        _created_at(normalized),
                        json.dumps(normalized, ensure_ascii=False),
                    ),
                )
                inserted += max(cur.rowcount, 0)
            conn.commit()
        finally:
            conn.close()

    # Chặn "hồi sinh": record đã xoá khỏi sqlite sẽ bị re-import ở lần restart sau
    # nếu json backup còn nguyên tên. Migrate xong → rename thành .migrated (giữ backup).
    try:
        p.replace(p.with_name(p.name + ".migrated"))
    except Exception:
        pass  # rename fail → hành vi cũ (benign), lần sau thử lại
    return inserted
