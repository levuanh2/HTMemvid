"""Auth Hardening Phase C — source route ownership (upload/list/status/delete/videos).

Uses the shared `client` fixture; monkeypatches main._auth_protect_enabled /
_current_user_id to simulate the flag and users. list-indexed data tests write a
tiny index.json + registry under the fixture's tmp DATA_DIR.
"""

from __future__ import annotations

import io
import json

import pytest


def _protect(main, monkeypatch, uid, on=True):
    monkeypatch.setattr(main, "_auth_protect_enabled", lambda: on)
    monkeypatch.setattr(main, "_current_user_id", lambda: uid)


@pytest.fixture()
def be(client):
    import app.main as main
    return main


# ---- helpers (unit) --------------------------------------------------------

def test_owned_stems_and_source_owner_ok(be, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "idA": {"source_stem": "doc_a", "filename": "a.md", "user_id": "A"},
        "idB": {"source_stem": "doc_b", "filename": "b.md", "user_id": "B"},
        "idL": {"source_stem": "doc_l", "filename": "l.md"},  # legacy, no owner
    })
    monkeypatch.setattr(be, "_auth_protect_enabled", lambda: True)
    assert be.owned_stems("A") == {"doc_a"}
    assert be._source_owner_ok("doc_a", "A") is True
    assert be._source_owner_ok("doc_a", "B") is False
    assert be._source_owner_ok("idB", "B") is True     # match by source_id key
    assert be._source_owner_ok("doc_l", "A") is False  # legacy owned by nobody


def test_resolve_owned_query_sources(be, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "idA": {"source_stem": "doc_a", "user_id": "A"},
        "idA2": {"source_stem": "doc_a2", "user_id": "A"},
        "idB": {"source_stem": "doc_b", "user_id": "B"},
    })
    monkeypatch.setattr(be, "_auth_protect_enabled", lambda: True)
    with be.app.app_context():  # the 403 branch builds a jsonify response
        # empty → all owned
        resolved, err = be._resolve_owned_query_sources([], "A")
        assert err is None and set(resolved) == {"doc_a", "doc_a2"}
        # subset owned → ok
        resolved, err = be._resolve_owned_query_sources(["doc_a"], "A")
        assert err is None and resolved == ["doc_a"]
        # foreign → 403
        resolved, err = be._resolve_owned_query_sources(["doc_b"], "A")
        assert resolved is None and err[1] == 403
        # no owned → sentinel (retrieval returns [], never global)
        resolved, err = be._resolve_owned_query_sources([], "C")
        assert err is None and resolved == be._NO_OWNED_SOURCES

        # flag off → passthrough (today's behavior)
        monkeypatch.setattr(be, "_auth_protect_enabled", lambda: False)
        resolved, err = be._resolve_owned_query_sources([], "A")
        assert err is None and resolved == []


# ---- 401 when protected, no token ------------------------------------------

def test_source_routes_401_without_token(be, client, monkeypatch):
    _protect(be, monkeypatch, None, on=True)  # flag on, no user
    assert client.post("/upload-file", data={"file": (io.BytesIO(b"x"), "a.md")},
                       content_type="multipart/form-data").status_code == 401
    assert client.get("/list-indexed").status_code == 401
    assert client.get("/sources/xyz/status").status_code == 401
    assert client.post("/delete-source", json={"video": "x"}).status_code == 401
    assert client.delete("/sources/xyz").status_code == 401
    assert client.get("/videos/x.mp4").status_code == 401


# ---- upload stamps owner ----------------------------------------------------

def test_upload_stamps_current_user(be, client, monkeypatch):
    _protect(be, monkeypatch, "userA", on=True)
    r = client.post("/upload-file", data={"file": (io.BytesIO(b"# doc\nhi"), "own.md")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    reg = be._load_source_registry()
    assert any(row.get("user_id") == "userA" for row in reg.values())


# ---- list-indexed owner filter ---------------------------------------------

def _seed_index_and_registry(be, rows):
    """rows: {source_id: {source_stem, user_id}}. Writes index.json chunks + registry."""
    meta = {}
    for i, (sid, row) in enumerate(rows.items()):
        meta[str(i)] = {"video": row["source_stem"], "source_stem": row["source_stem"], "text": "chunk"}
    be.INDEX_META_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    be.INDEX_META_JSON_PATH.write_text(json.dumps(meta), encoding="utf-8")
    be.SOURCE_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    be.SOURCE_REGISTRY_PATH.write_text(json.dumps(
        {sid: {"filename": row["source_stem"] + ".md", **row} for sid, row in rows.items()}
    ), encoding="utf-8")


def test_list_indexed_owner_scoped(be, client, monkeypatch):
    _seed_index_and_registry(be, {
        "idA": {"source_stem": "doc_a", "user_id": "userA"},
        "idB": {"source_stem": "doc_b", "user_id": "userB"},
        "idL": {"source_stem": "doc_l"},  # legacy
    })
    # A sees only doc_a
    _protect(be, monkeypatch, "userA", on=True)
    stems = {s["video_stem"] for s in client.get("/list-indexed").get_json()["sources"]}
    assert stems == {"doc_a"}
    # B sees only doc_b
    _protect(be, monkeypatch, "userB", on=True)
    stems = {s["video_stem"] for s in client.get("/list-indexed").get_json()["sources"]}
    assert stems == {"doc_b"}
    # flag off → all visible (open)
    _protect(be, monkeypatch, None, on=False)
    stems = {s["video_stem"] for s in client.get("/list-indexed").get_json()["sources"]}
    assert stems == {"doc_a", "doc_b", "doc_l"}


# ---- foreign source status/delete/video → 404 ------------------------------

def test_foreign_source_status_delete_video_404(be, client, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "idA": {"source_stem": "doc_a", "filename": "a.md", "user_id": "userA", "status": "ready"},
    })
    monkeypatch.setattr(be, "_get_source_status", lambda sid: {"status": "ready", "user_id": "userA", "source_stem": "doc_a"} if sid == "idA" else None)
    _protect(be, monkeypatch, "userB", on=True)  # B is not the owner
    assert client.get("/sources/idA/status").status_code == 404
    assert client.post("/delete-source", json={"video": "doc_a"}).status_code == 404
    assert client.get("/videos/doc_a.mp4").status_code == 404
    # owner A: status passes the owner check (may 404 later for missing files, but not the owner gate)
    _protect(be, monkeypatch, "userA", on=True)
    assert client.get("/sources/idA/status").status_code == 200
