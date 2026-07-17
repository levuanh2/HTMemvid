"""PR#7 — retrieval hot-path quick wins.

1) Legacy FAISS index cache theo (mtime_ns, size): query lặp không read_index lại;
   file đổi → reload (append/delete/rebuild đều ghi lại file).
2) LC vectorstore cache (store.load_vectorstore(use_cache=True)) cùng nguyên tắc;
   writer mặc định use_cache=False luôn load fresh.
3) _by_id thay next()-scan: kết quả retrieval y hệt, id lạ không crash.
Không model thật: encode_query_cached bị monkeypatch, faiss index nhỏ tự dựng.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
import pytest

import app.domains.retrieval.hybrid as hy
from app.domains.retrieval.hybrid import HybridRetriever


def _write_meta(tmp_path, n=3, stem="doc"):
    meta = {
        str(i): {"text": f"chunk {i} noi dung", "video": f"{stem}.mp4", "source_stem": stem}
        for i in range(n)
    }
    meta["__meta__"] = {"version": "1.1", "num_chunks": n}
    p = tmp_path / "index.json"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


def _write_faiss(index_path, n=3, dim=4, seed=0):
    import faiss
    rs = np.random.RandomState(seed)
    base = faiss.IndexFlatL2(dim)
    idx = faiss.IndexIDMap(base)
    idx.add_with_ids(rs.rand(n, dim).astype("float32"), np.arange(n, dtype="int64"))
    faiss.write_index(idx, str(index_path))


@pytest.fixture()
def r(tmp_path, monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    monkeypatch.delenv("USE_LC_VECTOR_STORE", raising=False)  # đường legacy raw
    meta_path = _write_meta(tmp_path)
    _write_faiss(tmp_path / "index.faiss")
    # query vector cố định — không cần model
    monkeypatch.setattr(hy, "encode_query_cached",
                        lambda q, m: np.zeros((1, 4), dtype="float32"))
    import app.domains.vectorstore.chunk_text_store as cts
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cts.reset_cache()
    return HybridRetriever(index_path=tmp_path / "index.faiss", meta_path=meta_path)


def _count_read_index(monkeypatch, counter):
    import faiss
    real = faiss.read_index

    def counting(path, *a, **kw):
        counter.append(path)
        return real(path, *a, **kw)

    monkeypatch.setattr(hy.faiss, "read_index", counting)


# --------------------------------------------------------------- faiss cache
def test_repeat_query_reads_index_once_same_results(r, monkeypatch):
    calls = []
    _count_read_index(monkeypatch, calls)
    a = [c.chunk_id for c in r.retrieve_faiss_only("noi dung", top_k=3)]
    b = [c.chunk_id for c in r.retrieve_faiss_only("noi dung", top_k=3)]
    c = [c.chunk_id for c in r.retrieve_faiss_only("noi dung", top_k=3)]
    assert a and a == b == c  # equivalence: cache không đổi kết quả
    assert len(calls) == 1    # đọc đĩa đúng MỘT lần


def test_index_file_change_triggers_reload(r, tmp_path, monkeypatch):
    calls = []
    _count_read_index(monkeypatch, calls)
    r.retrieve_faiss_only("noi dung", top_k=3)
    assert len(calls) == 1
    # "append": ghi index mới nhiều vector hơn + meta mới, ép mtime đổi
    _write_faiss(tmp_path / "index.faiss", n=4, seed=1)
    _write_meta(tmp_path, n=4)
    future = time.time() + 5
    os.utime(tmp_path / "index.faiss", (future, future))
    os.utime(tmp_path / "index.json", (future, future))
    out = r.retrieve_faiss_only("noi dung", top_k=4)
    assert len(calls) == 2  # file đổi → reload
    assert 3 in [c.chunk_id for c in out]  # chunk mới nhìn thấy được


def test_source_filtered_results_unchanged_and_dict_path(r):
    ids_all = [c.chunk_id for c in r.retrieve_faiss_only("noi dung", top_k=3)]
    ids_filtered = [c.chunk_id for c in r.retrieve_faiss_only(
        "noi dung", selected_sources=["doc"], top_k=3)]
    assert sorted(ids_filtered) == sorted(ids_all)  # cùng nguồn → cùng tập
    assert [c.chunk_id for c in r.retrieve_faiss_only(
        "noi dung", selected_sources=["khac"], top_k=3)] == []  # nguồn lạ → rỗng


def test_faiss_id_missing_from_meta_is_safe(r, tmp_path):
    # index có id 99 không tồn tại trong meta → bị bỏ, không crash.
    import faiss
    idx = faiss.read_index(str(tmp_path / "index.faiss"))
    idx.add_with_ids(np.zeros((1, 4), dtype="float32"), np.array([99], dtype="int64"))
    faiss.write_index(idx, str(tmp_path / "index.faiss"))
    future = time.time() + 5
    os.utime(tmp_path / "index.faiss", (future, future))
    out = r.retrieve_faiss_only("noi dung", top_k=10)
    assert 99 not in [c.chunk_id for c in out]
    out_f = r.retrieve_faiss_only("noi dung", selected_sources=["doc"], top_k=10)
    assert 99 not in [c.chunk_id for c in out_f]


def test_by_id_built_with_chunks(r):
    r._ensure_loaded()
    assert set(r._by_id.keys()) == {0, 1, 2}
    assert r._by_id[1] is r._chunks[1] or r._by_id[1].chunk_id == 1


# --------------------------------------------------------------- LC vs cache
@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    import app.domains.vectorstore.store as store
    store.INDEX_DIR = tmp_path
    store.META_PATH = str(tmp_path / "index.json")
    store.INDEX_PATH = str(tmp_path / "index.faiss")
    store._VS_CACHE["key"] = None
    store._VS_CACHE["vs"] = None
    (tmp_path / "index.faiss").write_bytes(b"x" * 10)
    (tmp_path / "index.pkl").write_bytes(b"y" * 10)
    loads = []

    class _FakeVS:
        pass

    monkeypatch.setattr(store.FAISS, "load_local",
                        classmethod(lambda cls, *a, **kw: loads.append(1) or _FakeVS()))
    monkeypatch.setattr(store, "get_embeddings", lambda: object())
    return store, loads


def test_load_vectorstore_cached_reuses_instance(store_env):
    store, loads = store_env
    v1 = store.load_vectorstore(use_cache=True)
    v2 = store.load_vectorstore(use_cache=True)
    assert v1 is v2
    assert len(loads) == 1


def test_load_vectorstore_reloads_on_file_change(store_env, tmp_path):
    store, loads = store_env
    v1 = store.load_vectorstore(use_cache=True)
    (tmp_path / "index.faiss").write_bytes(b"x" * 20)  # size đổi (mtime có thể trùng)
    v2 = store.load_vectorstore(use_cache=True)
    assert len(loads) == 2  # freshness key đổi → load lại
    assert v1 is not v2


def test_load_vectorstore_default_is_fresh_for_writers(store_env):
    store, loads = store_env
    store.load_vectorstore()  # writer path: không cache
    store.load_vectorstore()
    assert len(loads) == 2  # mỗi lần load fresh — không mutate bản cache của query
