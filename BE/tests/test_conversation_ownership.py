"""Auth Hardening Phase B — conversation ownership (store + endpoints).

Store tests are pure (tmp CONVERSATIONS_DB_PATH). Endpoint tests reuse the shared
`client` fixture and monkeypatch main._conversation_enabled / _auth_protect_enabled
/ _current_user_id to simulate the flag and users A/B.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(tmp_path / "conversations.sqlite"))
    from app.domains.conversation import store as s
    importlib.reload(s)
    s.init_db()
    return s


# ---- store-level owner enforcement ----------------------------------------

def test_ensure_establishes_owner_and_blocks_hijack(store):
    assert store.ensure_conversation("c1", user_id="A", enforce_owner=True) is True
    assert store.owner_check("c1", "A") is True
    # B cannot hijack an existing conversation
    assert store.ensure_conversation("c1", user_id="B", enforce_owner=True) is False
    assert store.get_conversation("c1")["user_id"] == "A"
    assert store.owner_check("c1", "B") is False


def test_append_message_owner_enforced(store):
    store.append_message("c1", "user", "hi A", user_id="A", enforce_owner=True)
    # B cannot append to A's conversation
    assert store.append_message("c1", "user", "hi B", user_id="B", enforce_owner=True) is None
    msgs = store.get_messages("c1", user_id="A", enforce_owner=True)
    assert [m["content"] for m in msgs] == ["hi A"]


def test_get_messages_foreign_returns_empty(store):
    store.append_message("c1", "user", "secret", user_id="A", enforce_owner=True)
    assert store.get_messages("c1", user_id="B", enforce_owner=True) == []
    assert store.get_messages("c1", user_id="A", enforce_owner=True)  # owner sees data


def test_set_context_reset_foreign_denied(store):
    store.append_message("c1", "user", "x", user_id="A", enforce_owner=True)
    assert store.set_context_reset("c1", user_id="B", enforce_owner=True) is None
    # A's row untouched (no reset)
    assert store.get_conversation("c1")["context_reset_at"] is None
    assert store.set_context_reset("c1", user_id="A", enforce_owner=True) is not None


def test_delete_messages_foreign_denied(store):
    store.append_message("c1", "user", "x", user_id="A", enforce_owner=True)
    assert store.delete_messages("c1", user_id="B", enforce_owner=True) == 0
    assert len(store.get_messages("c1", user_id="A", enforce_owner=True)) == 1
    assert store.delete_messages("c1", user_id="A", enforce_owner=True) == 1


def test_legacy_null_owner_denied_when_enforced(store):
    # Row created with no owner (legacy / flag-off write)
    store.append_message("c1", "user", "legacy")  # enforce_owner=False, user_id=None
    assert store.owner_check("c1", "A") is False  # NULL owner ≠ A → fail-closed
    assert store.get_messages("c1", user_id="A", enforce_owner=True) == []


def test_non_enforce_is_backward_compatible(store):
    # enforce_owner=False → open behavior regardless of user_id
    store.append_message("c1", "user", "m1")
    store.append_message("c1", "assistant", "m2")
    assert len(store.get_messages("c1")) == 2
    assert store.set_context_reset("c1") is not None
    assert store.delete_messages("c1") == 2


# ---- endpoint-level (flag on/off) ------------------------------------------

@pytest.fixture()
def conv_client(client, tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(tmp_path / "conversations.sqlite"))
    from app.domains.conversation import store as s
    importlib.reload(s)
    s.init_db()
    monkeypatch.setattr(main, "_conversation_enabled", lambda: True)
    return client, main, s


def _as_user(main, monkeypatch, uid, protect=True):
    monkeypatch.setattr(main, "_auth_protect_enabled", lambda: protect)
    monkeypatch.setattr(main, "_current_user_id", lambda: uid)


def test_endpoints_401_without_token_when_protected(conv_client, monkeypatch):
    client, main, s = conv_client
    _as_user(main, monkeypatch, None, protect=True)  # flag on, no user
    assert client.get("/conversations/x/messages").status_code == 401
    assert client.post("/conversations/x/clear-context").status_code == 401
    assert client.delete("/conversations/x").status_code == 401


def test_owner_isolation_when_protected(conv_client, monkeypatch):
    client, main, s = conv_client
    # A owns conv-a with a message
    s.append_message("conv-a", "user", "hello", user_id="userA", enforce_owner=True)

    _as_user(main, monkeypatch, "userA", protect=True)
    assert client.get("/conversations/conv-a/messages").status_code == 200
    assert len(client.get("/conversations/conv-a/messages").get_json()["messages"]) == 1
    assert client.post("/conversations/conv-a/clear-context").status_code == 200

    # B cannot see/clear/delete A's conversation → 404 (no oracle)
    _as_user(main, monkeypatch, "userB", protect=True)
    assert client.get("/conversations/conv-a/messages").status_code == 404
    assert client.post("/conversations/conv-a/clear-context").status_code == 404
    assert client.delete("/conversations/conv-a").status_code == 404
    # A's data intact
    _as_user(main, monkeypatch, "userA", protect=True)
    assert len(client.get("/conversations/conv-a/messages").get_json()["messages"]) == 1


def test_owner_can_delete_when_protected(conv_client, monkeypatch):
    client, main, s = conv_client
    s.append_message("conv-a", "user", "x", user_id="userA", enforce_owner=True)
    _as_user(main, monkeypatch, "userA", protect=True)
    r = client.delete("/conversations/conv-a")
    assert r.status_code == 200 and r.get_json()["removed"] == 1


def test_flag_off_open_behavior(conv_client, monkeypatch):
    client, main, s = conv_client
    s.append_message("conv-a", "user", "x")  # legacy, no owner
    _as_user(main, monkeypatch, None, protect=False)  # flag OFF
    # open: no token needed, messages readable
    r = client.get("/conversations/conv-a/messages")
    assert r.status_code == 200 and len(r.get_json()["messages"]) == 1
    assert client.post("/conversations/conv-a/clear-context").status_code == 200
    assert client.delete("/conversations/conv-a").status_code == 200
