import json
from app.domains.mindmap import store


def _rec(i="id1", h="h" * 64, sources=("a_docx",)):
    return {"id": i, "schema_version": 2, "title": "T", "sources": list(sources),
            "content_hash": h, "created_at": "2026-07-03T00:00:00Z",
            "nodes": [{"id": "n0", "parent": None, "kind": "root", "title": "T"}],
            "relations": [], "generator": {"pipeline": "skeleton_v1", "degraded": False, "missing": []}}


def _use_tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mm.sqlite"))


def test_save_get_by_hash_roundtrip(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    store.save_record(_rec())
    got = store.get_by_hash("h" * 64)
    assert got and got["id"] == "id1" and got["nodes"][0]["kind"] == "root"
    assert store.get_by_hash("x" * 64) is None


def test_list_newest_first_and_delete(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    store.save_record(_rec("a", created := "h1" * 32) | {"created_at": "2026-01-01T00:00:00Z"})
    store.save_record(_rec("b", "h2" * 32) | {"created_at": "2026-02-01T00:00:00Z"})
    ids = [r["id"] for r in store.list_records()]
    assert ids == ["b", "a"]
    assert store.delete_record("a") is True
    assert store.delete_record("a") is False
    assert [r["id"] for r in store.list_records()] == ["b"]


def test_delete_by_source_canonical(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    store.save_record(_rec("a", "h1" * 32, sources=["bao cao_docx"]))
    store.save_record(_rec("b", "h2" * 32, sources=["khac_docx"]))
    n = store.delete_by_source("bao_cao_docx")  # canonical: space → _
    assert n == 1
    assert [r["id"] for r in store.list_records()] == ["b"]


def test_migrate_from_json_idempotent(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    legacy = [{"id": "old1", "title": "L", "nodes": [], "sources": ["s"], "createdAt": "2025-04-29T00:00:00"}]
    p = tmp_path / "mindmaps.json"
    p.write_text(json.dumps(legacy), encoding="utf-8")
    assert store.migrate_from_json(p) == 1
    assert store.migrate_from_json(p) == 0  # idempotent
    rec = store.list_records()[0]
    assert rec["schema_version"] == 1 and rec["id"] == "old1"


def test_migrate_renames_json_to_prevent_resurrection(tmp_path, monkeypatch):
    """Bug 2026-07-04: record đã xoá bị 'hồi sinh' vì mỗi restart migrate re-import
    từ mindmaps.json backup. Fix: migrate xong rename file → .migrated."""
    _use_tmp_db(tmp_path, monkeypatch)
    legacy = [{"id": "old2", "title": "L", "nodes": [], "sources": ["s"], "createdAt": "2025-04-29T00:00:00"}]
    p = tmp_path / "mindmaps.json"
    p.write_text(json.dumps(legacy), encoding="utf-8")
    assert store.migrate_from_json(p) == 1
    assert not p.exists()                                  # file gốc đã rename
    assert (tmp_path / "mindmaps.json.migrated").exists()  # backup còn nguyên
    # Mô phỏng: user xoá record → restart (migrate chạy lại) → KHÔNG hồi sinh
    assert store.delete_record("old2") is True
    assert store.migrate_from_json(p) == 0                 # file không còn → no-op
    assert store.list_records() == []
