# BE/tests/test_summary_store.py — sqlite store + migrate legacy summaries.json
import json

import pytest

from app.domains.summary import store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMMARIES_DB_PATH", str(tmp_path / "summaries.sqlite"))
    yield


def _rec(rid="r1", h="h" * 64, sources=("a_docx",)):
    return {"id": rid, "schema_version": 2, "title": "T", "sources": list(sources),
            "content_hash": h, "created_at": "2026-07-06T00:00:00Z",
            "length_mode": "medium", "overview": "ov", "sections": [], "entities": [],
            "generator": {"degraded": False, "missing": []}}


def test_save_get_by_hash_roundtrip():
    store.save_record(_rec())
    got = store.get_by_hash("h" * 64)
    assert got and got["id"] == "r1"
    assert store.get_by_hash("x" * 64) is None
    assert store.get_by_hash("") is None


def test_list_get_delete():
    store.save_record(_rec("r1"))
    store.save_record(_rec("r2", h="g" * 64))
    assert {r["id"] for r in store.list_records()} == {"r1", "r2"}
    assert store.get_record("r1")["id"] == "r1"
    assert store.delete_record("r1") is True
    assert store.delete_record("r1") is False
    assert store.get_record("r1") is None


def test_delete_by_source_canonical_match():
    store.save_record(_rec("r1", sources=["a_docx"]))
    store.save_record(_rec("r2", h="g" * 64, sources=["b_docx"]))
    assert store.delete_by_source("a_docx") == 1
    assert {r["id"] for r in store.list_records()} == {"r2"}


def test_migrate_from_json_legacy_shape_and_rename(tmp_path):
    legacy = [{"id": "old1", "title": "Cũ",
               "data": {"summary": "## Tóm tắt cũ", "base_summary": "gốc"},
               "sources": ["a_docx"], "createdAt": "2026-07-01T00:00:00Z"}]
    p = tmp_path / "summaries.json"
    p.write_text(json.dumps(legacy), encoding="utf-8")

    assert store.migrate_from_json(p) == 1
    rec = store.get_record("old1")
    assert rec["schema_version"] == 1
    assert rec["summary_md"] == "## Tóm tắt cũ"
    assert rec["content_hash"] == ""
    assert rec["created_at"] == "2026-07-01T00:00:00Z"
    # rename chặn hồi sinh sau khi user xóa record khỏi sqlite
    assert not p.exists()
    assert (tmp_path / "summaries.json.migrated").exists()
    # chạy lại không nhân đôi
    assert store.migrate_from_json(p) == 0


def test_migrate_ignores_garbage(tmp_path):
    p = tmp_path / "summaries.json"
    p.write_text("not json", encoding="utf-8")
    assert store.migrate_from_json(p) == 0
    assert store.migrate_from_json(tmp_path / "missing.json") == 0
