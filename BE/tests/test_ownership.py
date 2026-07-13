"""Auth Hardening Phase A — ownership columns + owned_stems helper (UNENFORCED).

Verifies the additive user_id schema and the storage/registry stamping without any
route behavior change. Each store test points its *_DB_PATH at a tmp file.
"""

from __future__ import annotations

import importlib

import pytest


# ---- additive user_id columns exist + idempotent -------------------------

def _cols(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_jobs_user_id_column(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.sqlite"))
    from app.domains.jobs import jobs_store as js
    importlib.reload(js)
    js.init_db()
    js.init_db()  # idempotent re-init must not raise
    js.create_job("j1", "query", user_id="userA")
    job = js.get_job("j1")
    assert job["user_id"] == "userA"
    js.create_job("j2", "query")  # default None
    assert js.get_job("j2")["user_id"] is None
    conn = js.get_conn()
    try:
        assert "user_id" in _cols(conn, "jobs")
    finally:
        conn.close()


def test_conversation_user_id_column(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(tmp_path / "conversations.sqlite"))
    from app.domains.conversation import store
    importlib.reload(store)
    store.init_db()
    store.init_db()
    store.ensure_conversation("c1", user_id="userA")
    assert store.get_conversation("c1")["user_id"] == "userA"
    # First writer owns it: a later ensure with a different user must NOT overwrite.
    store.ensure_conversation("c1", user_id="userB")
    assert store.get_conversation("c1")["user_id"] == "userA"


def test_summary_user_id_column(tmp_path, monkeypatch):
    monkeypatch.setenv("SUMMARIES_DB_PATH", str(tmp_path / "summaries.sqlite"))
    from app.domains.summary import store
    importlib.reload(store)
    store.init_db()
    store.init_db()
    store.save_record({"id": "s1", "content_hash": "h1", "sources": ["doc_a"]}, user_id="userA")
    conn = store.get_conn()
    try:
        assert "user_id" in _cols(conn, "summaries")
        row = conn.execute("SELECT user_id FROM summaries WHERE id='s1'").fetchone()
        assert row[0] == "userA"
    finally:
        conn.close()


def test_mindmap_user_id_column(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mindmaps.sqlite"))
    from app.domains.mindmap import store
    importlib.reload(store)
    store.init_db()
    store.init_db()
    store.save_record({"id": "m1", "content_hash": "h1", "sources": ["doc_a"]}, user_id="userA")
    conn = store.get_conn()
    try:
        assert "user_id" in _cols(conn, "mindmaps")
        row = conn.execute("SELECT user_id FROM mindmaps WHERE id='m1'").fetchone()
        assert row[0] == "userA"
    finally:
        conn.close()


# ---- legacy-DB migration: a table WITHOUT user_id gets the column added ----

def test_legacy_summaries_db_gets_user_id_column(tmp_path, monkeypatch):
    import sqlite3
    dbp = tmp_path / "summaries.sqlite"
    conn = sqlite3.connect(str(dbp))
    conn.execute(
        "CREATE TABLE summaries (id TEXT PRIMARY KEY, content_hash TEXT, sources_json TEXT, "
        "created_at TEXT, record_json TEXT)"
    )  # old schema, no user_id
    conn.execute("INSERT INTO summaries(id, content_hash) VALUES('old', 'h')")
    conn.commit(); conn.close()
    monkeypatch.setenv("SUMMARIES_DB_PATH", str(dbp))
    from app.domains.summary import store
    importlib.reload(store)
    store.init_db()  # must ALTER-add user_id without dropping the legacy row
    conn = store.get_conn()
    try:
        assert "user_id" in _cols(conn, "summaries")
        assert conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] == 1
        assert conn.execute("SELECT user_id FROM summaries WHERE id='old'").fetchone()[0] is None
    finally:
        conn.close()


# ---- owned_stems + registry stamping (via the app) ------------------------

@pytest.fixture()
def be(client, monkeypatch):
    import app.main as main
    return main


def test_owned_stems_open_mode_returns_all(be, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "id1": {"source_stem": "doc_a", "user_id": "userA"},
        "id2": {"source_stem": "doc_b", "user_id": None},
        "id3": {"source_stem": "doc_c"},  # legacy, no user_id
    })
    monkeypatch.setenv("AUTH_PROTECT_APP_APIS", "false")
    # open mode: everyone sees everything (owner filter is a no-op)
    assert be.owned_stems("userA") == {"doc_a", "doc_b", "doc_c"}
    assert be.owned_stems(None) == {"doc_a", "doc_b", "doc_c"}


def test_owned_stems_protected_mode_filters(be, monkeypatch):
    monkeypatch.setattr(be, "_load_source_registry", lambda: {
        "id1": {"source_stem": "doc_a", "user_id": "userA"},
        "id2": {"source_stem": "doc_b", "user_id": "userB"},
        "id3": {"source_stem": "doc_c"},  # legacy → hidden when protected
    })
    monkeypatch.setenv("AUTH_PROTECT_APP_APIS", "true")
    assert be.owned_stems("userA") == {"doc_a"}
    assert be.owned_stems("userB") == {"doc_b"}
    assert be.owned_stems(None) == set()  # no token → nothing (fail-closed)


def test_current_user_id_none_without_token(be):
    # Outside a request / no token → None, never raises.
    with be.app.test_request_context("/"):
        assert be._current_user_id() is None


def test_upload_stamps_owner_none_without_token(be, client, monkeypatch):
    # The conftest client patches _trigger_background_ingest to a fast no-op ingest.
    import io
    captured = {}
    orig_save = be._save_source_registry
    monkeypatch.setattr(be, "_save_source_registry", lambda reg: captured.update(reg) or orig_save(reg))
    r = client.post("/upload-file", data={"file": (io.BytesIO(b"# doc\nhi"), "own.md")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    # a registry row was written with a user_id key present (None here, no token)
    assert any("user_id" in row for row in captured.values() if isinstance(row, dict))
