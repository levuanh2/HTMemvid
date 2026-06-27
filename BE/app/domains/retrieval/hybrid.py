from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import faiss
from rank_bm25 import BM25Okapi

from app.clients.llm_factory import DEFAULT_MODEL_NAME, encode_query_cached, get_embedding_model
from app.domains.vectorstore.store import _use_lc_vector_store
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    text: str
    video_stem: str
    bm25_score: float | None = None
    vector_score: float | None = None
    category: str | None = None
    language: str | None = None


def _meta_match(c: "RetrievedChunk", category: str | None, language: str | None) -> bool:
    if category and (c.category or "").lower() != category.lower():
        return False
    if language and (c.language or "").lower() != language.lower():
        return False
    return True


def _norm_stem(name: str) -> str:
    try:
        cleaned = unicodedata.normalize("NFKD", (name or "").strip()).replace("\u00a0", " ")
        cleaned = Path(cleaned).name
        return Path(cleaned).stem.lower()
    except Exception:
        return (name or "").strip().lower()


# Giống memory_tree._normalize_video_stem: bỏ hậu tố _YYYYMMDD_HHMMSS để khớp stem ngắn (registry) ↔ tên video đã index (có timestamp).
_STEM_TS_SUFFIX = re.compile(r"_\d{8}_\d{6}$")


def _stem_base(norm_stem: str) -> str:
    return _STEM_TS_SUFFIX.sub("", norm_stem or "")


def _selected_stem_aliases(selected_sources: Iterable[str]) -> tuple[set[str], set[str]]:
    norms = {_norm_stem(s) for s in selected_sources if (s or "").strip()}
    bases = {_stem_base(n) for n in norms if n}
    return norms, bases


def _chunk_visible_for_sources(chunk_stem_norm: str, norms: set[str], bases: set[str]) -> bool:
    if not norms:
        return True
    if chunk_stem_norm in norms:
        return True
    cb = _stem_base(chunk_stem_norm)
    return bool(cb) and cb in bases


def _tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    return re.findall(r"[\w\-À-ỹ]{2,}", text)


def _rrf_merge(
    a_ids: list[int],
    b_ids: list[int],
    *,
    k: int = 60,
    top_k: int = 6,
) -> list[int]:
    scores: dict[int, float] = {}
    for rank, cid in enumerate(a_ids, start=1):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    for rank, cid in enumerate(b_ids, start=1):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [cid for cid, _ in merged[:top_k]]


class HybridRetriever:
    """
    Hybrid retrieval: FAISS semantic + BM25 keyword, merged by RRF.
    Offline-capable.
    """

    def __init__(self, *, index_path: Path, meta_path: Path):
        self.index_path = Path(index_path)
        self.meta_path = Path(meta_path)

        self._meta_mtime: float | None = None
        self._chunks: list[RetrievedChunk] = []
        self._bm25: BM25Okapi | None = None
        self._bm25_tokens: list[list[str]] = []

    def _ensure_loaded(self) -> None:
        if not self.meta_path.exists():
            self._chunks = []
            self._bm25 = None
            self._bm25_tokens = []
            return

        mtime = self.meta_path.stat().st_mtime
        if self._meta_mtime is not None and mtime == self._meta_mtime and self._bm25 is not None:
            return

        with open(self.meta_path, encoding="utf-8") as f:
            meta = json.load(f) or {}

        chunks: list[RetrievedChunk] = []
        for k, v in meta.items():
            if not isinstance(k, str) or not k.isdigit():
                continue
            if not isinstance(v, dict):
                continue
            text = (v.get("text") or "").strip()
            if not text:
                continue
            video_stem = _norm_stem(v.get("video") or "")
            chunks.append(RetrievedChunk(
                chunk_id=int(k),
                text=text,
                video_stem=video_stem,
                category=(v.get("category") or None),
                language=(v.get("language") or None),
            ))

        chunks.sort(key=lambda c: c.chunk_id)
        tokens = [_tokenize(c.text) for c in chunks]

        self._chunks = chunks
        self._bm25_tokens = tokens
        self._bm25 = BM25Okapi(tokens) if chunks else None
        self._meta_mtime = mtime

    def _filter_by_sources(self, selected_sources: Iterable[str] | None) -> list[int]:
        if not selected_sources:
            return list(range(len(self._chunks)))
        norms, bases = _selected_stem_aliases(selected_sources)
        return [
            i
            for i, c in enumerate(self._chunks)
            if _chunk_visible_for_sources(c.video_stem, norms, bases)
        ]

    def retrieve(
        self,
        query: str,
        *,
        selected_sources: list[str] | None = None,
        top_k: int = 6,
        category: str | None = None,
        language: str | None = None,
    ) -> list[RetrievedChunk]:
        if os.getenv("SKIP_MODEL_LOAD") == "1":
            return []

        self._ensure_loaded()
        if not self._chunks:
            return []

        allowed_idx = self._filter_by_sources(selected_sources)
        # Lọc thêm theo metadata (category/language) nếu được yêu cầu.
        if category or language:
            allowed_idx = [i for i in allowed_idx if _meta_match(self._chunks[i], category, language)]
        if not allowed_idx:
            return []

        bm25_ids: list[int] = []
        if self._bm25 is not None:
            q_tokens = _tokenize(query)
            scores = self._bm25.get_scores(q_tokens)
            ranked = sorted(((float(scores[i]), i) for i in allowed_idx), key=lambda x: x[0], reverse=True)
            bm25_ids = [self._chunks[i].chunk_id for _, i in ranked[: min(10, len(ranked))]]

        faiss_ids: list[int] = []
        if _use_lc_vector_store():
            try:
                from app.domains.vectorstore.store import load_vectorstore
                if load_vectorstore() is not None:
                    ch = self.retrieve_faiss_only(query, selected_sources=selected_sources, top_k=10)
                    faiss_ids = [c.chunk_id for c in ch]
            except Exception as exc:
                logger.warning("HybridRetriever.retrieve: LC vector / retrieve_faiss_only failed: %s", exc)
                faiss_ids = []
        if not faiss_ids and self.index_path.exists():
            model_name = os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
            qv = encode_query_cached(query, model_name)
            if qv is None:
                model = get_embedding_model(model_name)
                if model is not None:
                    qv = model.encode([query], convert_to_numpy=True).astype("float32")
            if qv is not None:
                try:
                    idx = faiss.read_index(str(self.index_path))
                    _, I = idx.search(qv, 10)
                    for iid in I[0].tolist():
                        if iid == -1:
                            continue
                        cid = int(iid)
                        if selected_sources:
                            norms, bases = _selected_stem_aliases(selected_sources)
                            found = next((c for c in self._chunks if c.chunk_id == cid), None)
                            if found and _chunk_visible_for_sources(found.video_stem, norms, bases):
                                faiss_ids.append(cid)
                        else:
                            faiss_ids.append(cid)
                except Exception as exc:
                    logger.warning("HybridRetriever.retrieve: legacy FAISS search failed: %s", exc)

        merged_ids = _rrf_merge(faiss_ids, bm25_ids, top_k=top_k)

        by_id = {c.chunk_id: c for c in self._chunks}
        out = [by_id[cid] for cid in merged_ids if cid in by_id]
        if category or language:
            out = [c for c in out if _meta_match(c, category, language)]
        return out[:top_k]

    def retrieve_bm25_only(
        self, query: str, *, selected_sources: list[str] | None = None, top_k: int = 6
    ) -> list[RetrievedChunk]:
        """Chỉ xếp hạng BM25 (để ghép EnsembleRetriever)."""
        if os.getenv("SKIP_MODEL_LOAD") == "1":
            return []

        self._ensure_loaded()
        if not self._chunks or self._bm25 is None:
            return []

        allowed_idx = self._filter_by_sources(selected_sources)
        if not allowed_idx:
            return []

        q_tokens = _tokenize(query)
        scores = self._bm25.get_scores(q_tokens)
        ranked = sorted(((float(scores[i]), i) for i in allowed_idx), key=lambda x: x[0], reverse=True)
        pick_n = min(max(top_k, 10), len(ranked))
        out: list[RetrievedChunk] = []
        for scr, i in ranked[:pick_n]:
            base = self._chunks[i]
            out.append(
                RetrievedChunk(
                    chunk_id=base.chunk_id,
                    text=base.text,
                    video_stem=base.video_stem,
                    bm25_score=float(scr),
                )
            )
        return out[:top_k]

    def retrieve_faiss_only(
        self, query: str, *, selected_sources: list[str] | None = None, top_k: int = 6
    ) -> list[RetrievedChunk]:
        """Chỉ xếp hạng vector FAISS (để ghép EnsembleRetriever)."""
        if os.getenv("SKIP_MODEL_LOAD") == "1":
            return []

        self._ensure_loaded()
        if not self._chunks:
            return []

        if _use_lc_vector_store():
            try:
                from app.domains.vectorstore.store import load_vectorstore
                vs = load_vectorstore()
                if vs is not None:
                    fetch_k = max(30, top_k * 5)
                    pairs = vs.similarity_search_with_score(query, k=fetch_k)
                    norms, bases = _selected_stem_aliases(selected_sources) if selected_sources else (set(), set())
                    by_id = {c.chunk_id: c for c in self._chunks}
                    out: list[RetrievedChunk] = []
                    for d, dist in pairs:
                        stem = _norm_stem(d.metadata.get("video") or "")
                        if norms and not _chunk_visible_for_sources(stem, norms, bases):
                            continue
                        cid = d.metadata.get("chunk_id")
                        if cid is None:
                            continue
                        cid = int(cid)
                        sc = float(dist)
                        if cid in by_id:
                            base = by_id[cid]
                            out.append(
                                RetrievedChunk(
                                    chunk_id=base.chunk_id,
                                    text=base.text,
                                    video_stem=base.video_stem,
                                    vector_score=sc,
                                )
                            )
                        else:
                            out.append(
                                RetrievedChunk(
                                    chunk_id=cid,
                                    text=(d.page_content or "").strip(),
                                    video_stem=stem,
                                    vector_score=sc,
                                )
                            )
                        if len(out) >= top_k:
                            break
                    return out[:top_k]
            except Exception as exc:
                logger.warning("HybridRetriever.retrieve_faiss_only: LC path failed: %s", exc)

        if not self.index_path.exists():
            return []

        allowed_idx = self._filter_by_sources(selected_sources)
        if not allowed_idx:
            return []

        model_name = os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME)
        qv = encode_query_cached(query, model_name)
        if qv is None:
            model = get_embedding_model(model_name)
            if model is None:
                return []
            qv = model.encode([query], convert_to_numpy=True).astype("float32")

        try:
            idx = faiss.read_index(str(self.index_path))
            D, I = idx.search(qv, max(10, top_k * 2))
        except Exception as exc:
            logger.warning("HybridRetriever.retrieve_faiss_only: legacy FAISS read/search failed: %s", exc)
            return []

        raw: list[tuple[float, int]] = []
        for dist, iid in zip(D[0].tolist(), I[0].tolist()):
            if iid == -1:
                continue
            cid = int(iid)
            if selected_sources:
                norms, bases = _selected_stem_aliases(selected_sources)
                found = next((c for c in self._chunks if c.chunk_id == cid), None)
                if found and _chunk_visible_for_sources(found.video_stem, norms, bases):
                    raw.append((float(dist), cid))
            else:
                raw.append((float(dist), cid))

        by_id = {c.chunk_id: c for c in self._chunks}
        out_legacy: list[RetrievedChunk] = []
        for dist, cid in raw:
            if cid not in by_id:
                continue
            base = by_id[cid]
            out_legacy.append(
                RetrievedChunk(
                    chunk_id=base.chunk_id,
                    text=base.text,
                    video_stem=base.video_stem,
                    vector_score=dist,
                )
            )
            if len(out_legacy) >= top_k:
                break
        return out_legacy[:top_k]
