"""D5: lọc retrieval theo category/language từ metadata index.json."""
import json

from app.domains.retrieval.hybrid import HybridRetriever, _meta_match


def _write_meta(tmp_path):
    meta = {
        "0": {"text": "Nội dung y tế tiếng Việt.", "video": "doc1.mp4", "category": "yte", "language": "vi"},
        "1": {"text": "English finance content.", "video": "doc2.mp4", "category": "finance", "language": "en"},
        "__meta__": {"version": "1.1", "num_chunks": 2},
    }
    p = tmp_path / "index.json"
    p.write_text(json.dumps(meta), encoding="utf-8")
    return p


def test_loads_category_language(tmp_path):
    meta_path = _write_meta(tmp_path)
    r = HybridRetriever(index_path=tmp_path / "index.faiss", meta_path=meta_path)
    r._ensure_loaded()
    by_id = {c.chunk_id: c for c in r._chunks}
    assert by_id[0].category == "yte" and by_id[0].language == "vi"
    assert by_id[1].category == "finance" and by_id[1].language == "en"


def test_meta_match_and_filter(tmp_path):
    meta_path = _write_meta(tmp_path)
    r = HybridRetriever(index_path=tmp_path / "index.faiss", meta_path=meta_path)
    r._ensure_loaded()
    allowed = r._filter_by_sources(None)
    yte = [i for i in allowed if _meta_match(r._chunks[i], "yte", None)]
    assert [r._chunks[i].chunk_id for i in yte] == [0]
    en = [i for i in allowed if _meta_match(r._chunks[i], None, "en")]
    assert [r._chunks[i].chunk_id for i in en] == [1]
    # không filter -> giữ tất cả
    assert [i for i in allowed if _meta_match(r._chunks[i], None, None)] == allowed
