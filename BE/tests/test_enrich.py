"""Test làm giàu chunk (D3)."""
import os

import shared.config as cfg
from app.domains.ingest import enrich


def _reload(**env):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    cfg.reload()


def test_attach_metadata():
    _reload(DOC_CATEGORY="ytế", CONTEXTUAL_EMBEDDINGS="0", HYPO_QA="0")
    m = enrich.attach_metadata("This is an English sentence about data.", source="a.pdf", heading_path="Doc > Sec 1")
    assert m["source"] == "a.pdf"
    assert m["category"] == "ytế"
    assert m["heading_path"] == "Doc > Sec 1"
    assert m["language"]  # langdetect trả mã ngôn ngữ
    assert "date" in m


def test_contextual_toggle():
    _reload(CONTEXTUAL_EMBEDDINGS="0")
    assert enrich.contextualize("chunk", "doc", ask=lambda *a, **k: "X") == "chunk"
    _reload(CONTEXTUAL_EMBEDDINGS="1")
    out = enrich.contextualize("chunk", "doc", ask=lambda *a, **k: "Đoạn này thuộc mục A.")
    assert out.startswith("Đoạn này thuộc mục A.")
    assert "chunk" in out
    _reload(CONTEXTUAL_EMBEDDINGS="0")  # khôi phục


def test_hypo_qa_toggle():
    _reload(HYPO_QA="0")
    assert enrich.hypothetical_qa("chunk", ask=lambda *a, **k: "- Q1?") == ""
    _reload(HYPO_QA="1")
    out = enrich.hypothetical_qa("chunk", ask=lambda *a, **k: "- Q1?\n- Q2?")
    assert "Q1?" in out
    _reload(HYPO_QA="0")  # khôi phục
