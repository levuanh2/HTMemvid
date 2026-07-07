from services.mindmap.pipeline import schema as s

def _nodes():
    return [
        {"id": "n1", "parent": None, "kind": "root", "title": "Doc"},
        {"id": "n2", "parent": "n1", "kind": "section", "title": "A", "chunk_refs": ["1"]},
        {"id": "n3", "parent": "n2", "kind": "idea", "title": "a1"},
    ]

def test_content_hash_stable_and_order_insensitive():
    h1 = s.content_hash(["b", "a"], ["t1", "t2"])
    h2 = s.content_hash(["a", "b"], ["t1", "t2"])
    assert h1 == h2 and len(h1) == 64
    assert s.content_hash(["a"], ["t1"]) != s.content_hash(["a"], ["KHÁC"])

def test_sanitize_orphan_reparented_to_root_and_dedup():
    nodes = _nodes() + [
        {"id": "n9", "parent": "KHONG_TON_TAI", "kind": "idea", "title": "mồ côi"},
        {"id": "n2", "parent": "n1", "kind": "section", "title": "A trùng id"},
    ]
    out = s.sanitize_nodes(nodes)
    ids = [n["id"] for n in out]
    assert ids.count("n2") == 1
    orphan = next(n for n in out if n["id"] == "n9")
    assert orphan["parent"] == "n1"  # về root

def test_sanitize_caps_total_keeps_root_sections_first():
    nodes = [{"id": "root", "parent": None, "kind": "root", "title": "R"}]
    for i in range(5):
        nodes.append({"id": f"s{i}", "parent": "root", "kind": "section", "title": f"S{i}"})
    for i in range(300):
        nodes.append({"id": f"i{i}", "parent": f"s{i % 5}", "kind": "idea", "title": f"I{i}"})
    out = s.sanitize_nodes(nodes)
    assert len(out) <= s.MAX_NODES
    kinds = {n["kind"] for n in out}
    assert "root" in kinds and "section" in kinds

def test_validate_relations_drops_bad_and_caps():
    nodes = _nodes()
    rels = [
        {"source": "n2", "target": "n3", "type": "leads_to", "label": "dẫn tới"},   # trùng cạnh cây (n3.parent=n2) → bỏ
        {"source": "n2", "target": "n2", "type": "relates_to", "label": ""},        # self-loop → bỏ
        {"source": "n2", "target": "XX", "type": "relates_to", "label": ""},        # id lạ → bỏ
        {"source": "n3", "target": "n1", "type": "kind_la", "label": ""},           # type lạ → relates_to
    ]
    out = s.validate_relations(rels, nodes)
    assert len(out) == 1 and out[0]["type"] == "relates_to"

def test_build_record_shape():
    rec = s.build_record(title="T", sources=["a"], nodes=_nodes(), relations=[],
                         content_hash_value="x" * 64, model="m", elapsed_sec=1.5,
                         degraded_missing=["relations"])
    assert rec["schema_version"] == 2
    assert rec["generator"]["degraded"] is True
    assert rec["generator"]["missing"] == ["relations"]
    assert rec["content_hash"] == "x" * 64
    assert rec["id"] and rec["created_at"].endswith("Z")


def test_content_hash_sensitive_to_headings():
    # Re-ingest phục hồi heading_path (text không đổi) phải ra hash MỚI,
    # nếu không cache trả mãi bản mindmap nông cũ.
    base = s.content_hash(["a"], ["t1"], [""])
    with_heading = s.content_hash(["a"], ["t1"], ["1. Giới thiệu"])
    assert base != with_heading
    # backward-compat: không truyền headings == truyền toàn rỗng? KHÔNG — arg
    # None bỏ qua khối \x02, phải bằng chính nó và ổn định
    assert s.content_hash(["a"], ["t1"]) == s.content_hash(["a"], ["t1"])


def test_build_record_carries_skeleton_method():
    rec = s.build_record(title="T", sources=["a"], nodes=[], relations=[],
                         content_hash_value="h", model="m", elapsed_sec=1.0,
                         degraded_missing=[], skeleton_method="llm_outline")
    assert rec["generator"]["skeleton_method"] == "llm_outline"
    rec2 = s.build_record(title="T", sources=["a"], nodes=[], relations=[],
                          content_hash_value="h", model="m", elapsed_sec=1.0,
                          degraded_missing=[])
    assert rec2["generator"]["skeleton_method"] == ""
