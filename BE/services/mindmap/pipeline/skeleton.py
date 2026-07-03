"""Stage 0 — skeleton deterministic (0 LLM): heading_path → tree_sections → TF-IDF clusters."""
from __future__ import annotations

from services.mindmap.pipeline.schema import sanitize_nodes

_SEP = " > "  # khớp chunking.py::_heading_path


def _root(title: str) -> dict:
    return {"id": "n0", "parent": None, "kind": "root", "title": title or "Mind Map",
            "note": "", "chunk_refs": [], "order": 0}


def _from_headings(title: str, chunks: list[dict]) -> list[dict] | None:
    if not any((c.get("heading_path") or "").strip() for c in chunks):
        return None
    root = _root(title)
    nodes = [root]
    by_path: dict[tuple, dict] = {}
    counter = 0
    for c in chunks:
        hp = (c.get("heading_path") or "").strip()
        parts = tuple(p.strip() for p in hp.split(_SEP) if p.strip()) if hp else ("Nội dung khác",)
        parent_id = root["id"]
        for depth in range(1, len(parts) + 1):
            key = parts[:depth]
            node = by_path.get(key)
            if node is None:
                counter += 1
                node = {"id": f"n{counter}", "parent": parent_id,
                        "kind": "section" if depth == 1 else "idea",
                        "title": parts[depth - 1], "note": "", "chunk_refs": [],
                        "order": len([n for n in nodes if n["parent"] == parent_id])}
                by_path[key] = node
                nodes.append(node)
            parent_id = node["id"]
        # chunk provenance gắn vào node SÂU NHẤT của path
        by_path[parts]["chunk_refs"].extend(c.get("chunk_keys") or [])
    return nodes


def _from_tree_sections(title: str, sections: list[dict]) -> list[dict] | None:
    sections = [s for s in (sections or []) if (s.get("title") or "").strip()]
    if not sections:
        return None
    root = _root(title)
    nodes = [root]
    for i, s in enumerate(sections):
        nodes.append({"id": f"n{i + 1}", "parent": root["id"], "kind": "section",
                      "title": s["title"].strip(), "note": "",
                      "chunk_refs": [str(r) for r in (s.get("chunk_refs") or [])], "order": i})
    return nodes


def _from_clusters(title: str, chunks: list[dict]) -> list[dict] | None:
    texts = [(c.get("text") or "").strip() for c in chunks]
    texts_idx = [i for i, t in enumerate(texts) if t]
    if len(texts_idx) < 4:
        return None
    try:
        from sklearn.cluster import KMeans
        from sklearn.feature_extraction.text import TfidfVectorizer
        vec = TfidfVectorizer(max_features=2000)
        X = vec.fit_transform([texts[i] for i in texts_idx])
        k = min(6, max(2, len(texts_idx) // 3))
        km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(X)
        terms = vec.get_feature_names_out()
        root = _root(title)
        nodes = [root]
        for ci in range(k):
            top = km.cluster_centers_[ci].argsort()[::-1][:3]
            label = " / ".join(terms[t] for t in top) or f"Chủ đề {ci + 1}"
            refs: list[str] = []
            for j, lab in enumerate(km.labels_):
                if lab == ci:
                    refs.extend(chunks[texts_idx[j]].get("chunk_keys") or [])
            nodes.append({"id": f"n{ci + 1}", "parent": root["id"], "kind": "section",
                          "title": label, "note": "", "chunk_refs": refs, "order": ci})
        return nodes
    except Exception:
        return None


def build_skeleton(mm_input: dict) -> tuple[list[dict], str]:
    title = (mm_input.get("title") or "Mind Map").strip()
    chunks = mm_input.get("chunks") or []
    for fn, method in ((_from_headings, "headings"),
                       (lambda t, _c: _from_tree_sections(t, mm_input.get("tree_sections")), "tree_sections"),
                       (_from_clusters, "clusters")):
        nodes = fn(title, chunks)
        if nodes and len(nodes) > 1:
            return sanitize_nodes(nodes), method
    return sanitize_nodes([_root(title)]), "single"
