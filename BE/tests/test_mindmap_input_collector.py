import json
from app.domains.mindmap import input_collector as ic
from app.domains.vectorstore import chunk_text_store


def _write_meta(tmp_path, meta):
    p = tmp_path / "index.json"
    p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return p


def test_collects_matching_source_with_store_text(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: f"text-{cid}")
    meta = {
        "0": {"source_stem": "bao_cao_docx", "video": "v.mp4", "heading_path": "1. Mở đầu"},
        "1": {"source_stem": "khac_docx", "video": "k.mp4"},
    }
    out = ic.collect_mindmap_input(_write_meta(tmp_path, meta), ["bao_cao_docx"])
    assert out["sources"] == ["bao_cao_docx"]
    assert len(out["chunks"]) == 1
    c = out["chunks"][0]
    assert c["text"] == "text-0" and c["heading_path"] == "1. Mở đầu" and c["chunk_keys"] == ["0"]


def test_merges_subchunks_by_parent(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: f"t{cid}")
    meta = {
        "10": {"source_stem": "a_docx", "heading_path": "H"},
        "11": {"source_stem": "a_docx", "is_subchunk": True, "parent_id": "10", "sub_order": 2},
        "12": {"source_stem": "a_docx", "is_subchunk": True, "parent_id": "10", "sub_order": 1},
    }
    out = ic.collect_mindmap_input(_write_meta(tmp_path, meta), ["a_docx"])
    assert len(out["chunks"]) == 1
    c = out["chunks"][0]
    assert c["text"] == "t10\n\nt12\n\nt11"          # cha + sub theo sub_order
    assert c["chunk_keys"] == ["10", "12", "11"]


def test_tree_sections_included(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: "t")
    monkeypatch.setattr(ic, "_load_tree_sections", lambda stems: [{"title": "Tổng quan", "chunk_refs": ["0"]}])
    meta = {"0": {"source_stem": "a_docx"}}
    out = ic.collect_mindmap_input(_write_meta(tmp_path, meta), ["a_docx"])
    assert out["tree_sections"] == [{"title": "Tổng quan", "chunk_refs": ["0"]}]


def test_title_single_vs_multi(tmp_path, monkeypatch):
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: "t")
    p = _write_meta(tmp_path, {"0": {"source_stem": "bao_cao_docx"}})
    assert ic.collect_mindmap_input(p, ["bao_cao.docx"])["title"] == "bao_cao"
    out = ic.collect_mindmap_input(p, ["a.docx", "b.docx", "c.docx", "d.docx"])
    assert out["title"].startswith("Tổng hợp:")


def test_orphan_subgroup_becomes_own_chunk_excluding_empty(tmp_path, monkeypatch):
    # parent "99" KHÔNG có trong meta → sub-group mồ côi; sub 21 rỗng text phải bị loại khỏi chunk_keys
    monkeypatch.setattr(chunk_text_store, "get_text", lambda cid: "" if cid == 21 else f"t{cid}")
    meta = {
        "20": {"source_stem": "a_docx", "is_subchunk": True, "parent_id": "99", "sub_order": 2},
        "21": {"source_stem": "a_docx", "is_subchunk": True, "parent_id": "99", "sub_order": 1},
        "22": {"source_stem": "a_docx", "is_subchunk": True, "parent_id": "99", "sub_order": 3},
    }
    out = ic.collect_mindmap_input(_write_meta(tmp_path, meta), ["a_docx"])
    assert len(out["chunks"]) == 1
    c = out["chunks"][0]
    assert c["key"] == "99"
    assert c["text"] == "t20\n\nt22"          # sub_order 1 rỗng bị bỏ, 2 rồi 3
    assert c["chunk_keys"] == ["20", "22"]     # KHÔNG chứa "21" (text rỗng)
