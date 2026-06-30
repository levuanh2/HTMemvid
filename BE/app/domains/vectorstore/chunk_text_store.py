"""Tầng truy cập text DUY NHẤT cho chunk.

Thứ tự nguồn: (1) chunks.sqlite (runtime, ghi lúc ingest); (2) index.json inline `text`
(tương thích index cũ / fallback khi video lỗi); (3) decode (video, frame_index) on-demand
(recovery, LRU-cache). Video là canonical; sqlite là dẫn xuất, tái dựng được.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Optional

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None
_conn_path: Optional[str] = None
_decode_cache: "OrderedDict[tuple[str,int], str]" = OrderedDict()
_DECODE_CACHE_MAX = int(os.getenv("CHUNK_TEXT_DECODE_CACHE", "512"))


def _db_path() -> str:
    from app.domains.vectorstore import store
    return str(Path(store.INDEX_DIR) / "chunks.sqlite")


def _get_conn() -> sqlite3.Connection:
    global _conn, _conn_path
    path = _db_path()
    with _lock:
        if _conn is not None and _conn_path == path:
            return _conn
        if _conn is not None:
            try: _conn.close()
            except Exception: pass
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("CREATE TABLE IF NOT EXISTS chunks (chunk_id INTEGER PRIMARY KEY, text TEXT)")
        _conn.commit()
        _conn_path = path
        return _conn


def init() -> None:
    _get_conn()


def reset_cache() -> None:
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            try: _conn.close()
            except Exception: pass
        _conn = None
        _conn_path = None
        _decode_cache.clear()


def put_many(items: list[tuple[int, str]]) -> None:
    if not items:
        return
    conn = _get_conn()
    with _lock:
        conn.executemany(
            "INSERT OR REPLACE INTO chunks (chunk_id, text) VALUES (?, ?)",
            [(int(cid), str(t or "")) for cid, t in items],
        )
        conn.commit()


def _from_sqlite(chunk_id: int) -> Optional[str]:
    conn = _get_conn()
    row = conn.execute("SELECT text FROM chunks WHERE chunk_id=?", (int(chunk_id),)).fetchone()
    return row[0] if row else None


def _from_inline_or_video(chunk_id: int) -> Optional[str]:
    from app.domains.vectorstore import store
    meta = store.load_meta() or {}
    entry = meta.get(str(int(chunk_id)))
    if not isinstance(entry, dict):
        return None
    t = (entry.get("text") or "").strip()
    if t:
        return t
    video, fi = entry.get("video"), entry.get("frame_index")
    if video and fi is not None:
        key = (str(video), int(fi))
        if key in _decode_cache:
            _decode_cache.move_to_end(key)
            return _decode_cache[key]
        from app.domains.ingest import video_utils
        try:
            txt = video_utils.decode_frame(str(video), int(fi))
        except Exception:
            txt = None
        if txt:
            _decode_cache[key] = txt
            _decode_cache.move_to_end(key)
            while len(_decode_cache) > _DECODE_CACHE_MAX:
                _decode_cache.popitem(last=False)
            return txt
    return None


def get_text(chunk_id: int) -> Optional[str]:
    t = _from_sqlite(chunk_id)
    if t is not None:
        return t
    return _from_inline_or_video(chunk_id)


def get_texts(ids: Iterable[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for cid in ids:
        t = get_text(int(cid))
        if t is not None:
            out[int(cid)] = t
    return out


def iter_all() -> Iterable[tuple[int, str]]:
    conn = _get_conn()
    rows = conn.execute("SELECT chunk_id, text FROM chunks").fetchall()
    if rows:
        for cid, t in rows:
            yield int(cid), t or ""
        return
    # sqlite trống → fallback index.json inline (index cũ)
    from app.domains.vectorstore import store
    meta = store.load_meta() or {}
    for k, v in meta.items():
        if isinstance(k, str) and k.isdigit() and isinstance(v, dict):
            t = (v.get("text") or "").strip()
            if t:
                yield int(k), t


def mtime() -> float:
    p = _db_path()
    if os.path.exists(p):
        return os.path.getmtime(p)
    from app.domains.vectorstore import store
    return os.path.getmtime(store.META_PATH) if os.path.exists(store.META_PATH) else 0.0


def rebuild_from_videos() -> int:
    """Recovery: dựng lại sqlite từ index.json pointer + decode video. Trả số chunk dựng được."""
    from app.domains.vectorstore import store
    from app.domains.ingest import video_utils
    meta = store.load_meta() or {}
    items: list[tuple[int, str]] = []
    for k, v in meta.items():
        if not (isinstance(k, str) and k.isdigit() and isinstance(v, dict)):
            continue
        t = (v.get("text") or "").strip()
        if not t and v.get("video") and v.get("frame_index") is not None:
            try:
                t = video_utils.decode_frame(str(v["video"]), int(v["frame_index"])) or ""
            except Exception:
                t = ""
        if t:
            items.append((int(k), t))
    put_many(items)
    return len(items)
