from services.mindmap.pipeline.skeleton import build_skeleton


def _mk(chunks=None, sections=None, title="Tài liệu X"):
    return {"title": title, "sources": ["x"], "chunks": chunks or [], "tree_sections": sections or []}


def test_headings_build_tree_in_document_order():
    chunks = [
        {"key": "0", "text": "mở đầu", "heading_path": "1. Giới thiệu", "chunk_keys": ["0"]},
        {"key": "1", "text": "chi tiết pp", "heading_path": "2. Phương pháp > 2.1 Thu thập", "chunk_keys": ["1"]},
        {"key": "2", "text": "so khớp", "heading_path": "2. Phương pháp > 2.2 Xử lý", "chunk_keys": ["2"]},
        {"key": "3", "text": "kết quả", "heading_path": "3. Kết quả", "chunk_keys": ["3"]},
    ]
    nodes, method = build_skeleton(_mk(chunks))
    assert method == "headings"
    root = next(n for n in nodes if n["kind"] == "root")
    assert root["title"] == "Tài liệu X"
    secs = [n for n in nodes if n["kind"] == "section" and n["parent"] == root["id"]]
    assert [s["title"] for s in secs] == ["1. Giới thiệu", "2. Phương pháp", "3. Kết quả"]
    pp = next(s for s in secs if s["title"] == "2. Phương pháp")
    kids = [n for n in nodes if n["parent"] == pp["id"]]
    assert [k["title"] for k in kids] == ["2.1 Thu thập", "2.2 Xử lý"]
    thu_thap = kids[0]
    assert thu_thap["chunk_refs"] == ["1"]  # provenance từ chunk_keys


def test_fallback_tree_sections_when_no_headings():
    chunks = [{"key": "0", "text": "abc", "heading_path": "", "chunk_keys": ["0"]},
              {"key": "1", "text": "def", "heading_path": "", "chunk_keys": ["1"]}]
    sections = [{"title": "Phần A", "chunk_refs": ["0"]},
                {"title": "Phần B", "chunk_refs": ["1"]}]
    nodes, method = build_skeleton(_mk(chunks, sections))
    assert method == "tree_sections"
    assert any(n["title"] == "Phần A" and n["kind"] == "section" for n in nodes)


def test_single_tree_section_is_rejected_as_filler():
    # 1 section duy nhất ("Tổng quan tài liệu" size-based) = filler, không phải
    # cấu trúc — phải rơi tiếp xuống clusters/single thay vì giữ node vô nghĩa.
    chunks = [{"key": "0", "text": "abc", "heading_path": "", "chunk_keys": ["0"]}]
    sections = [{"title": "Tổng quan tài liệu", "chunk_refs": ["0"]}]
    nodes, method = build_skeleton(_mk(chunks, sections))
    assert method == "single"
    assert len(nodes) == 1 and nodes[0]["kind"] == "root"


def test_fallback_clusters_when_nothing_else(monkeypatch):
    chunks = [{"key": str(i), "text": f"máy học mô hình dữ liệu huấn luyện số {i}", "heading_path": "", "chunk_keys": [str(i)]} for i in range(8)]
    nodes, method = build_skeleton(_mk(chunks))
    assert method in ("clusters", "single")
    assert any(n["kind"] == "root" for n in nodes)
    assert any(n["kind"] == "section" for n in nodes)


def test_empty_input_returns_root_only():
    nodes, method = build_skeleton(_mk())
    assert method == "single"
    assert len(nodes) == 1 and nodes[0]["kind"] == "root"
