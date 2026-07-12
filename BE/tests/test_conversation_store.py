"""Phase A — Conversation Context Layer storage + /query wiring.

Store tests are pure (no app): they point CONVERSATIONS_DB_PATH at a tmp file.
Wiring tests force the feature flag on via env + shared.config.reload() and call the
main.py helpers directly (the conftest `client` fixture is session-scoped with the
flag OFF, which itself proves the flag-off no-op path).
"""

from __future__ import annotations

import importlib
import time

import pytest


@pytest.fixture()
def conv_store(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(tmp_path / "conversations.sqlite"))
    from app.domains.conversation import store
    importlib.reload(store)
    store.init_db()
    return store


# ---- store: conversations ---------------------------------------------------

def test_ensure_conversation_creates_row(conv_store):
    conv_store.ensure_conversation("c1", active_source_scope=["doc_a"])
    row = conv_store.get_conversation("c1")
    assert row is not None
    assert row["conversation_id"] == "c1"
    assert row["context_reset_at"] is None
    assert row["active_source_scope"] == ["doc_a"]


def test_ensure_conversation_idempotent(conv_store):
    conv_store.ensure_conversation("c1")
    conv_store.ensure_conversation("c1")
    assert conv_store.get_conversation("c1") is not None


def test_get_unknown_conversation_returns_none(conv_store):
    assert conv_store.get_conversation("nope") is None
    assert conv_store.get_messages("nope") == []


# ---- store: messages --------------------------------------------------------

def test_append_and_get_messages_ordered(conv_store):
    conv_store.append_message("c1", "user", "câu hỏi 1", selected_source_ids=["doc_a"], source_context_hash="h1")
    conv_store.append_message("c1", "assistant", "trả lời 1", source_context_hash="h1", answer_summary="tl1")
    msgs = conv_store.get_messages("c1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["selected_source_ids"] == ["doc_a"]
    assert msgs[1]["answer_summary"] == "tl1"
    assert msgs[0]["source_context_hash"] == "h1"


def test_append_skips_empty(conv_store):
    assert conv_store.append_message("c1", "user", "   ") is None
    assert conv_store.append_message("c1", "", "hi") is None
    assert conv_store.get_messages("c1") == []


def test_get_messages_after_ts_excludes_old(conv_store):
    conv_store.append_message("c1", "user", "old")
    time.sleep(0.01)
    cut = time.time()
    time.sleep(0.01)
    conv_store.append_message("c1", "user", "new")
    recent = conv_store.get_messages("c1", after_ts=cut)
    assert [m["content"] for m in recent] == ["new"]


def test_get_messages_limit_returns_newest(conv_store):
    for i in range(5):
        conv_store.append_message("c1", "user", f"m{i}")
    last2 = conv_store.get_messages("c1", limit=2)
    assert [m["content"] for m in last2] == ["m3", "m4"]  # oldest-first within the newest-2


# ---- store: reset / delete / cleanup ---------------------------------------

def test_set_context_reset_persists(conv_store):
    ts = conv_store.set_context_reset("c1")
    row = conv_store.get_conversation("c1")
    assert row["context_reset_at"] == pytest.approx(ts, abs=0.01)


def test_delete_messages_hard_removes(conv_store):
    conv_store.append_message("c1", "user", "a")
    conv_store.append_message("c1", "assistant", "b")
    removed = conv_store.delete_messages("c1")
    assert removed == 2
    assert conv_store.get_messages("c1") == []
    # conversation row itself survives a history delete
    assert conv_store.get_conversation("c1") is not None


def test_cap_drops_oldest(conv_store, monkeypatch):
    monkeypatch.setenv("CONVERSATION_MAX_MESSAGES", "3")
    for i in range(5):
        conv_store.append_message("c1", "user", f"m{i}")
    msgs = conv_store.get_messages("c1")
    assert [m["content"] for m in msgs] == ["m2", "m3", "m4"]


def test_cleanup_expired_ttl_zero_is_noop(conv_store, monkeypatch):
    monkeypatch.setenv("CONVERSATION_TTL_HOURS", "0")  # disabled → keeps everything
    conv_store.append_message("c1", "user", "a")
    conv_store.cleanup_expired()
    assert len(conv_store.get_messages("c1")) == 1
    assert conv_store.get_conversation("c1") is not None


def test_cleanup_expired_removes_stale(conv_store, monkeypatch):
    monkeypatch.setenv("CONVERSATION_TTL_HOURS", "24")
    conv_store.append_message("c1", "user", "a")
    # Force the conversation's updated_at far into the past (older than the TTL).
    conn = conv_store.get_conn()
    try:
        conn.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                     (time.time() - 48 * 3600, "c1"))
        conn.commit()
    finally:
        conn.close()
    conv_store.cleanup_expired()
    assert conv_store.get_conversation("c1") is None
    assert conv_store.get_messages("c1") == []


# ---- source_context_hash (cache bucket alias) ------------------------------

def test_source_context_hash_scoped_by_sources():
    from app.domains.cache import llm_cache
    h_a = llm_cache.source_context_hash(["doc_a"])
    h_b = llm_cache.source_context_hash(["doc_b"])
    assert h_a and h_b and h_a != h_b  # different documents → different scope


# ---- /query wiring (flag on) -----------------------------------------------

@pytest.fixture()
def be_main_flag_on(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(tmp_path / "conversations.sqlite"))
    monkeypatch.setenv("CONVERSATION_CONTEXT_ENABLED", "1")
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    import shared.config as config
    config.reload()
    from app.domains.conversation import store
    importlib.reload(store)
    store.init_db()
    import app.main as be_main
    yield be_main, store
    monkeypatch.setenv("CONVERSATION_CONTEXT_ENABLED", "0")
    config.reload()


def test_save_conversation_turns_writes_rows_when_enabled(be_main_flag_on):
    be_main, store = be_main_flag_on
    assert be_main._conversation_enabled() is True
    be_main._save_conversation_turns(
        "sess1", "Nội dung file là gì?", "File nói về Phase 5.",
        source_ids=["doc_a"], source_context_hash="h1", cited_chunk_ids=["doc_a::0"],
    )
    msgs = store.get_messages("sess1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["answer_summary"] == "File nói về Phase 5."
    assert msgs[1]["cited_chunk_ids"] == ["doc_a::0"]


def test_save_conversation_turns_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(tmp_path / "conversations.sqlite"))
    monkeypatch.setenv("CONVERSATION_CONTEXT_ENABLED", "0")
    import shared.config as config
    config.reload()
    from app.domains.conversation import store
    importlib.reload(store)
    store.init_db()
    import app.main as be_main
    be_main._save_conversation_turns("sess2", "q", "a", source_ids=["doc_a"])
    assert store.get_messages("sess2") == []


def test_save_turns_never_raises_on_db_error(be_main_flag_on, monkeypatch):
    be_main, store = be_main_flag_on
    from app.domains.conversation import store as conv_store
    monkeypatch.setattr(conv_store, "append_message", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    # Must swallow — a store failure never breaks /query.
    be_main._save_conversation_turns("sess3", "q", "a")


# ---- T15: /query stays in-process (no RQ enqueue) --------------------------

def test_query_does_not_use_rq_enqueue(client, monkeypatch):
    import app.jobs.queue as queue
    calls = []
    monkeypatch.setattr(queue, "enqueue_job", lambda *a, **k: calls.append((a, k)))
    r = client.post("/query", json={"q": "test", "sources": [], "use_memory_tree": False})
    assert r.status_code == 202
    assert r.get_json().get("session_id")
    time.sleep(0.2)
    assert calls == []  # query runs on a daemon thread, never through the queue
