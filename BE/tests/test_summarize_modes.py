import pytest
import json
from app.domains.summary.summarize_advanced import advanced_summarize

def test_advanced_summarize_fast_mode(monkeypatch):
    # Mock ask_ai to avoid LLM calls
    def mock_ask_ai(prompt, system_prompt=None, model=None, feature=None, **kw):
        return "Đây là một đoạn tóm tắt văn bản thô."
    
    from app.domains.summary import summarize_advanced
    monkeypatch.setattr(summarize_advanced, "ask_ai", mock_ask_ai)

    text = "Đoạn văn bản mẫu dùng để tóm tắt nhanh."
    # Pass pre_chunks to bypass preprocess_data (semantic split/torch load)
    res = advanced_summarize(text=text, pre_chunks=[text], mode="fast")

    # Verify return schema
    assert isinstance(res, dict)
    assert res["summary"] == "Đây là một đoạn tóm tắt văn bản thô."
    assert res["structured"]["summary"] == "Đây là một đoạn tóm tắt văn bản thô."
    assert len(res["structured"]["keyPoints"]) > 0
    assert res["structured"]["keyPoints"][0] == "Đây là một đoạn tóm tắt văn bản thô"

    # Verify fast mode overrides
    assert res["metadata"]["mode"] == "fast"
    assert res["metadata"]["used_dancer"] is False
    assert res["metadata"]["used_entity_chain"] is False
    assert res["metadata"]["used_cod"] is False
    assert res["metadata"]["used_structured"] is False
    assert res["metadata"]["used_fact_check"] is False
    assert res["metadata"]["cod_iterations"] == 0


def test_advanced_summarize_balanced_mode_defaults(monkeypatch):
    captured_args = {}

    def mock_ask_ai(prompt, system_prompt=None, model=None, feature=None, **kw):
        return "Bản tóm tắt."
    
    def mock_extract_entities(text, model=None):
        return ["Thực thể A"]

    def mock_chain_of_density(text, initial_summary, iterations, model=None):
        captured_args["cod_iterations"] = iterations
        return "Tóm tắt CoD."

    from app.domains.summary import summarize_advanced
    monkeypatch.setattr(summarize_advanced, "ask_ai", mock_ask_ai)
    monkeypatch.setattr(summarize_advanced, "extract_entities", mock_extract_entities)
    monkeypatch.setattr(summarize_advanced, "chain_of_density", mock_chain_of_density)

    # For short text, use_dancer defaults to False in balanced mode
    text = "Văn bản ngắn."
    res = advanced_summarize(text=text, pre_chunks=[text], mode="balanced")

    assert res["metadata"]["mode"] == "balanced"
    assert res["metadata"]["used_dancer"] is False
    assert res["metadata"]["used_entity_chain"] is True
    assert res["metadata"]["used_cod"] is True
    assert res["metadata"]["used_structured"] is True
    assert res["metadata"]["used_fact_check"] is False
    assert captured_args["cod_iterations"] == 1


def test_advanced_summarize_quality_mode_defaults(monkeypatch):
    captured_args = {}

    def mock_ask_ai(prompt, system_prompt=None, model=None, feature=None, **kw):
        return "Bản tóm tắt."
    
    def mock_extract_entities(text, model=None):
        return ["Thực thể A"]

    def mock_chain_of_density(text, initial_summary, iterations, model=None):
        captured_args["cod_iterations"] = iterations
        return "Tóm tắt CoD."
        
    def mock_structured_extraction(text, summary, model=None):
        return {"summary": "Tóm tắt có cấu trúc"}

    def mock_fact_check(source_text, summary, model=None):
        return {"status": "CONSISTENT"}

    from app.domains.summary import summarize_advanced
    monkeypatch.setattr(summarize_advanced, "ask_ai", mock_ask_ai)
    monkeypatch.setattr(summarize_advanced, "extract_entities", mock_extract_entities)
    monkeypatch.setattr(summarize_advanced, "chain_of_density", mock_chain_of_density)
    monkeypatch.setattr(summarize_advanced, "structured_extraction", mock_structured_extraction)
    monkeypatch.setattr(summarize_advanced, "fact_check", mock_fact_check)

    text = "Văn bản ngắn dùng để kiểm thử chế độ quality."
    res = advanced_summarize(text=text, pre_chunks=[text], mode="quality")

    assert res["metadata"]["mode"] == "quality"
    assert res["metadata"]["used_dancer"] is False  # text < 2000 chars
    assert res["metadata"]["used_entity_chain"] is True
    assert res["metadata"]["used_cod"] is True
    assert res["metadata"]["used_structured"] is True
    assert res["metadata"]["used_fact_check"] is True
    # text < 1000 -> iterations = 1
    assert captured_args["cod_iterations"] == 1


def test_summarize_documents_route(client, tmp_path, monkeypatch):
    """Kiểm tra xem API /summarize-documents nhận tham số mode và truyền đúng."""
    captured = {}

    def mock_advanced_summarize(**kwargs):
        captured.update(kwargs)
        return {
            "summary": "Tóm tắt mẫu",
            "structured": {},
            "metadata": {"mode": kwargs.get("mode")}
        }

    import app.main as be_main
    monkeypatch.setattr(be_main, "advanced_summarize", mock_advanced_summarize)
    
    # Mock chunk_text_store.get_text và meta
    monkeypatch.setattr(be_main, "_load_source_registry", lambda: {})
    
    # Create a temporary index.json metadata file
    meta_file = tmp_path / "index.json"
    # set video to doc_1.mp4 to match sources=["doc_1"]
    meta_data = {"0": {"video": "doc_1.mp4", "source_stem": "doc_1", "text": "Đoạn văn bản nguồn."}}
    meta_file.write_text(json.dumps(meta_data), encoding="utf-8")
    monkeypatch.setattr(be_main, "INDEX_META_JSON_PATH", meta_file)

    from app.domains.vectorstore import chunk_text_store
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: "Đoạn văn bản nguồn.")

    r = client.post("/summarize-documents", json={
        "sources": ["doc_1"],
        "mode": "fast"
    })
    
    assert r.status_code == 200
    assert captured["mode"] == "fast"
