"""CRAG wiring trong query graph: grade → route (correct/ambiguous/wrong) → rewrite/fallback."""

from __future__ import annotations

import app.domains.retrieval.grading as grading
import app.domains.retrieval.query_rewrite as query_rewrite
from tests._qg_build import base_env, build, init_state, run


def _no_llm_rewrite(monkeypatch):
    # Tránh gọi LLM thật trong rewrite_query.
    monkeypatch.setattr(query_rewrite, "rewrite_query", lambda q, **k: q + " (rewritten)")


def test_crag_off_passthrough(monkeypatch):
    base_env(monkeypatch, CRAG_ENABLED="0")
    called = {"n": 0}
    monkeypatch.setattr(grading, "grade_documents", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "wrong")
    g, _ = build()
    out = run(g, init_state("hello"))
    assert called["n"] == 0  # grader không bao giờ chạy khi tắt
    assert out["payload"]["answer"] == "generated answer"


def test_crag_correct_generates(monkeypatch):
    base_env(monkeypatch, CRAG_ENABLED="1")
    _no_llm_rewrite(monkeypatch)
    monkeypatch.setattr(grading, "grade_documents", lambda *a, **k: "correct")
    g, _ = build()
    out = run(g, init_state("q"))
    assert out["payload"]["answer"] == "generated answer"
    assert int(out.get("rewrite_count") or 0) == 0


def test_crag_ambiguous_then_correct_one_rewrite(monkeypatch):
    base_env(monkeypatch, CRAG_ENABLED="1", CRAG_REWRITE_MAX="2")
    _no_llm_rewrite(monkeypatch)
    seq = iter(["ambiguous", "correct"])
    monkeypatch.setattr(grading, "grade_documents", lambda *a, **k: next(seq))
    g, _ = build()
    out = run(g, init_state("q"))
    assert out["payload"]["answer"] == "generated answer"
    assert int(out["rewrite_count"]) == 1


def test_crag_wrong_falls_back(monkeypatch):
    base_env(monkeypatch, CRAG_ENABLED="1", CRAG_REWRITE_MAX="1")
    _no_llm_rewrite(monkeypatch)
    monkeypatch.setattr(grading, "grade_documents", lambda *a, **k: "wrong")
    g, cache = build()
    out = run(g, init_state("q"))
    assert "không tìm thấy" in out["payload"]["answer"].lower()
    assert out["status_code"] == 200
    assert out.get("crag_fallback") is True
    assert cache == {}  # KHÔNG cache câu trả lời fallback


def test_crag_ambiguous_bounded(monkeypatch):
    base_env(monkeypatch, CRAG_ENABLED="1", CRAG_REWRITE_MAX="2")
    _no_llm_rewrite(monkeypatch)
    calls = {"n": 0}

    def _grade(*a, **k):
        calls["n"] += 1
        return "ambiguous"

    monkeypatch.setattr(grading, "grade_documents", _grade)
    g, _ = build()
    out = run(g, init_state("q"))
    # ambiguous hết budget → generate best-effort; grade gọi tối đa max+1 lần (3), rewrite_count==2.
    assert int(out["rewrite_count"]) == 2
    assert calls["n"] <= 3
    assert out["payload"]["answer"] == "generated answer"


def test_crag_rewrite_llm_failure_bounded(monkeypatch):
    base_env(monkeypatch, CRAG_ENABLED="1", CRAG_REWRITE_MAX="1")
    monkeypatch.setattr(grading, "grade_documents", lambda *a, **k: "wrong")

    def _boom(q, **k):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(query_rewrite, "rewrite_query", _boom)
    g, _ = build()
    out = run(g, init_state("q"))
    # LLM rewrite lỗi vẫn không vòng lặp vô hạn → vẫn tới fallback.
    assert out.get("crag_fallback") is True
    assert int(out["rewrite_count"]) == 1
