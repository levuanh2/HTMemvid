# QR Canonical Text Store + Slim index.json — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the QR video the canonical/portable text archive, slim `index.json` to pointers `(video, frame_index)` + metadata, and serve runtime text from a derived `chunks.sqlite` rebuildable from video.

**Architecture:** Three stores with clear roles — `videos/*.mp4` (canonical archive + recovery), `index/index.json` (pointers + metadata, no text), `index/chunks.sqlite` (runtime text, derived). A single access layer `chunk_text_store` is the only place that reads chunk text; consumers (BM25, memory, mindmap, endpoints) go through it. Text is written to sqlite at ingest time (no video decode in normal operation); video decode is recovery-only.

**Tech Stack:** Python 3.11, sqlite3 (stdlib), OpenCV (cv2) QR decode, FAISS, rank_bm25, langgraph 0.2.x.

## Global Constraints

- Pin chặt: langgraph 0.2.x / langchain 0.3.x / pydantic <2.11 / torch 2.5.1+cpu — KHÔNG nâng, KHÔNG thêm dependency mới (chỉ stdlib `sqlite3` + cv2/numpy đã có).
- Test env: chạy pytest bằng **global `python`** (`cd BE && python -m pytest ...`). Unit test KHÔNG tải model (`SKIP_MODEL_LOAD=1` hoặc fake).
- `chunk_id` là int, dùng CHUNG làm khóa của `index.json`, FAISS id, và `chunks.sqlite`.
- Sau thay đổi: `python -c "import app.graphs.ingest_graph; import app.graphs.query_graph"` phải pass.
- Chỉ ghi video `.mp4` (mp4v) để khớp `rebuild_index_from_video.glob("*.mp4")`.
- Single source of truth cho text reads = module `chunk_text_store`. Không đọc `meta[id]["text"]` trực tiếp ở consumer mới.
- Sau khi xong: cập nhật `.playbook` (root cause/prevention/regression).

---

### Task 1: video_utils — decode giữ thứ tự + decode 1 frame + chỉ .mp4

**Files:**
- Modify: `BE/app/domains/ingest/video_utils.py` (`decode_video_qr` ~113-160; `save_qr_frames_to_video` candidates list)
- Test: `BE/tests/test_video_codec.py` (mở rộng)

**Interfaces:**
- Produces: `decode_video_qr(path) -> list[tuple[int, str]]` (frame_index, chunk_text) THEO THỨ TỰ frame; `decode_frame(path, frame_index) -> str | None`.

- [ ] **Step 1: Write failing test** (append to `tests/test_video_codec.py`)

```python
def test_decode_video_qr_preserves_order(monkeypatch):
    import app.domains.ingest.video_utils as vu

    class _Cap:
        def __init__(self, path): self._i = 0
        def read(self):
            frames = ["[METADATA:parent=0,order=1,video=d.mp4,ts=t,checksum=x] alpha",
                      "[METADATA:parent=1,order=1,video=d.mp4,ts=t,checksum=y] beta"]
            if self._i >= len(frames): return False, None
            f = frames[self._i]; self._i += 1
            return True, f
        def release(self): pass

    class _Det:
        def __init__(self): self._q = ["[METADATA:parent=0,order=1,video=d.mp4,ts=t,checksum=x] alpha",
                                       "[METADATA:parent=1,order=1,video=d.mp4,ts=t,checksum=y] beta"]
            # detect returns the frame string we passed in
        def detectAndDecode(self, frame): return frame, None, None

    monkeypatch.setattr(vu.cv2, "VideoCapture", _Cap)
    monkeypatch.setattr(vu.cv2, "QRCodeDetector", _Det)
    out = vu.decode_video_qr("d.mp4")
    assert [fi for fi, _ in out] == [0, 1], "phải giữ thứ tự frame"
    assert [t for _, t in out] == ["alpha", "beta"]
```

- [ ] **Step 2: Run test, verify FAIL**

Run: `cd BE && SKIP_MODEL_LOAD=1 python -m pytest tests/test_video_codec.py::test_decode_video_qr_preserves_order -v`
Expected: FAIL (current returns `list(set)` of full strings, không phải tuple có thứ tự).

- [ ] **Step 3: Rewrite `decode_video_qr`** (replace body, bỏ checksum-skip làm mất thứ tự — vẫn verify nhưng giữ frame index)

```python
def decode_video_qr(path: str) -> List[tuple[int, str]]:
    """Decode QR theo THỨ TỰ frame. Trả [(frame_index, chunk_text)]. Verify checksum nếu có
    (sai → bỏ frame đó nhưng KHÔNG đổi frame_index của frame khác)."""
    cap = cv2.VideoCapture(path)
    detector = cv2.QRCodeDetector()
    QR_METADATA_PREFIX, QR_METADATA_SUFFIX = "[METADATA:", "]"

    def _checksum(text: str) -> str:
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]

    def _extract(decoded: str):
        if not decoded.startswith(QR_METADATA_PREFIX):
            return None, decoded.strip()
        end = decoded.find(QR_METADATA_SUFFIX)
        if end == -1:
            return None, decoded.strip()
        meta = {}
        for part in decoded[len(QR_METADATA_PREFIX):end].split(','):
            if '=' in part:
                k, v = part.split('=', 1); meta[k.strip()] = v.strip()
        return meta, decoded[end + 1:].strip()

    out: List[tuple[int, str]] = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        text, _pts, _ = detector.detectAndDecode(frame)
        if text:
            meta, chunk_text = _extract(text)
            ok = (not meta) or (meta.get("checksum") is None) or (str(meta.get("checksum")) == _checksum(chunk_text))
            if ok:
                out.append((idx, chunk_text))
        idx += 1
    cap.release()
    return out


def decode_frame(path: str, frame_index: int) -> Optional[str]:
    """Decode 1 frame theo index (recovery on-demand)."""
    cap = cv2.VideoCapture(path)
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
        ret, frame = cap.read()
        if not ret:
            return None
        detector = cv2.QRCodeDetector()
        text, _pts, _ = detector.detectAndDecode(frame)
        if not text:
            return None
        end = text.find("]")
        return text[end + 1:].strip() if text.startswith("[METADATA:") and end != -1 else text.strip()
    finally:
        cap.release()
```

Add `from typing import Optional` if missing.

- [ ] **Step 4: Restrict save to .mp4** — in `save_qr_frames_to_video`, change `candidates` to mp4 only:

```python
    candidates = [('mp4v', '.mp4'), ('avc1', '.mp4')]
```

- [ ] **Step 5: Run tests, verify PASS**

Run: `cd BE && SKIP_MODEL_LOAD=1 python -m pytest tests/test_video_codec.py -v`
Expected: PASS (all). The fallthrough test now only has .mp4 candidates → update its `_video_is_valid` monkeypatch to `lambda p: p.endswith(".mp4")`.

- [ ] **Step 6: Commit**

```bash
git add BE/app/domains/ingest/video_utils.py BE/tests/test_video_codec.py
git commit -m "fix(video): ordered QR decode + single-frame decode + mp4-only"
```

---

### Task 2: chunk_processor — gán frame_index sau khi lọc frame hỏng

**Files:**
- Modify: `BE/app/domains/ingest/chunk_processor.py` (sau `metadata_entries = [e for e in metadata_entries if e is not None]`)
- Test: `BE/tests/test_chunk_processor_index.py` (mở rộng)

**Interfaces:**
- Produces: mỗi entry có `entry["frame_index"]` = vị trí 0-based trong list cuối cùng (khớp thứ tự ghi video).

- [ ] **Step 1: Write failing test**

```python
def test_entries_get_frame_index_after_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(cp, "save_qr_frames_to_video", lambda frames, prefix="": str(tmp_path / "v.mp4"))
    import app.domains.vectorstore.store as store
    monkeypatch.setattr(store, "_load_meta", lambda: {})
    _v, entries = cp.process_and_store_chunks(["a", "b", "c"], "doc.mp4", "2026-06-30T00:00:00")
    assert [e["frame_index"] for e in entries] == list(range(len(entries)))
```

- [ ] **Step 2: Run, verify FAIL** — `cd BE && SKIP_MODEL_LOAD=1 python -m pytest tests/test_chunk_processor_index.py::test_entries_get_frame_index_after_filter -v` → FAIL (KeyError frame_index).

- [ ] **Step 3: Implement** — after the filter line, before saving video:

```python
    qr_frames = [f for f in qr_frames if f is not None]
    metadata_entries = [e for e in metadata_entries if e is not None]
    for i, e in enumerate(metadata_entries):
        e["frame_index"] = i   # khớp thứ tự ghi video (frame i ↔ entry i)
```

- [ ] **Step 4: Run, verify PASS** — same command → PASS. Also run full file.

- [ ] **Step 5: Commit**

```bash
git add BE/app/domains/ingest/chunk_processor.py BE/tests/test_chunk_processor_index.py
git commit -m "feat(ingest): persist frame_index per chunk entry (post-filter)"
```

---

### Task 3: chunk_text_store — sqlite text store + fallback layer

**Files:**
- Create: `BE/app/domains/vectorstore/chunk_text_store.py`
- Test: `BE/tests/test_chunk_text_store.py`

**Interfaces:**
- Consumes: `store.INDEX_DIR` (path), `store.load_meta()` (read index.json), `video_utils.decode_frame`.
- Produces: `put_many(items: list[tuple[int,str]])`; `get_text(chunk_id:int)->str|None`; `get_texts(ids)->dict[int,str]`; `iter_all()->Iterable[tuple[int,str]]`; `mtime()->float`; `rebuild_from_videos()->int`; `reset_cache()`; `init()`.

- [ ] **Step 1: Write failing tests**

```python
import importlib
import app.domains.vectorstore.store as store


def _setup(tmp_path):
    store.INDEX_DIR = tmp_path
    store.META_PATH = str(tmp_path / "index.json")
    store.INDEX_PATH = str(tmp_path / "index.faiss")
    cts = importlib.import_module("app.domains.vectorstore.chunk_text_store")
    cts.reset_cache()
    return cts


def test_put_get_iter(tmp_path):
    cts = _setup(tmp_path)
    cts.put_many([(0, "alpha"), (1, "beta")])
    assert cts.get_text(0) == "alpha"
    assert cts.get_texts([0, 1]) == {0: "alpha", 1: "beta"}
    assert sorted(cts.iter_all()) == [(0, "alpha"), (1, "beta")]


def test_get_text_falls_back_to_inline_meta(tmp_path, monkeypatch):
    cts = _setup(tmp_path)  # sqlite trống
    monkeypatch.setattr(store, "load_meta", lambda: {"5": {"text": "legacy"}})
    assert cts.get_text(5) == "legacy"


def test_get_text_decodes_video_when_no_sqlite_no_inline(tmp_path, monkeypatch):
    cts = _setup(tmp_path)
    monkeypatch.setattr(store, "load_meta", lambda: {"7": {"video": "d.mp4", "frame_index": 2}})
    import app.domains.ingest.video_utils as vu
    monkeypatch.setattr(vu, "decode_frame", lambda path, fi: "from_video" if (path == "d.mp4" and fi == 2) else None)
    assert cts.get_text(7) == "from_video"
    # cache: lần 2 không gọi decode (đổi decode_frame để chứng minh)
    monkeypatch.setattr(vu, "decode_frame", lambda path, fi: None)
    assert cts.get_text(7) == "from_video"


def test_iter_all_uses_inline_when_sqlite_empty(tmp_path, monkeypatch):
    cts = _setup(tmp_path)
    monkeypatch.setattr(store, "load_meta", lambda: {"0": {"text": "x"}, "1": {"text": "y"}})
    assert sorted(cts.iter_all()) == [(0, "x"), (1, "y")]
```

- [ ] **Step 2: Run, verify FAIL** — `cd BE && SKIP_MODEL_LOAD=1 python -m pytest tests/test_chunk_text_store.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement module**

```python
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
```

- [ ] **Step 4: Run, verify PASS** — `cd BE && SKIP_MODEL_LOAD=1 python -m pytest tests/test_chunk_text_store.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add BE/app/domains/vectorstore/chunk_text_store.py BE/tests/test_chunk_text_store.py
git commit -m "feat(vectorstore): chunk_text_store (sqlite + inline + video-decode fallback)"
```

---

### Task 4: store.append — ghi text vào sqlite, index.json bỏ text (giữ inline khi video lỗi)

**Files:**
- Modify: `BE/app/domains/vectorstore/store.py` (`append_to_index` meta-write loop ~519-533; `append_chunks_to_lc_index` meta-write loop ~333-345)
- Test: `BE/tests/test_store_precomputed.py` (mở rộng)

**Interfaces:**
- Consumes: `chunk_text_store.put_many`. `custom_metadata[i]` có thể chứa `video`, `frame_index`.
- Produces: `index.json` entry KHÔNG có `text` khi có video; CÓ `text` inline khi `video` rỗng/thiếu. sqlite luôn có text.

- [ ] **Step 1: Write failing test** (raw path)

```python
def test_append_writes_sqlite_and_slims_index(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    monkeypatch.delenv("USE_LC_VECTOR_STORE", raising=False)
    import app.domains.vectorstore.store as vs
    import app.domains.vectorstore.chunk_text_store as cts
    _patch_paths(vs, tmp_path); cts.reset_cache()
    import numpy as np
    vs.append_to_index(
        chunks=["alpha", "beta"], video_name="doc.mp4",
        embeddings=np.zeros((2, 8), dtype="float32"),
        custom_metadata=[{"video": "doc.mp4", "frame_index": 0}, {"video": "doc.mp4", "frame_index": 1}],
    )
    meta = json.load(open(vs.META_PATH, encoding="utf-8"))
    assert "text" not in meta["0"], "index.json không giữ text khi có video"
    assert meta["0"]["frame_index"] == 0
    assert cts.get_text(0) == "alpha", "text nằm ở sqlite"


def test_append_keeps_inline_text_when_no_video(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    monkeypatch.delenv("USE_LC_VECTOR_STORE", raising=False)
    import app.domains.vectorstore.store as vs
    import app.domains.vectorstore.chunk_text_store as cts
    _patch_paths(vs, tmp_path); cts.reset_cache()
    import numpy as np
    vs.append_to_index(chunks=["x"], video_name="", embeddings=np.zeros((1, 8), dtype="float32"),
                       custom_metadata=[{"video": "", "frame_index": None}])
    meta = json.load(open(vs.META_PATH, encoding="utf-8"))
    assert meta["0"].get("text") == "x", "video lỗi → giữ inline text (an toàn)"
```

- [ ] **Step 2: Run, verify FAIL** — `cd BE && python -m pytest tests/test_store_precomputed.py::test_append_writes_sqlite_and_slims_index -v` → FAIL (text vẫn còn).

- [ ] **Step 3: Implement** — in `append_to_index` meta loop, replace the per-entry build:

```python
    from app.domains.vectorstore import chunk_text_store
    text_items: list[tuple[int, str]] = []
    now = datetime.now().isoformat()
    for i, chunk in enumerate(chunks):
        cid = int(ids[i])
        meta_entry = {"video": video_name, "timestamp": now}
        if custom_metadata and i < len(custom_metadata):
            meta_entry.update(custom_metadata[i])
        has_video = bool(meta_entry.get("video")) and meta_entry.get("frame_index") is not None
        if has_video:
            text_items.append((cid, chunk))      # text -> sqlite
        else:
            meta_entry["text"] = chunk            # video lỗi -> giữ inline (an toàn)
        emb_vec = _optional_prefix_embedding_list(chunk)
        if emb_vec is not None:
            meta_entry["embedding"] = emb_vec
        meta[str(cid)] = meta_entry
    if text_items:
        chunk_text_store.put_many(text_items)
```

Apply the **same pattern** to `append_chunks_to_lc_index`'s meta loop (lines ~333-345): same `has_video` check, `text_items`, `chunk_text_store.put_many`, drop `"text"` when `has_video`.

- [ ] **Step 4: Run, verify PASS** — both new tests + existing `test_store_precomputed.py` PASS. (Existing tests pass `custom_metadata` without video → keep inline text → still fine.)

- [ ] **Step 5: Commit**

```bash
git add BE/app/domains/vectorstore/store.py BE/tests/test_store_precomputed.py
git commit -m "feat(vectorstore): write chunk text to sqlite, slim index.json to pointer"
```

---

### Task 5: ingest_graph — đưa video + frame_index vào custom_metadata

**Files:**
- Modify: `BE/app/graphs/ingest_graph.py` (`embed_index_node`, build of `md` per entry ~208-221)
- Test: `BE/tests/test_late_chunk_ingest.py` (mở rộng)

**Interfaces:**
- Consumes: `entry.get("frame_index")` (Task 2), `state["video_path"]`.
- Produces: `custom_metadata[i]` có `video` (= video_path) và `frame_index`.

- [ ] **Step 1: Write failing test** — extend `test_late_chunk_ingest.py` fake_append to capture `custom_metadata`, assert each has `frame_index` and `video`:

```python
    def fake_append(chunks, video_name, custom_metadata=None, batch_size=32, embeddings=None):
        captured["custom_metadata"] = custom_metadata
    # ... after invoke:
    cm = captured["custom_metadata"]
    assert all("frame_index" in m for m in cm)
    assert all(m.get("video") for m in cm)
```

(fake_process must set `frame_index` per entry — update it to include `"frame_index": len(entries)` per appended entry.)

- [ ] **Step 2: Run, verify FAIL** — `cd BE && python -m pytest tests/test_late_chunk_ingest.py -v` → FAIL (no frame_index/video in md).

- [ ] **Step 3: Implement** — in `embed_index_node`, where `md` dict is built per entry, add:

```python
                md = {
                    "parent_id": entry.get("parent_id"),
                    "sub_order": entry.get("sub_order"),
                    "total_parts": entry.get("total_parts"),
                    "is_subchunk": entry.get("is_subchunk", False),
                    "source_stem": src_stem,
                    "source_id": state.get("source_id"),
                    "video": state.get("video_path") or "",
                    "frame_index": entry.get("frame_index"),
                }
```

- [ ] **Step 4: Run, verify PASS** — same command → PASS.

- [ ] **Step 5: Commit**

```bash
git add BE/app/graphs/ingest_graph.py BE/tests/test_late_chunk_ingest.py
git commit -m "feat(ingest): carry video+frame_index pointer into chunk metadata"
```

---

### Task 6: hybrid BM25 + result text qua chunk_text_store

**Files:**
- Modify: `BE/app/domains/retrieval/hybrid.py` (`_ensure_loaded` ~105-145)
- Test: `BE/tests/test_retrieval_filter.py` hoặc mới `BE/tests/test_hybrid_textstore.py`

**Interfaces:**
- Consumes: `chunk_text_store.iter_all()`, `chunk_text_store.mtime()`.
- Produces: BM25 corpus + `self._chunks[].text` lấy từ chunk_text_store (không từ `meta["text"]`).

- [ ] **Step 1: Write failing test** (`tests/test_hybrid_textstore.py`)

```python
import json
import app.domains.vectorstore.store as store
import app.domains.vectorstore.chunk_text_store as cts


def test_bm25_corpus_from_sqlite_not_index_text(tmp_path, monkeypatch):
    store.INDEX_DIR = tmp_path
    store.META_PATH = str(tmp_path / "index.json")
    cts.reset_cache()
    # index.json: pointer, KHÔNG text
    meta = {"0": {"video": "d.mp4", "frame_index": 0, "source_stem": "d"},
            "1": {"video": "d.mp4", "frame_index": 1, "source_stem": "d"}, "__meta__": {}}
    with open(store.META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    cts.put_many([(0, "alpha lexical token"), (1, "beta other token")])

    from app.domains.retrieval.hybrid import HybridRetriever
    r = HybridRetriever(meta_path=store.META_PATH)
    r._ensure_loaded()
    assert {c.chunk_id for c in r._chunks} == {0, 1}
    assert any("alpha" in c.text for c in r._chunks), "text phải đến từ sqlite"
    assert r._bm25 is not None
```

(Confirm `HybridRetriever.__init__` accepts `meta_path`; if it derives meta_path internally, set it via the constructor arg it already uses — check `hybrid.py` ctor.)

- [ ] **Step 2: Run, verify FAIL** — `cd BE && SKIP_MODEL_LOAD=1 python -m pytest tests/test_hybrid_textstore.py -v` → FAIL (current reads `v.get("text")` → empty → no chunks).

- [ ] **Step 3: Implement** — replace `_ensure_loaded` body to source text from chunk_text_store:

```python
    def _ensure_loaded(self) -> None:
        if not self.meta_path.exists():
            self._chunks = []; self._bm25 = None; self._bm25_tokens = []
            return
        from app.domains.vectorstore import chunk_text_store
        mtime = max(self.meta_path.stat().st_mtime, chunk_text_store.mtime())
        if self._meta_mtime is not None and mtime == self._meta_mtime and self._bm25 is not None:
            return
        with open(self.meta_path, encoding="utf-8") as f:
            meta = json.load(f) or {}
        texts = dict(chunk_text_store.iter_all())   # chunk_id -> text (sqlite hoặc inline)
        chunks: list[RetrievedChunk] = []
        for k, v in meta.items():
            if not (isinstance(k, str) and k.isdigit() and isinstance(v, dict)):
                continue
            cid = int(k)
            text = (texts.get(cid) or v.get("text") or "").strip()
            if not text:
                continue
            video_stem = _norm_stem(v.get("source_stem") or v.get("video") or "")
            chunks.append(RetrievedChunk(chunk_id=cid, text=text, video_stem=video_stem,
                                         category=(v.get("category") or None),
                                         language=(v.get("language") or None)))
        chunks.sort(key=lambda c: c.chunk_id)
        self._chunks = chunks
        self._bm25_tokens = [_tokenize(c.text) for c in chunks]
        self._bm25 = BM25Okapi(self._bm25_tokens) if chunks else None
        self._meta_mtime = mtime
```

- [ ] **Step 4: Run, verify PASS** — new test + `tests/test_retrieval_filter.py` + `tests/test_query*.py` PASS.

- [ ] **Step 5: Commit**

```bash
git add BE/app/domains/retrieval/hybrid.py BE/tests/test_hybrid_textstore.py
git commit -m "feat(retrieval): BM25 corpus + result text via chunk_text_store"
```

---

### Task 7: Đổi các read-site còn lại sang chunk_text_store

**Files (modify, dùng `chunk_text_store.get_text`/`get_texts`):**
- `BE/app/domains/vectorstore/store.py` — `search_index` (~594 `meta[key]["text"]`), `rebuild_chunk_index` (~653 `v.get("text","")`), `rebuild_lc_index_from_meta` (~403 `v.get("text")`).
- `BE/app/domains/memory/tree.py` — `_join_chunk_text` (307), section/doc builders (413-414, 461-472), evidence (1156).
- `BE/services/mindmap/worker.py` — `collect_chunks_for_sources` (65), `_cluster_and_label_no_llm` (776), texts_only (1030/1113), final (1876), visual (2038).
- `BE/app/main.py` — `/sources` (1184), summary (1551-1566).
- Test: `BE/tests/test_query.py`, `tests/test_mindmap_source_match.py`, `tests/test_delete_source.py` (chạy lại) + 1 test mới cho `search_index`.

**Interfaces:**
- Consumes: `chunk_text_store.get_text(chunk_id)` / `get_texts(ids)`.
- Pattern: chỗ nào có `chunk_id`/key + đọc `text`, đổi sang `get_text(cid)`; chỗ duyệt nhiều chunk theo source → gom ids rồi `get_texts`.

- [ ] **Step 1: Write failing test** — `search_index` trả text từ sqlite:

```python
def test_search_index_text_from_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    import app.domains.vectorstore.store as vs
    import app.domains.vectorstore.chunk_text_store as cts
    vs.INDEX_DIR = tmp_path; vs.META_PATH = str(tmp_path / "index.json"); vs.INDEX_PATH = str(tmp_path / "index.faiss")
    cts.reset_cache()
    import json
    json.dump({"0": {"video": "d.mp4", "frame_index": 0}}, open(vs.META_PATH, "w", encoding="utf-8"))
    cts.put_many([(0, "hello world")])
    # search_index dưới SKIP trả [] sớm → test ở tầng materialize: gọi helper đọc text
    assert cts.get_text(0) == "hello world"
```

(For `search_index` itself: it returns `[]` under SKIP_MODEL_LOAD; the real change is replacing `meta[key]["text"]` with `chunk_text_store.get_text(int(key))`. Verify via reading the line is changed + the query e2e tests.)

- [ ] **Step 2: Run baseline** — `cd BE && python -m pytest tests/test_query.py tests/test_mindmap_source_match.py tests/test_delete_source.py -v` (note current pass set).

- [ ] **Step 3: Implement swaps.** For EACH site, replace direct text read with `chunk_text_store`:

`store.search_index` (~594):
```python
        from app.domains.vectorstore import chunk_text_store
        t = chunk_text_store.get_text(int(key))
        if t:
            results.append(t)
```
`store.rebuild_chunk_index` (~653) and `rebuild_lc_index_from_meta` (~403): replace `v.get("text")` with `chunk_text_store.get_text(int(k)) or v.get("text") or ""`.

`memory/tree.py::_join_chunk_text` (307) — it receives chunk dicts; change so it reads `chunk_text_store.get_text(c["chunk_id"])` when `c.get("text")` empty:
```python
    from app.domains.vectorstore import chunk_text_store
    t = (c.get("text") or "").strip() or (chunk_text_store.get_text(int(c["chunk_id"])) if c.get("chunk_id") is not None else "")
```
`mindmap/worker.py::collect_chunks_for_sources` (65) and other `c.get("text")`: same fallback to `chunk_text_store.get_text(c["chunk_id"])`.

`main.py` `/sources` (1184) and summary (1551-1566): where iterating meta items with key `cid`, use `chunk_text_store.get_text(int(cid))` instead of `item.get('text','')` / `m.get("text","")`.

- [ ] **Step 4: Run, verify PASS** — `cd BE && python -m pytest tests/test_query.py tests/test_mindmap_source_match.py tests/test_delete_source.py tests/test_store_precomputed.py -v` → PASS (same or better than baseline).

- [ ] **Step 5: Commit**

```bash
git add BE/app/domains/vectorstore/store.py BE/app/domains/memory/tree.py BE/services/mindmap/worker.py BE/app/main.py BE/tests/
git commit -m "refactor(text): route all chunk-text reads through chunk_text_store"
```

---

### Task 8: Recovery CLI + integ/smoke + playbook

**Files:**
- Create: `BE/app/scripts/rebuild_sqlite_from_videos.py`
- Test: `BE/tests/test_late_chunk_ingest.py` (assert index.json slim + sqlite has text via real graph)
- Modify: `.playbook/lessons-learned.md`, `.playbook/known-issues.md`

**Interfaces:**
- Consumes: `chunk_text_store.rebuild_from_videos()`.

- [ ] **Step 1: Write failing integ assertion** — in `test_late_chunk_ingest.py`, after invoke, assert:

```python
    meta = json.load(open(store.META_PATH, encoding="utf-8"))  # need real append, not fake
```
(For this, run a variant using REAL `store.append_to_index` with patched paths + `SKIP_MODEL_LOAD` off + fake encoder; assert `"text" not in meta["0"]` and `chunk_text_store.get_text(0)` non-empty. If too heavy, keep the unit coverage from Tasks 3-4 and make this a smoke script instead.)

- [ ] **Step 2: Implement recovery CLI**

```python
"""Dựng lại chunks.sqlite từ index.json pointer + decode video (recovery)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from app.domains.vectorstore import chunk_text_store

if __name__ == "__main__":
    n = chunk_text_store.rebuild_from_videos()
    print(f"[rebuild_sqlite] dựng lại {n} chunk text từ video/inline")
```

- [ ] **Step 3: Run full suite + import check**

Run: `cd BE && python -c "import app.graphs.ingest_graph; import app.graphs.query_graph" && python -m pytest -q`
Expected: imports OK; all PASS.

- [ ] **Step 4: Real smoke (manual, cần bge-m3)** — script: ingest 1 .md qua graph thật → assert index.json không có text, `chunks.sqlite` có; query trả đúng; xoá sqlite → `rebuild_sqlite_from_videos` → query vẫn đúng. Lưu ở scratchpad.

- [ ] **Step 5: Update playbook** — thêm note: video=canonical, index slim, sqlite derived; root cause (trùng text + video write-only); prevention (mọi read qua chunk_text_store; frame_index gán sau lọc; .mp4-only); regression (các test ở trên).

- [ ] **Step 6: Commit**

```bash
git add BE/app/scripts/rebuild_sqlite_from_videos.py .playbook/ BE/tests/
git commit -m "feat(recovery): rebuild chunks.sqlite from videos + playbook + smoke"
```

---

## Self-Review

**Spec coverage:** index.json slim (T4,T5) ✓; video canonical + frame_index (T1,T2) ✓; chunks.sqlite runtime (T3,T4) ✓; no video decode in normal op — text→sqlite at ingest (T4) ✓; BM25 from sqlite (T6) ✓; all read sites via access layer (T6,T7) ✓; backward compat inline (T3 fallback) ✓; 3 bug fixes (T1 decode order + .mp4-only; T2 frame_index) ✓; recovery (T3 rebuild_from_videos + T8 CLI) ✓; data-loss safety inline-when-no-video (T4) ✓; tests + import check (T8) ✓. No gaps.

**Placeholder scan:** All code steps contain real code; commands have expected output. Task 7 lists exact sites + replacement pattern (the code). Task 8 Step 1/4 are explicitly marked as smoke-if-too-heavy with the concrete alternative.

**Type consistency:** `chunk_text_store` API names (`put_many`, `get_text`, `get_texts`, `iter_all`, `mtime`, `rebuild_from_videos`, `reset_cache`, `init`) consistent across T3/T4/T6/T7/T8. `decode_video_qr -> list[tuple[int,str]]` and `decode_frame(path, frame_index) -> str|None` consistent (T1 produces, T3 consumes). `frame_index` int, set in T2, read in T4/T5. `chunk_id` int throughout.
