"""Auth Hardening Phase D — mindmap derived-artifact + chunk-text ownership."""

from __future__ import annotations

import json

import pytest


def _protect(main, monkeypatch, uid, on=True):
    monkeypatch.setattr(main, "_auth_protect_enabled", lambda: on)
    monkeypatch.setattr(main, "_current_user_id", lambda: uid)


@pytest.fixture()
def be(client):
    import app.main as main
    return main


# ---- store-level owner predicates (real sqlite) ----------------------------

def _store(monkeypatch, tmp_path):
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mm.sqlite"))
    from app.domains.mindmap import store
    return store


def _rec(i="m1", h="h" * 64):
    return {"id": i, "schema_version": 2, "title": "T", "sources": ["doc_a"],
            "content_hash": h, "created_at": "2026-07-13T00:00:00Z",
            "nodes": [{"id": "root", "parent": None, "kind": "root", "title": "T"}],
            "relations": [], "generator": {"degraded": False, "missing": []}}


def test_content_hash_no_cross_user_reuse(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_record(_rec(), user_id="A")
    h = "h" * 64
    assert store.get_by_hash(h, user_id="A", enforce_owner=True)["id"] == "m1"
    assert store.get_by_hash(h, user_id="B", enforce_owner=True) is None
    assert store.get_by_hash(h) is not None


def test_list_get_delete_owner_scoped(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_record(_rec("mA", "a" * 64), user_id="A")
    store.save_record(_rec("mB", "b" * 64), user_id="B")
    store.save_record(_rec("mL", "c" * 64))  # legacy NULL owner
    assert {r["id"] for r in store.list_records(user_id="A", enforce_owner=True)} == {"mA"}
    assert {r["id"] for r in store.list_records()} == {"mA", "mB", "mL"}
    assert store.get_record("mA", user_id="B", enforce_owner=True) is None
    assert store.get_record("mL", user_id="A", enforce_owner=True) is None
    assert store.delete_record("mA", user_id="B", enforce_owner=True) is False
    assert store.delete_record("mA", user_id="A", enforce_owner=True) is True


# ---- 401 when protected, no token ------------------------------------------

def test_mindmap_routes_401_without_token(be, client, monkeypatch):
    _protect(be, monkeypatch, None, on=True)
    assert client.post("/generate-mindmap", json={"sources": ["x"]}).status_code == 401
    assert client.get("/mindmap-status/j1").status_code == 401
    assert client.post("/mindmap-cancel/j1").status_code == 401
    assert client.get("/mindmaps").status_code == 401
    assert client.put("/mindmaps/m1", json={"nodes": []}).status_code == 401
    assert client.delete("/mindmaps/m1").status_code == 401
    assert client.get("/chunk-text/5").status_code == 401


# ---- generate: source ownership --------------------------------------------

def test_generate_foreign_source_403(be, client, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "idA": {"source_stem": "doc_a", "user_id": "userA"},
        "idB": {"source_stem": "doc_b", "user_id": "userB"},
    })
    _protect(be, monkeypatch, "userA", on=True)
    assert client.post("/generate-mindmap", json={"sources": ["doc_b"]}).status_code == 403


def test_owner_generate_cache_hit_no_cross_user(be, client, monkeypatch):
    from app.domains.mindmap import store
    monkeypatch.setattr(be, "_mindmap_input_and_hash", lambda s: ({"chunks": [1]}, "h" * 64))
    rec = _rec()
    monkeypatch.setattr(store, "get_by_hash",
                        lambda h, user_id=None, enforce_owner=False: rec if user_id == "userA" else None)
    monkeypatch.setattr(be, "_load_source_registry", lambda: {"idA": {"source_stem": "doc_a", "user_id": "userA"}})
    _protect(be, monkeypatch, "userA", on=True)
    assert client.post("/generate-mindmap", json={"sources": ["doc_a"]}).status_code == 200
    _protect(be, monkeypatch, "userB", on=True)
    monkeypatch.setattr(be, "_load_source_registry", lambda: {"idB": {"source_stem": "doc_a", "user_id": "userB"}})
    monkeypatch.setattr(be, "_start_mindmap_job", lambda *a, **k: "jid")
    assert client.post("/generate-mindmap", json={"sources": ["doc_a"]}).status_code == 202


# ---- status / cancel owner isolation ---------------------------------------

def test_status_cancel_owner_isolation(be, client, monkeypatch):
    from app.domains.jobs import jobs_store as js
    monkeypatch.setattr(js, "get_job", lambda jid: {"job_type": "mindmap", "status": "running",
                                                    "progress": 10, "current_node": "x",
                                                    "result": None, "error": None, "user_id": "userA"})
    cancelled = {}
    monkeypatch.setattr(js, "request_cancel", lambda jid: cancelled.setdefault("jid", jid))
    _protect(be, monkeypatch, "userA", on=True)
    assert client.get("/mindmap-status/mj").status_code == 200
    assert client.post("/mindmap-cancel/mj").status_code == 200 and cancelled.get("jid") == "mj"
    cancelled.clear()
    _protect(be, monkeypatch, "userB", on=True)
    assert client.get("/mindmap-status/mj").status_code == 404
    assert client.post("/mindmap-cancel/mj").status_code == 404
    assert cancelled == {}


# ---- PUT / DELETE owner isolation ------------------------------------------

def test_put_delete_owner_isolation(be, client, monkeypatch):
    from app.domains.mindmap import store
    saved = {}
    monkeypatch.setattr(store, "get_record",
                        lambda mid, user_id=None, enforce_owner=False: _rec() if user_id == "userA" else None)
    monkeypatch.setattr(store, "save_record",
                        lambda rec, user_id=None: saved.update(uid=user_id, id=rec.get("id")))
    monkeypatch.setattr(store, "delete_record",
                        lambda mid, user_id=None, enforce_owner=False: user_id == "userA")
    # B cannot read A's mindmap → PUT/DELETE 404
    _protect(be, monkeypatch, "userB", on=True)
    body = {"nodes": [{"id": "root", "parent": None, "kind": "root", "title": "T"}], "relations": []}
    assert client.put("/mindmaps/m1", json=body).status_code == 404
    assert client.delete("/mindmaps/m1").status_code == 404
    # A can, and PUT re-stamps the owner (never nulls it)
    _protect(be, monkeypatch, "userA", on=True)
    assert client.put("/mindmaps/m1", json=body).status_code == 200
    assert saved["uid"] == "userA"
    assert client.delete("/mindmaps/m1").status_code == 200


# ---- chunk-text isolation --------------------------------------------------

def test_chunk_text_owner_isolation(be, client, monkeypatch):
    from app.domains.vectorstore import chunk_text_store
    # index.json maps chunk 5 → source doc_a
    be.INDEX_META_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    be.INDEX_META_JSON_PATH.write_text(json.dumps({"5": {"source_stem": "doc_a", "video": "doc_a"}}), encoding="utf-8")
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: "nội dung" if cid == 5 else None)
    monkeypatch.setattr(be, "_load_source_registry", lambda: {"idA": {"source_stem": "doc_a", "user_id": "userA"}})
    _protect(be, monkeypatch, "userA", on=True)
    assert client.get("/chunk-text/5").get_json()["text"] == "nội dung"
    _protect(be, monkeypatch, "userB", on=True)
    assert client.get("/chunk-text/5").status_code == 404  # no exfiltration by global chunk id
