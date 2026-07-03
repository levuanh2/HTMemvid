from services.mindmap.jsonrepair import repair_json_text


def test_strips_code_fence_and_trailing_comma():
    raw = '```json\n{"a": [1, 2,], "b": "x,y",}\n```'
    out = repair_json_text(raw)
    import json
    data = json.loads(out)
    assert data["a"] == [1, 2]
    assert data["b"] == "x,y"  # comma TRONG chuỗi không bị đụng


def test_plain_json_unchanged():
    import json
    assert json.loads(repair_json_text('{"k": "v"}')) == {"k": "v"}
