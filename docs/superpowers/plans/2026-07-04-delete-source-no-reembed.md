# Delete Source Without Re-embed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Khi xoá source/chunk khỏi vector index, loại đúng các vector hiện có theo id và KHÔNG re-embed toàn bộ corpus trong nhánh thành công; giữ fallback rebuild cũ để không bao giờ làm hỏng index.

**Architecture:** `store.py` hiện có 2 backend song song. Nhánh LangChain lưu `index.faiss` + `index.pkl` trong `INDEX_DIR`, load qua `FAISS.load_local(...)`, meta lưu bằng `_save_meta(...)`; nhánh legacy raw-FAISS lưu `IndexIDMap` với id chính là `chunk_id`. Thay vì `delete_* -> _save_meta -> rebuild_chunk_index(...)`, thêm API xoá theo id trên từng backend: LC map `chunk_id -> docstore_id` từ `InMemoryDocstore._dict` rồi gọi `FAISS.delete(ids=[...])`; raw-FAISS gọi `remove_ids(...)`. Chỉ khi delete-by-id lỗi mới rơi về rebuild cũ.

**Tech Stack:** Python 3.11, LangChain FAISS wrapper, raw FAISS `IndexIDMap`, docker-compose, gunicorn.

## Global Constraints

- Test BE bằng `.venv` của repo, chạy từ thư mục `BE/`: `../.venv/Scripts/python.exe -m pytest ... -v`.
- KHÔNG nâng dependency, KHÔNG đổi format index trên đĩa.
- Mọi lỗi delete-by-id phải fallback về `rebuild_chunk_index(...)`; không bao giờ để index/meta lệch nhau hoặc để index ở trạng thái hỏng.
- Nhánh thành công ở cả LC và raw-FAISS đều KHÔNG được re-embed.
- Bám đúng cấu trúc hiện có trong `BE/app/domains/vectorstore/store.py`: `INDEX_DIR`, `_save_meta`, `_use_lc_vector_store`, `FAISS.load_local(...)`, `load_vectorstore()`.
- Test model phải chạy với `SKIP_MODEL_LOAD=1` hoặc fake embeddings; không tải model thật trong unit test.
- Sau thay đổi phải chạy `cd BE && ../.venv/Scripts/python.exe -c "import app.main"` và pass.

---

### Task 1: `store.py` — thêm remove API theo id, không re-embed ở nhánh thành công

**Files:**
- Modify: `BE/app/domains/vectorstore/store.py`
- Test: `BE/tests/test_vectorstore_delete_by_id.py`

**Interfaces:**
- Produces:
  - `remove_chunks_from_lc_index(chunk_ids: list[int]) -> int`
  - `remove_chunks_from_raw_index(chunk_ids: list[int]) -> int`
- Behavior:
  - LC: map `chunk_id` sang `docstore_id` bằng cách duyệt `vs.docstore._dict.items()` và đọc `doc.metadata["chunk_id"]`.
  - Legacy raw-FAISS: `IndexIDMap.remove_ids(np.array(ids, dtype="int64"))`.
  - Nếu delete-by-id fail: raise để caller fallback rebuild.

- [ ] **Step 1: Viết test fail cho LC mapping**

```python
# BE/tests/test_vectorstore_delete_by_id.py
from langchain.docstore.document import Document

def test_remove_chunks_from_lc_index_maps_chunk_id_to_docstore_id(monkeypatch):
    import app.domains.vectorstore.store as store

    deleted_ids = []

    class _VS:
        def __init__(self):
            self.docstore = type("DS", (), {
                "_dict": {
                    "uuid-a": Document(page_content="A", metadata={"chunk_id": 10, "video": "s1"}),
                    "uuid-b": Document(page_content="B", metadata={"chunk_id": 11, "video": "s1"}),
                    "uuid-c": Document(page_content="C", metadata={"chunk_id": 12, "video": "s2"}),
                }
            })()
        def delete(self, ids):
            deleted_ids.extend(ids)
            return True
        def save_local(self, path):
            pass

    monkeypatch.setattr(store, "load_vectorstore", lambda: _VS())
    monkeypatch.setattr(store, "_backup_dir_before_write", lambda *a, **k: None)

    removed = store.remove_chunks_from_lc_index([10, 12])
    assert removed == 2
    assert deleted_ids == ["uuid-a", "uuid-c"]
```

- [ ] **Step 2: Chạy fail**

```bash
cd BE
../.venv/Scripts/python.exe -m pytest tests/test_vectorstore_delete_by_id.py::test_remove_chunks_from_lc_index_maps_chunk_id_to_docstore_id -v
```

- [ ] **Step 3: Implement trong `store.py`**

```python
def remove_chunks_from_lc_index(chunk_ids: list[int]) -> int:
    if _skip_faiss_in_ci():
        return 0
    if not chunk_ids:
        return 0

    vs = load_vectorstore()
    if vs is None:
        return 0

    wanted = {int(cid) for cid in chunk_ids}
    docstore_ids: list[str] = []
    doc_dict = getattr(vs.docstore, "_dict", {}) or {}
    for docstore_id, doc in doc_dict.items():
        md = getattr(doc, "metadata", {}) or {}
        try:
            cid = int(md.get("chunk_id"))
        except Exception:
            continue
        if cid in wanted:
            docstore_ids.append(str(docstore_id))

    if not docstore_ids:
        return 0

    ok = vs.delete(ids=docstore_ids)
    if ok is False:
        raise RuntimeError("LangChain FAISS.delete returned False")

    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    _backup_dir_before_write(INDEX_DIR, keep=keep)
    vs.save_local(str(INDEX_DIR))
    return len(docstore_ids)


def remove_chunks_from_raw_index(chunk_ids: list[int]) -> int:
    if _skip_faiss_in_ci():
        return 0
    ids = [int(cid) for cid in chunk_ids]
    if not ids or not os.path.exists(INDEX_PATH):
        return 0

    idx = faiss.read_index(INDEX_PATH)
    removed = idx.remove_ids(np.array(ids, dtype="int64"))
    keep = int(os.environ.get("FAISS_BACKUP_KEEP", "3"))
    save_index_with_backup(idx, INDEX_DIR, keep=keep)
    return int(removed)
```

Lưu ý:
- LC path dùng docstore id kiểu uuid, KHÔNG phải `chunk_id`.
- Giữ `rebuild_lc_index_from_meta(...)` và `rebuild_chunk_index(...)` nguyên vẹn làm đường fallback an toàn.

- [ ] **Step 4: Chạy pass + thêm case raw-FAISS**

```bash
cd BE
set SKIP_MODEL_LOAD=1
../.venv/Scripts/python.exe -m pytest tests/test_vectorstore_delete_by_id.py -v
```

---

### Task 2: chuyển flow xoá source sang remove API mới; rebuild chỉ còn là fallback

**Files:**
- Modify: `BE/app/domains/vectorstore/store.py`
- Test: `BE/tests/test_vectorstore_delete_source.py`

**Interfaces:**
- Changes:
  - `delete_chunks_by_source(source_id: str) -> int`
  - `delete_source_from_index(video_name: str)`
- New flow:
  1. Xác định danh sách `chunk_ids` cần xoá từ meta hiện tại.
  2. Gọi remove API đúng backend.
  3. Chỉ khi remove API ném lỗi mới `_save_meta(keep_meta)` + `rebuild_chunk_index(keep_meta)`.
  4. Nhánh thành công: cập nhật meta + `__meta__["num_chunks"]`, lưu bằng `_save_meta(...)`, không in log rebuild.

- [ ] **Step 1: Viết test e2e nhỏ cho delete-by-source không rebuild**

```python
def test_delete_chunks_by_source_lc_keeps_remaining_vectors_and_no_rebuild_log(monkeypatch, capsys, tmp_path):
    import os
    os.environ["USE_LC_VECTOR_STORE"] = "1"
    os.environ["SKIP_MODEL_LOAD"] = "1"

    import app.domains.vectorstore.store as store

    class FakeEmb:
        def embed_documents(self, texts):
            return [[float(i + 1), 0.0, 0.0] for i, _ in enumerate(texts)]
        def embed_query(self, text):
            return [1.0, 0.0, 0.0]

    monkeypatch.setattr(store, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(store, "INDEX_PATH", str(tmp_path / "index.faiss"))
    monkeypatch.setattr(store, "get_embeddings", lambda: FakeEmb())

    chunks = ["alpha s1", "beta s1", "gamma s2", "delta s2"]
    metas = [
        {"source_id": "s1", "video": "s1"},
        {"source_id": "s1", "video": "s1"},
        {"source_id": "s2", "video": "s2"},
        {"source_id": "s2", "video": "s2"},
    ]
    store.append_chunks_to_lc_index(chunks, custom_metadata=metas)

    deleted = store.delete_chunks_by_source("s1")
    assert deleted == 2

    meta = store._load_meta()
    data_ids = sorted(int(k) for k in meta.keys() if k.isdigit())
    assert len(data_ids) == 2

    vs = store.load_vectorstore()
    assert vs is not None
    assert len(getattr(vs.docstore, "_dict", {})) == 2

    out = capsys.readouterr().out
    assert "rebuilt LC FAISS" not in out

    hits = store.similarity_search_lc("gamma", k=2)
    assert any("gamma s2" in h for h in hits)
```

- [ ] **Step 2: Chạy fail**

```bash
cd BE
../.venv/Scripts/python.exe -m pytest tests/test_vectorstore_delete_source.py::test_delete_chunks_by_source_lc_keeps_remaining_vectors_and_no_rebuild_log -v
```

- [ ] **Step 3: Implement**

```python
def delete_source_from_index(video_name: str):
    meta = _load_meta()
    chunk_ids = [int(k) for k, v in meta.items()
                 if isinstance(k, str) and k.isdigit() and v.get("video") == video_name]
    keep_meta = {k: v for k, v in meta.items() if not (isinstance(k, str) and k.isdigit() and v.get("video") == video_name)}
    try:
        if _use_lc_vector_store():
            remove_chunks_from_lc_index(chunk_ids)
        else:
            remove_chunks_from_raw_index(chunk_ids)
        _save_meta(keep_meta)
    except Exception:
        _save_meta(keep_meta)
        rebuild_chunk_index(keep_meta)


def delete_chunks_by_source(source_id: str) -> int:
    # giữ normalize như code hiện tại, chỉ đổi backend action
    ...
```

Chi tiết cần giữ khớp code thật:
- Preserve `_normalize_source_id(...)` hiện tại trong `delete_chunks_by_source`.
- Preserve `__meta__` bookkeeping sau khi xoá thành công.
- Fallback except phải dùng đúng đường cũ `rebuild_chunk_index(keep_meta)`.

- [ ] **Step 4: Chạy pass**

```bash
cd BE
set SKIP_MODEL_LOAD=1
../.venv/Scripts/python.exe -m pytest tests/test_vectorstore_delete_by_id.py tests/test_vectorstore_delete_source.py -v
../.venv/Scripts/python.exe -c "import app.main"
```

---

### Task 3: hardening vận hành — tăng backend workers và ghi doc env

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docs/ARCHITECTURE.md`

**Interfaces:**
- `docker-compose.yml` backend `environment` thêm `WEB_CONCURRENCY: "2"`.
- `docs/ARCHITECTURE.md` phần `Environment Configuration` thêm 1 dòng: `WEB_CONCURRENCY=2` để backend còn trả `/health` khi 1 worker đang xử lý request nặng.

- [ ] **Step 1: Sửa cấu hình**

```yaml
backend:
  environment:
    PORT: "8080"
    WEB_CONCURRENCY: "2"
```

```bash
# docs/ARCHITECTURE.md
WEB_CONCURRENCY=2              # gunicorn workers cho backend; giữ /health sống khi 1 worker bận
```

- [ ] **Step 2: Smoke thủ công trong Docker**

```bash
docker compose up -d --build backend
# bắt đầu xoá 1 source lớn từ UI hoặc API
curl -fsS http://localhost:8080/health
curl -fsS http://localhost:8080/list-indexed
```

Expected:
- `/health` vẫn trả lời trong lúc request xoá đang chạy.
- Nếu delete-by-id thành công thì thời gian xoá không còn tỷ lệ với toàn bộ số chunk còn lại.

---

### Task 4: cập nhật `.playbook` sau khi fix xong + regression note

**Files:**
- Modify: `.playbook/known-issues.md`
- Modify: `.playbook/lessons-learned.md`

**Interfaces:**
- `known-issues`: annotate mục `Xoá nguồn khi index lớn...` là đã fix, nêu ngày fix và fallback behavior.
- `lessons-learned`: thêm regression note ngắn về bẫy LC docstore id khác `chunk_id`, và vì sao phải giữ rebuild fallback.

- [ ] **Step 1: Update docs**

Nội dung tối thiểu cần chốt:
- LC `FAISS.delete(ids=...)` nhận docstore ids, không nhận `chunk_id`.
- Legacy raw-FAISS giữ id = `chunk_id`, nên delete path hai backend khác nhau.
- Mọi lỗi delete-by-id phải rebuild để ưu tiên tính toàn vẹn index hơn hiệu năng.

- [ ] **Step 2: Verify cuối**

```bash
cd BE
set SKIP_MODEL_LOAD=1
../.venv/Scripts/python.exe -m pytest tests/test_vectorstore_delete_by_id.py tests/test_vectorstore_delete_source.py -v
../.venv/Scripts/python.exe -c "import app.main"
```

---

## Rollout order

Task 1 → Task 2 → Task 3 → Task 4

Task 3 có thể mở PR chung với Task 2, nhưng không được coi là fix cốt lõi nếu chưa có test Task 2.
