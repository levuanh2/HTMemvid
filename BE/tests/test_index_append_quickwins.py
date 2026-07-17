"""PR#6 ingest/index quick wins.

1) Append KHÔNG full-dir backup nữa (chi phí lớn nhất của ingest với index to);
   thao tác phá huỷ (remove/rebuild) GIỮ backup.
2) Prefix-embedding (embed lần 2 mỗi chunk vào index.json) mặc định TẮT,
   bật lại qua STORE_PREFIX_EMBEDDINGS=1; consumer legacy None-safe.
Đường raw-FAISS + precomputed embeddings — không cần model thật.
"""
from __future__ import annotations

import json

import numpy as np
import pytest


@pytest.fixture()
def vs(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    monkeypatch.delenv("USE_LC_VECTOR_STORE", raising=False)
    monkeypatch.delenv("STORE_PREFIX_EMBEDDINGS", raising=False)
    import app.domains.vectorstore.store as store
    import app.domains.vectorstore.chunk_text_store as cts
    store.META_PATH = str(tmp_path / "index.json")
    store.INDEX_PATH = str(tmp_path / "index.faiss")
    store.INDEX_DIR = tmp_path
    cts.reset_cache()
    return store


def _backup_dirs(tmp_path):
    return [p for p in tmp_path.parent.iterdir()
            if p.is_dir() and p.name.startswith(f"{tmp_path.name}_backup_")]


def _append(vs, n=3, dim=8, video="doc.mp4"):
    embs = np.random.RandomState(0).rand(n, dim).astype("float32")
    vs.append_to_index(
        chunks=[f"chunk {i}" for i in range(n)],
        video_name=video,
        embeddings=embs,
        custom_metadata=[{"source_stem": "doc"}] * n,
    )


# --------------------------------------------------------------- backup policy
def test_append_does_not_create_backup(vs, tmp_path):
    _append(vs)
    assert _backup_dirs(tmp_path) == []  # không copytree nào trên đường append
    # index vẫn ghi đầy đủ
    import faiss
    assert faiss.read_index(vs.INDEX_PATH).ntotal == 3
    meta = json.load(open(vs.META_PATH, encoding="utf-8"))
    assert meta["__meta__"]["num_chunks"] == 3


def test_second_append_still_no_backup_and_grows_index(vs, tmp_path):
    _append(vs, n=2)
    _append(vs, n=2)
    assert _backup_dirs(tmp_path) == []
    import faiss
    assert faiss.read_index(vs.INDEX_PATH).ntotal == 4
    meta = json.load(open(vs.META_PATH, encoding="utf-8"))
    assert meta["__meta__"]["num_chunks"] == 4  # id nối tiếp, không đè


def test_remove_chunks_still_backs_up(vs, tmp_path, monkeypatch):
    _append(vs, n=3)
    # remove path bị CI-skip gate; bản thân nó không cần model → bỏ cờ để chạy thật.
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    removed = vs.remove_chunks_from_raw_index([0])
    assert removed == 1
    assert len(_backup_dirs(tmp_path)) == 1  # thao tác phá huỷ GIỮ backup
    import faiss
    assert faiss.read_index(vs.INDEX_PATH).ntotal == 2


def test_append_atomic_meta_write_preserved(vs, tmp_path):
    # _save_meta vẫn atomic (tmp + replace): sau append không còn file .tmp treo.
    _append(vs)
    assert not (tmp_path / "index.tmp").exists()
    assert (tmp_path / "index.json").exists()


# --------------------------------------------------------------- prefix embedding
class _FakePrefixModel:
    def encode(self, texts, convert_to_numpy=True, show_progress_bar=False, **kw):
        return np.ones((len(texts), 4), dtype="float32")


def test_prefix_embedding_off_by_default(vs, monkeypatch):
    monkeypatch.setattr(vs, "get_embedding_model", lambda *a, **k: _FakePrefixModel())
    _append(vs)
    meta = json.load(open(vs.META_PATH, encoding="utf-8"))
    for k, v in meta.items():
        if k.isdigit():
            assert "embedding" not in v  # không embed lần 2, không phình index.json


def test_prefix_embedding_flag_restores_old_behavior(vs, monkeypatch):
    monkeypatch.setenv("STORE_PREFIX_EMBEDDINGS", "1")
    monkeypatch.setattr(vs, "get_embedding_model", lambda *a, **k: _FakePrefixModel())
    _append(vs)
    meta = json.load(open(vs.META_PATH, encoding="utf-8"))
    assert meta["0"]["embedding"] == [1.0, 1.0, 1.0, 1.0]


def test_legacy_consumer_none_safe_without_embedding(vs, monkeypatch):
    # Helper legacy đọc entry.get("embedding") phải chịu được meta không có field.
    _append(vs)
    meta = json.load(open(vs.META_PATH, encoding="utf-8"))
    from services.mindmap.worker import collect_chunks_for_sources
    chunks = collect_chunks_for_sources(meta, ["doc"])
    assert len(chunks) == 3
    assert all(c["embedding"] is None for c in chunks)  # fallback an toàn
    assert all(c["text"] for c in chunks)
