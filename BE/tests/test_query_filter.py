"""Wire filter category/language vào /query (b)."""


def test_query_accepts_filters(client):
    r = client.post("/query", json={"q": "tài liệu nói gì?", "category": "yte", "language": "vi"})
    assert r.status_code == 202
    assert r.get_json().get("job_id")


def test_cache_key_includes_filters():
    import app.main as be_main
    base = be_main._make_query_cache_key("q", [], True, None)
    with_cat = be_main._make_query_cache_key("q", [], True, {"category": "yte"})
    with_lang = be_main._make_query_cache_key("q", [], True, {"language": "vi"})
    assert base != with_cat != with_lang
    assert base != with_lang
