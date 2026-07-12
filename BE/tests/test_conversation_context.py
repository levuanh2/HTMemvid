"""Phase B — recent context builder + Clear/Delete/Get endpoints.

Builder tests are pure (tmp DB). Endpoint tests reuse the session-scoped `client`
fixture and force the flag on by monkeypatching main._conversation_enabled (the
store writes under the fixture's tmp DATA_DIR). A real-graph build test proves the
new QueryState fields are accepted by langgraph/pydantic.
"""

from __future__ import annotations

import importlib
import time

import pytest


@pytest.fixture()
def conv(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(tmp_path / "conversations.sqlite"))
    from app.domains.conversation import store
    importlib.reload(store)
    store.init_db()
    from app.domains.conversation import context_builder
    importlib.reload(context_builder)
    return store, context_builder


# ---- builder ---------------------------------------------------------------

def _seed(store, cid, hash_a="hA"):
    store.append_message(cid, "user", "Nội dung file là gì?", selected_source_ids=["doc_a"], source_context_hash=hash_a)
    store.append_message(cid, "assistant", "File nói về Phase 5 RQ worker.", selected_source_ids=["doc_a"],
                         source_context_hash=hash_a, answer_summary="File nói về Phase 5 RQ worker.")


def test_builder_returns_recent_scoped_turns(conv):
    store, cb = conv
    _seed(store, "c1")
    ctx = cb.build_recent_conversation_context("c1", selected_sources=["doc_a"], source_context_hash="hA")
    assert ctx.is_empty is False
    assert ctx.source_scope_match is True
    assert [t["role"] for t in ctx.turns] == ["user", "assistant"]
    assert ctx.context_signature


def test_builder_empty_for_unknown_conversation(conv):
    store, cb = conv
    ctx = cb.build_recent_conversation_context("nope", selected_sources=["doc_a"], source_context_hash="hA")
    assert ctx.is_empty is True
    assert ctx.turns == []


def test_builder_respects_context_reset_at(conv):
    store, cb = conv
    _seed(store, "c1")
    store.set_context_reset("c1")  # reset now → older turns excluded
    time.sleep(0.01)
    ctx = cb.build_recent_conversation_context("c1", selected_sources=["doc_a"], source_context_hash="hA")
    assert ctx.is_empty is True  # everything is before the reset


def test_builder_drops_cross_document_turns(conv):
    store, cb = conv
    _seed(store, "c1", hash_a="hA")  # turns scoped to doc_a / hA
    # Now the user switches to doc_b (different bucket) → no same-scope context → no leak.
    ctx = cb.build_recent_conversation_context("c1", selected_sources=["doc_b"], source_context_hash="hB")
    assert ctx.source_scope_match is False
    assert ctx.is_empty is True


def test_builder_trims_to_max_turns(conv):
    store, cb = conv
    for i in range(10):
        store.append_message("c1", "user", f"q{i}", selected_source_ids=["doc_a"], source_context_hash="hA")
    ctx = cb.build_recent_conversation_context("c1", selected_sources=["doc_a"], source_context_hash="hA", max_turns=4)
    assert len(ctx.turns) == 4
    assert ctx.turns[-1]["content"] == "q9"


# ---- endpoints (flag forced on against the shared client) ------------------

@pytest.fixture()
def flag_on(client, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "_conversation_enabled", lambda: True)
    from app.domains.conversation import store
    importlib.reload(store)
    store.init_db()
    return client, main, store


def test_clear_context_keeps_messages_sets_reset(flag_on):
    client, main, store = flag_on
    store.append_message("s1", "user", "cũ")
    r = client.post("/conversations/s1/clear-context")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert store.get_messages("s1")  # messages still in DB (not deleted)
    assert store.get_conversation("s1")["context_reset_at"] is not None


def test_delete_conversation_hard_deletes(flag_on):
    client, main, store = flag_on
    store.append_message("s2", "user", "a")
    store.append_message("s2", "assistant", "b")
    r = client.delete("/conversations/s2")
    assert r.status_code == 200 and r.get_json()["removed"] == 2
    assert store.get_messages("s2") == []


def test_get_messages_returns_history(flag_on):
    client, main, store = flag_on
    store.append_message("s3", "user", "hỏi")
    store.append_message("s3", "assistant", "đáp")
    r = client.get("/conversations/s3/messages")
    assert r.status_code == 200
    roles = [m["role"] for m in r.get_json()["messages"]]
    assert roles == ["user", "assistant"]


def test_endpoints_404_when_flag_disabled(client):
    # Shared client has the flag OFF → controls report disabled, never 500.
    assert client.post("/conversations/x/clear-context").status_code == 404
    assert client.delete("/conversations/x").status_code == 404
    assert client.get("/conversations/x/messages").status_code == 404


# ---- real graph accepts the new state fields -------------------------------

def test_query_graph_accepts_conversation_context_fields(monkeypatch):
    from tests import _qg_build as qb
    qb.base_env(monkeypatch)
    g, _cache = qb.build()
    state = qb.init_state(
        "nó là gì",
        conversation_context={"turns": [{"role": "user", "content": "x"}], "is_empty": False},
        source_context_hash="hA",
        original_question="nó là gì",
        standalone_question="Phase 5 RQ worker là gì",
        context_mode="contextual",
        context_signature="sig1",
    )
    out = qb.run(g, state, thread_id="tctx")
    assert isinstance(out, dict)
    assert (out.get("payload") or {}).get("answer")
