"""Phase C — conversational follow-up rewrite + retrieval integration.

Rewrite tests mock the LLM (get_llm) to return fixed JSON. Retrieval tests use the
real query graph with a recording retriever to prove the standalone question drives
retrieval while the original question stays in state for answer generation.
"""

from __future__ import annotations

import json

import pytest


class _FakeLLM:
    def __init__(self, resp: str):
        self.resp = resp

    def invoke(self, messages, **kwargs):
        from langchain_core.messages import AIMessage
        return AIMessage(content=self.resp)


def _mock_llm(monkeypatch, resp: str):
    import app.clients.llm_factory as lf
    monkeypatch.setattr(lf, "get_llm", lambda **k: _FakeLLM(resp))


_CTX = {"turns": [
    {"role": "user", "content": "Nội dung file là gì?"},
    {"role": "assistant", "content": "File nói về Phase 5 RQ worker: ingest, summary, mindmap."},
]}


def _resp(standalone, needs=True, conf=0.9):
    return json.dumps({
        "standalone_question": standalone, "needs_context": needs,
        "refers_to_previous_answer": needs, "confidence": conf, "reason": "test",
    }, ensure_ascii=False)


@pytest.mark.parametrize("q,standalone", [
    ("nó là gì", "Phase 5 RQ worker là gì?"),
    ("phần đó nói kỹ hơn", "Giải thích kỹ hơn phần Phase 5 RQ worker."),
    ("ý trên có đúng không", "Ý về Phase 5 RQ worker ở trên có đúng không?"),
    ("so sánh cái này với cái kia", "So sánh queue ingest với queue summary."),
])
def test_vietnamese_followup_rewrite(monkeypatch, q, standalone):
    from app.domains.conversation.rewrite import rewrite_followup_question
    _mock_llm(monkeypatch, _resp(standalone))
    out = rewrite_followup_question(q, _CTX, ["doc_a"])
    assert out["standalone_question"] == standalone
    assert out["needs_context"] is True
    assert out["confidence"] == pytest.approx(0.9)


def test_standalone_question_kept_when_no_context_needed(monkeypatch):
    from app.domains.conversation.rewrite import rewrite_followup_question
    q = "MemVid dùng cơ sở dữ liệu nào?"
    _mock_llm(monkeypatch, _resp(q, needs=False, conf=0.95))
    out = rewrite_followup_question(q, _CTX, ["doc_a"])
    assert out["standalone_question"] == q
    assert out["needs_context"] is False


def test_low_confidence_keeps_original(monkeypatch):
    from app.domains.conversation.rewrite import rewrite_followup_question, decide_context_mode
    q = "nó là gì"
    _mock_llm(monkeypatch, _resp("một câu đoán mơ hồ", needs=True, conf=0.2))
    out = rewrite_followup_question(q, _CTX, ["doc_a"])
    assert out["standalone_question"] == q  # low confidence → keep original
    assert decide_context_mode(out) == "low_confidence_contextual"


def test_parse_failure_falls_back_to_original(monkeypatch):
    from app.domains.conversation.rewrite import rewrite_followup_question
    q = "nó là gì"
    _mock_llm(monkeypatch, "xin lỗi tôi không thể trả về JSON")
    out = rewrite_followup_question(q, _CTX, ["doc_a"])
    assert out["standalone_question"] == q
    assert out["confidence"] == 0.0


def test_llm_error_falls_back_to_original(monkeypatch):
    from app.domains.conversation.rewrite import rewrite_followup_question
    import app.clients.llm_factory as lf

    class _Boom:
        def invoke(self, *a, **k):
            raise RuntimeError("model down")

    monkeypatch.setattr(lf, "get_llm", lambda **k: _Boom())
    out = rewrite_followup_question("nó là gì", _CTX, ["doc_a"])
    assert out["standalone_question"] == "nó là gì"
    assert out["reason"] == "llm_error"


def test_empty_context_is_standalone(monkeypatch):
    from app.domains.conversation.rewrite import rewrite_followup_question
    out = rewrite_followup_question("nó là gì", {"turns": []}, ["doc_a"])
    assert out["needs_context"] is False
    assert out["standalone_question"] == "nó là gì"


def test_decide_context_mode_mapping():
    from app.domains.conversation.rewrite import decide_context_mode
    assert decide_context_mode({"needs_context": False}) == "standalone"
    assert decide_context_mode({"needs_context": True, "confidence": 0.9}) == "contextual"
    assert decide_context_mode({"needs_context": True, "confidence": 0.1}) == "low_confidence_contextual"


# ---- retrieval uses the standalone question; answer keeps the original -----

class _RecordingRetriever:
    def __init__(self, chunks):
        self._chunks = chunks
        self.queries = []

    def retrieve(self, q, **kwargs):
        self.queries.append(q)
        return list(self._chunks)


def test_retrieval_uses_standalone_question(monkeypatch):
    from tests import _qg_build as qb
    qb.base_env(monkeypatch)
    rec = _RecordingRetriever([qb.StubChunk("Phase 5 RQ worker relevant chunk")])
    g, _cache = qb.build(retriever=rec)
    state = qb.init_state(
        "nó là gì",
        standalone_question="Phase 5 RQ worker là gì?",
        original_question="nó là gì",
        context_mode="contextual",
    )
    out = qb.run(g, state, thread_id="tret")
    assert rec.queries and rec.queries[0] == "Phase 5 RQ worker là gì?"  # retrieval used rewrite
    assert out.get("q") == "nó là gì"  # answer path still sees the original question


def test_retrieval_falls_back_to_original_when_no_rewrite(monkeypatch):
    from tests import _qg_build as qb
    qb.base_env(monkeypatch)
    rec = _RecordingRetriever([qb.StubChunk("relevant chunk")])
    g, _cache = qb.build(retriever=rec)
    state = qb.init_state("MemVid là gì?")  # no standalone_question set
    qb.run(g, state, thread_id="tret2")
    assert rec.queries and rec.queries[0] == "MemVid là gì?"
