"""Phase D-minimal — cache & single-flight safety guards for conversational follow-ups.

We keep the current, already-safe invariant: contextual follow-ups BYPASS the cache
(never reuse an answer from another document, another conversation, or before Clear
Context). These tests lock that invariant in so a future change can't silently break it.
No contextual answer caching is added in this phase.
"""

from __future__ import annotations

import importlib
import time

import pytest


# ---- graph: contextual follow-up bypasses the cache; standalone still caches ----

def test_followup_with_history_bypasses_cache(monkeypatch):
    from tests import _qg_build as qb
    qb.base_env(monkeypatch)
    g, cache = qb.build()
    # A follow-up ("nó ...") WITH history → cache_lookup must bypass (cache_key=None),
    # so finalize writes nothing: the answer can never be reused across documents.
    state = qb.init_state("nó là gì", conversation_history=[
        {"role": "user", "content": "Nội dung file là gì?"},
        {"role": "assistant", "content": "File nói về Phase 5."},
    ])
    out = qb.run(g, state, thread_id="tfollow")
    assert (out.get("payload") or {}).get("answer")
    assert cache == {}  # nothing cached for a contextual follow-up


def test_standalone_question_still_caches(monkeypatch):
    from tests import _qg_build as qb
    qb.base_env(monkeypatch)
    g, cache = qb.build()
    state = qb.init_state("MemVid dùng cơ sở dữ liệu nào?", conversation_history=[])
    qb.run(g, state, thread_id="tstand")
    assert cache  # standalone question is cached as before


def test_standalone_with_history_still_caches(monkeypatch):
    # is_standalone_question heuristic: a full standalone question with history is
    # still cacheable (generate_answer drops history from the prompt when cache_key set).
    from tests import _qg_build as qb
    qb.base_env(monkeypatch)
    g, cache = qb.build()
    state = qb.init_state("MemVid dùng cơ sở dữ liệu nào để lưu job?", conversation_history=[
        {"role": "user", "content": "chào"},
        {"role": "assistant", "content": "chào bạn"},
    ])
    qb.run(g, state, thread_id="tstandh")
    assert cache


# ---- cross-document / reset: the scope signature changes ----

@pytest.fixture()
def conv(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSATIONS_DB_PATH", str(tmp_path / "conversations.sqlite"))
    from app.domains.conversation import store
    importlib.reload(store)
    store.init_db()
    from app.domains.conversation import context_builder
    importlib.reload(context_builder)
    return store, context_builder


def test_generic_followup_scoped_by_document(conv):
    from app.domains.cache import llm_cache
    # 'nó là gì' means different things per document → different cache bucket → no reuse.
    assert llm_cache.source_context_hash(["doc_a"]) != llm_cache.source_context_hash(["doc_b"])


def test_context_signature_changes_after_clear_context(conv):
    store, cb = conv
    store.append_message("c1", "user", "Nội dung là gì?", selected_source_ids=["doc_a"], source_context_hash="hA")
    store.append_message("c1", "assistant", "Phase 5.", selected_source_ids=["doc_a"], source_context_hash="hA",
                         answer_summary="Phase 5.")
    sig_before = cb.build_recent_conversation_context("c1", ["doc_a"], "hA").context_signature
    assert sig_before

    store.set_context_reset("c1")
    time.sleep(0.01)
    # New same-scope turn AFTER the reset → a fresh signature (reset_at folded in).
    store.append_message("c1", "user", "Còn queue thì sao?", selected_source_ids=["doc_a"], source_context_hash="hA")
    sig_after = cb.build_recent_conversation_context("c1", ["doc_a"], "hA").context_signature
    assert sig_after and sig_after != sig_before  # pre-reset context can't be reused


def test_cleared_context_yields_empty_until_new_turns(conv):
    store, cb = conv
    store.append_message("c1", "user", "cũ", selected_source_ids=["doc_a"], source_context_hash="hA")
    store.set_context_reset("c1")
    time.sleep(0.01)
    ctx = cb.build_recent_conversation_context("c1", ["doc_a"], "hA")
    assert ctx.is_empty is True  # 'nó là gì' right after clear resolves to nothing old


# ---- single-flight still bypasses contextual follow-ups ----

def test_single_flight_bypasses_followup_with_history(monkeypatch):
    import app.main as main

    monkeypatch.setenv("SINGLE_FLIGHT_ENABLED", "true")
    monkeypatch.setattr(main, "_get_session_history_safe", lambda sid, n: [
        {"role": "user", "content": "Nội dung là gì?"},
        {"role": "assistant", "content": "Phase 5."},
    ])
    # A follow-up must not coalesce (context-specific → different answers per session).
    res = main._single_flight_try("j1", "nó là gì", [], True, None, None, "s1")
    assert res == {"served": False, "lock": None}
