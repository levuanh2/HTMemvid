import json

from app.domains.mindmap import store


def _rec(i="m1"):
    return {
        "id": i,
        "schema_version": 2,
        "title": "Gốc",
        "sources": ["a_docx"],
        "content_hash": "h" * 64,
        "created_at": "2026-07-04T00:00:00Z",
        "nodes": [
            {"id": "n0", "parent": None, "kind": "root", "title": "Gốc", "note": "", "chunk_refs": [], "order": 0},
            {"id": "n1", "parent": "n0", "kind": "section", "title": "A", "note": "x", "chunk_refs": ["1"], "order": 0},
        ],
        "relations": [],
        "generator": {"pipeline": "skeleton_v1", "degraded": False, "missing": []},
    }


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mm.sqlite"))
    from app import main as be_main

    return be_main.app.test_client()


def test_get_record_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mm.sqlite"))
    store.save_record(_rec())
    assert store.get_record("m1")["title"] == "Gốc"
    assert store.get_record("khong_co") is None


def test_put_updates_and_protects_fields(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    store.save_record(_rec())
    body = _rec()
    body["id"] = "HACK"
    body["content_hash"] = "x" * 64
    body["sources"] = ["khac"]
    body["title"] = "Đã sửa"
    body["nodes"].append({"id": "n2", "parent": "n1", "kind": "idea", "title": "Ý mới", "note": "", "chunk_refs": [], "order": 0})
    body["relations"] = [
        {"source": "n1", "target": "n2", "type": "leads_to", "label": "dẫn"},
        {"source": "n2", "target": "XX", "type": "relates_to", "label": ""},
    ]
    r = client.put("/mindmaps/m1", data=json.dumps(body), content_type="application/json")
    assert r.status_code == 200
    saved = store.get_record("m1")
    assert saved["title"] == "Đã sửa"
    assert saved["content_hash"] == "h" * 64
    assert saved["sources"] == ["a_docx"]
    assert saved["created_at"] == "2026-07-04T00:00:00Z"
    assert any(n["id"] == "n2" for n in saved["nodes"])
    assert saved["relations"] == []
    assert saved["generator"]["edited"] is True
    assert saved["updated_at"].endswith("Z")


def test_put_404_unknown_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.put("/mindmaps/khong_co", data=json.dumps(_rec()), content_type="application/json")
    assert r.status_code == 404


def test_put_400_empty_nodes(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    store.save_record(_rec())
    r = client.put("/mindmaps/m1", data=json.dumps({"nodes": []}), content_type="application/json")
    assert r.status_code == 400
