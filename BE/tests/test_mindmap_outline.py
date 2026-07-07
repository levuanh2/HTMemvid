import json

from services.mindmap.pipeline import outline as ol


def _mm(chunks=None, title="Tài liệu X"):
    return {"title": title, "sources": ["x"],
            "chunks": chunks or [
                {"key": "0", "text": "đoạn một", "chunk_keys": ["0"]},
                {"key": "1", "text": "đoạn hai", "chunk_keys": ["1"]},
            ]}


def test_outline_happy_path_builds_two_level_tree(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    payload = {"sections": [
        {"title": "Phần A", "children": [
            {"title": "Ý A1", "chunk_keys": ["0"]},
            {"title": "Ý A2", "chunk_keys": ["9999"]},  # id bịa → phải bị lọc
        ]},
        {"title": "Phần B", "children": []},
    ]}
    monkeypatch.setattr(ol, "ask_ai", lambda *a, **kw: json.dumps(payload))
    nodes = ol.build_outline(_mm(), model="m", timeout_sec=5)
    assert nodes is not None
    root = next(n for n in nodes if n["kind"] == "root")
    secs = [n for n in nodes if n["kind"] == "section" and n["parent"] == root["id"]]
    assert [s["title"] for s in secs] == ["Phần A", "Phần B"]
    a1 = next(n for n in nodes if n["title"] == "Ý A1")
    assert a1["chunk_refs"] == ["0"]
    a2 = next(n for n in nodes if n["title"] == "Ý A2")
    assert a2["chunk_refs"] == []  # id bịa bị lọc, node vẫn giữ


def test_outline_malformed_json_returns_none(monkeypatch):
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    monkeypatch.setattr(ol, "ask_ai", lambda *a, **kw: "không phải json {{{")
    assert ol.build_outline(_mm(), model="m", timeout_sec=5) is None


def test_outline_skipped_under_skip_model_load(monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    called = {"n": 0}

    def _boom(*a, **kw):
        called["n"] += 1
        return "{}"
    monkeypatch.setattr(ol, "ask_ai", _boom)
    assert ol.build_outline(_mm(), model="m", timeout_sec=5) is None
    assert called["n"] == 0


def test_outline_single_fat_section_reshaped_to_sections(monkeypatch):
    # LLM lười trả 1 section ôm >=4 children → children phải được promote thành
    # sections (con của root) để cây có nhiều nhánh thật thay vì 1 cột phẳng.
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    payload = {"sections": [{"title": "Tất cả trong một", "children": [
        {"title": f"Chủ đề {i}", "chunk_keys": ["0"]} for i in range(4)
    ]}]}
    monkeypatch.setattr(ol, "ask_ai", lambda *a, **kw: json.dumps(payload))
    nodes = ol.build_outline(_mm(), model="m", timeout_sec=5)
    assert nodes is not None
    root = next(n for n in nodes if n["kind"] == "root")
    secs = [n for n in nodes if n["kind"] == "section" and n["parent"] == root["id"]]
    assert [s["title"] for s in secs] == [f"Chủ đề {i}" for i in range(4)]
    assert not any(n["title"] == "Tất cả trong một" for n in nodes)  # vỏ rỗng bị bỏ


def test_outline_malformed_shapes_degrade_to_none(monkeypatch):
    # codex #2: sections là string / children là string → phải trả None (degraded),
    # không được ném AttributeError thành job error.
    monkeypatch.delenv("SKIP_MODEL_LOAD", raising=False)
    for payload in ('{"sections": ["foo", "bar"]}',
                    '{"sections": [{"title": "A", "children": ["x", "y"]}]}',
                    '{"sections": "not a list"}'):
        monkeypatch.setattr(ol, "ask_ai", lambda *a, _p=payload, **kw: _p)
        result = ol.build_outline(_mm(), model="m", timeout_sec=5)
        # payload 2 có section title hợp lệ → cho phép nodes hợp lệ HOẶC None,
        # miễn KHÔNG ném exception
        assert result is None or isinstance(result, list)
