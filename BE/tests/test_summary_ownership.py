"""Auth Hardening Phase D — summary derived-artifact ownership.

Route tests use the shared `client` fixture and monkeypatch main._auth_protect_enabled /
_current_user_id to simulate the flag and users. Store tests exercise a real sqlite db
(SUMMARIES_DB_PATH → tmp) to prove the owner predicates and no cross-user content_hash reuse.
"""

from __future__ import annotations

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
    monkeypatch.setenv("SUMMARIES_DB_PATH", str(tmp_path / "sum.sqlite"))
    from app.domains.summary import store
    return store


def _rec(i="s1", h="h" * 64):
    return {"id": i, "schema_version": 2, "title": "T", "sources": ["doc_a"],
            "content_hash": h, "created_at": "2026-07-13T00:00:00Z",
            "length_mode": "medium", "overview": "ov", "sections": [], "entities": [],
            "generator": {"degraded": False, "missing": []}}


def test_content_hash_no_cross_user_reuse(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_record(_rec(), user_id="A")
    h = "h" * 64
    assert store.get_by_hash(h, user_id="A", enforce_owner=True)["id"] == "s1"
    assert store.get_by_hash(h, user_id="B", enforce_owner=True) is None   # no reuse
    assert store.get_by_hash(h) is not None                               # flag off → global


def test_list_get_delete_owner_scoped(monkeypatch, tmp_path):
    store = _store(monkeypatch, tmp_path)
    store.save_record(_rec("sA", "a" * 64), user_id="A")
    store.save_record(_rec("sB", "b" * 64), user_id="B")
    store.save_record(_rec("sL", "c" * 64))  # legacy NULL owner
    assert {r["id"] for r in store.list_records(user_id="A", enforce_owner=True)} == {"sA"}
    assert {r["id"] for r in store.list_records()} == {"sA", "sB", "sL"}  # flag off → all
    assert store.get_record("sA", user_id="A", enforce_owner=True)["id"] == "sA"
    assert store.get_record("sA", user_id="B", enforce_owner=True) is None
    assert store.get_record("sL", user_id="A", enforce_owner=True) is None  # legacy hidden
    assert store.delete_record("sA", user_id="B", enforce_owner=True) is False  # foreign no-op
    assert store.delete_record("sA", user_id="A", enforce_owner=True) is True


# ---- 401 when protected, no token ------------------------------------------

def test_summary_routes_401_without_token(be, client, monkeypatch):
    _protect(be, monkeypatch, None, on=True)
    assert client.post("/generate-summary", json={"sources": ["x"]}).status_code == 401
    assert client.get("/summary-status/j1").status_code == 401
    assert client.post("/summary-cancel/j1").status_code == 401
    assert client.get("/summaries").status_code == 401
    assert client.delete("/summaries/s1").status_code == 401


# ---- generate: source ownership --------------------------------------------

def test_generate_foreign_source_403(be, client, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "idA": {"source_stem": "doc_a", "user_id": "userA"},
        "idB": {"source_stem": "doc_b", "user_id": "userB"},
    })
    _protect(be, monkeypatch, "userA", on=True)
    r = client.post("/generate-summary", json={"sources": ["doc_b"]})
    assert r.status_code == 403


def test_owner_generate_cache_hit(be, client, monkeypatch):
    from app.domains.summary import store
    monkeypatch.setattr(be, "_load_source_registry", lambda: {"idA": {"source_stem": "doc_a", "user_id": "userA"}})
    monkeypatch.setattr(be, "_summary_input_and_hash", lambda s, m: ({"chunks": [1]}, "h" * 64))
    rec = _rec()
    monkeypatch.setattr(store, "get_by_hash",
                        lambda h, user_id=None, enforce_owner=False: rec if user_id == "userA" else None)
    _protect(be, monkeypatch, "userA", on=True)
    r = client.post("/generate-summary", json={"sources": ["doc_a"]})
    assert r.status_code == 200 and r.get_json()["cached"] is True
    # a different owner does NOT hit A's cached record (would start a job → 202)
    _protect(be, monkeypatch, "userB", on=True)
    monkeypatch.setattr(be, "_load_source_registry", lambda: {"idB": {"source_stem": "doc_a", "user_id": "userB"}})
    monkeypatch.setattr(be, "_start_summary_job", lambda *a: "jid")
    r = client.post("/generate-summary", json={"sources": ["doc_a"]})
    assert r.status_code == 202  # no cross-user reuse → job started


# ---- status / cancel owner isolation ---------------------------------------

def test_status_cancel_owner_isolation(be, client, monkeypatch):
    from app.domains.jobs import jobs_store as js
    monkeypatch.setattr(js, "get_job", lambda jid: {"job_type": "summary", "status": "running",
                                                    "progress": 10, "current_node": "x",
                                                    "result": None, "error": None, "user_id": "userA"})
    cancelled = {}
    monkeypatch.setattr(js, "request_cancel", lambda jid: cancelled.setdefault("jid", jid))
    _protect(be, monkeypatch, "userA", on=True)
    assert client.get("/summary-status/sj").status_code == 200
    assert client.post("/summary-cancel/sj").status_code == 200 and cancelled.get("jid") == "sj"
    # B sees a foreign job as not-found and cannot cancel it
    cancelled.clear()
    _protect(be, monkeypatch, "userB", on=True)
    assert client.get("/summary-status/sj").status_code == 404
    assert client.post("/summary-cancel/sj").status_code == 404
    assert cancelled == {}  # cancel never reached the store


# ---- list / delete owner scoping (route passes owner params) ---------------

def test_list_delete_pass_owner_params(be, client, monkeypatch):
    from app.domains.summary import store
    seen = {}
    monkeypatch.setattr(store, "list_records",
                        lambda user_id=None, enforce_owner=False: seen.update(list=(user_id, enforce_owner)) or [])
    monkeypatch.setattr(store, "delete_record",
                        lambda sid, user_id=None, enforce_owner=False: seen.update(delete=(sid, user_id, enforce_owner)) or True)
    _protect(be, monkeypatch, "userA", on=True)
    client.get("/summaries")
    client.delete("/summaries/s1")
    assert seen["list"] == ("userA", True)
    assert seen["delete"] == ("s1", "userA", True)
