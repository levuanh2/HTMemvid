import json
from services.mindmap.pipeline import relations as rel

NODES = [
    {"id": "n0", "parent": None, "kind": "root", "title": "R"},
    {"id": "n1", "parent": "n0", "kind": "section", "title": "Phương pháp", "note": "..."},
    {"id": "n2", "parent": "n0", "kind": "section", "title": "Kết quả", "note": "..."},
]

def test_relations_parsed_and_validated(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    def fake_ask(prompt, system_prompt=None, model=None, feature=None, options=None, **kw):
        return json.dumps({"relations": [
            {"source": "n1", "target": "n2", "type": "leads_to", "label": "dẫn tới"},
            {"source": "n1", "target": "n1", "type": "causes", "label": "loop"},
        ]})
    monkeypatch.setattr(rel, "ask_ai", fake_ask)
    out, degraded = rel.extract_relations(NODES, model="m", timeout_sec=5)
    assert degraded is False
    assert out == [{"source": "n1", "target": "n2", "type": "leads_to", "label": "dẫn tới"}]

def test_relations_llm_failure_degrades(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    monkeypatch.setattr(rel, "ask_ai", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out, degraded = rel.extract_relations(NODES, model="m", timeout_sec=5)
    assert out == [] and degraded is True

def test_relations_skipped_when_too_few_sections():
    out, degraded = rel.extract_relations(NODES[:2], model="m")
    assert out == [] and degraded is False
