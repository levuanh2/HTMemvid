# BE/tests/test_mindmap_routes.py
import json
import pytest

def test_generate_cache_hit_returns_done(client, monkeypatch):
    import app.main as be_main
    from app.domains.mindmap import store
    rec = {"id": "r1", "schema_version": 2, "title": "T", "sources": ["a_docx"],
           "content_hash": "h" * 64, "created_at": "2026-07-03T00:00:00Z",
           "nodes": [], "relations": [], "generator": {"degraded": False, "missing": []}}
    monkeypatch.setattr(be_main, "_mindmap_input_and_hash", lambda sources: ({"chunks": [1]}, "h" * 64))
    monkeypatch.setattr(store, "get_by_hash", lambda h: rec if h == "h" * 64 else None)
    r = client.post("/generate-mindmap", json={"sources": ["a_docx"]})
    assert r.status_code == 200
    assert r.get_json()["status"] == "done"
    assert r.get_json()["result"]["id"] == "r1"

def test_generate_force_bypasses_cache(client, monkeypatch):
    import app.main as be_main
    from app.domains.mindmap import store
    monkeypatch.setattr(be_main, "_mindmap_input_and_hash", lambda sources: ({"chunks": [1]}, "h" * 64))
    monkeypatch.setattr(store, "get_by_hash", lambda h: {"id": "r1"})
    started = {}
    monkeypatch.setattr(be_main, "_start_mindmap_job", lambda *a, **k: started.setdefault("yes", True) or "jid")
    r = client.post("/generate-mindmap", json={"sources": ["a_docx"], "force": True})
    assert r.status_code == 202 and started.get("yes")

def test_cancel_endpoint_sets_flag(client, monkeypatch):
    from app.domains.jobs import jobs_store as js
    called = {}
    monkeypatch.setattr(js, "request_cancel", lambda jid: called.setdefault("jid", jid))
    r = client.post("/mindmap-cancel/abc")
    assert r.status_code == 200 and called["jid"] == "abc"

def test_chunk_text_endpoint(client, monkeypatch):
    from app.domains.vectorstore import chunk_text_store
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: "nội dung" if cid == 7 else None)
    assert client.get("/chunk-text/7").get_json()["text"] == "nội dung"
    assert client.get("/chunk-text/999").status_code == 404

def test_list_and_delete_use_store(client, monkeypatch):
    from app.domains.mindmap import store
    monkeypatch.setattr(store, "list_records", lambda: [{"id": "x"}])
    monkeypatch.setattr(store, "delete_record", lambda mid: mid == "x")
    assert client.get("/mindmaps").get_json()["mindmaps"] == [{"id": "x"}]
    assert client.delete("/mindmaps/x").status_code == 200
    assert client.delete("/mindmaps/nope").status_code == 404
