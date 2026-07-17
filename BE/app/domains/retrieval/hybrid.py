from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import faiss
from rank_bm25 import BM25Okapi

from app.clients.llm_factory import DEFAULT_MODEL_NAME, encode_query_cached, get_embedding_model
from app.domains.vectorstore.store import _use_lc_vector_store
from shared.source_id import canonical_source_stem
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
    # \u0110\u1ecbnh danh canonical D\u00d9NG CHUNG (shared.source_id) \u2014 kh\u1edbp \u0111\u00fang c\u00e1ch ingest \u0111\u1eb7t
    # t\u00ean video_path (sanitize space/\u0111\u1eb7c bi\u1ec7t \u2192 '_'), n\u00ean selected \u2194 chunk lu\u00f4n tr\u00f9ng.
    return canonical_source_stem(name)


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
        # PR#7: chunk_id -> chunk, build MỘT lần cùng _chunks — thay next()-scan
        # O(n) mỗi FAISS hit và dict-build O(n) mỗi query.
        self._by_id: dict[int, RetrievedChunk] = {}
        self._bm25: BM25Okapi | None = None
        self._bm25_tokens: list[list[str]] = []
        # PR#7: cache faiss index legacy per-instance, key mtime+size — hết
        # faiss.read_index từ đĩa mỗi query; append/delete ghi lại file → tự reload.
        self._faiss_idx = None
        self._faiss_key: tuple | None = None

    def _load_faiss_index(self):
        """Đọc index.faiss có cache theo (mtime_ns, size). File đổi → reload;
        đọc lỗi → raise như cũ (caller đã bọc try/except + log)."""
        st = self.index_path.stat()
        key = (st.st_mtime_ns, st.st_size)
        if self._faiss_idx is not None and self._faiss_key == key:
            return self._faiss_idx
        idx = faiss.read_index(str(self.index_path))
        self._faiss_idx = idx
        self._faiss_key = key
        return idx

    def _ensure_loaded(self) -> None:
        if not self.meta_path.exists():
            self._chunks = []
            self._by_id = {}
            self._bm25 = None
            self._bm25_tokens = []
            return

        import app.domains.vectorstore.chunk_text_store as chunk_text_store
        mtime = max(self.meta_path.stat().st_mtime, chunk_text_store.mtime())
        if self._meta_mtime is not None and mtime == self._meta_mtime and self._bm25 is not None:
            return

        with open(self.meta_path, encoding="utf-8") as f:
            meta = json.load(f) or {}

        texts = dict(chunk_text_store.iter_all())
        chunks: list[RetrievedChunk] = []
        for k, v in meta.items():
            if not isinstance(k, str) or not k.isdigit():
                continue
            if not isinstance(v, dict):
                continue
            cid = int(k)
            text = (texts.get(cid) or v.get("text") or "").strip()
            if not text:
                continue
            # Ưu tiên canonical source_stem ghi sẵn (chunk mới); fallback suy từ
            # video_path (chunk cũ) — cả hai qua cùng canonicalizer nên khớp selected.
            video_stem = _norm_stem(v.get("source_stem") or v.get("video") or "")
            chunks.append(RetrievedChunk(
                chunk_id=cid,
                text=text,
                video_stem=video_stem,
                category=(v.get("category") or None),
                language=(v.get("language") or None),
            ))

        chunks.sort(key=lambda c: c.chunk_id)
        tokens = [_tokenize(c.text) for c in chunks]

        self._chunks = chunks
        self._by_id = {c.chunk_id: c for c in chunks}
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
                if load_vectorstore(use_cache=True) is not None:
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
                    idx = self._load_faiss_index()
                    _, I = idx.search(qv, 10)
                    # PR#7: aliases tính MỘT lần ngoài vòng hit; lookup qua _by_id
                    # thay next()-scan O(n) mỗi hit. Kết quả y hệt.
                    norms, bases = _selected_stem_aliases(selected_sources) if selected_sources else (set(), set())
                    for iid in I[0].tolist():
                        if iid == -1:
                            continue
                        cid = int(iid)
                        if selected_sources:
                            found = self._by_id.get(cid)
                            if found and _chunk_visible_for_sources(found.video_stem, norms, bases):
                                faiss_ids.append(cid)
                        else:
                            faiss_ids.append(cid)
                except Exception as exc:
                    logger.warning("HybridRetriever.retrieve: legacy FAISS search failed: %s", exc)

        merged_ids = _rrf_merge(faiss_ids, bm25_ids, top_k=top_k)

        out = [self._by_id[cid] for cid in merged_ids if cid in self._by_id]
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
                vs = load_vectorstore(use_cache=True)
                if vs is not None:
                    fetch_k = max(30, top_k * 5)
                    pairs = vs.similarity_search_with_score(query, k=fetch_k)
                    norms, bases = _selected_stem_aliases(selected_sources) if selected_sources else (set(), set())
                    by_id = self._by_id
                    out: list[RetrievedChunk] = []
                    for d, dist in pairs:
                        stem = _norm_stem(d.metadata.get("source_stem") or d.metadata.get("video") or "")
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
            idx = self._load_faiss_index()
            D, I = idx.search(qv, max(10, top_k * 2))
        except Exception as exc:
            logger.warning("HybridRetriever.retrieve_faiss_only: legacy FAISS read/search failed: %s", exc)
            return []

        # PR#7: aliases một lần + _by_id lookup thay next()-scan — kết quả y hệt.
        norms, bases = _selected_stem_aliases(selected_sources) if selected_sources else (set(), set())
        raw: list[tuple[float, int]] = []
        for dist, iid in zip(D[0].tolist(), I[0].tolist()):
            if iid == -1:
                continue
            cid = int(iid)
            if selected_sources:
                found = self._by_id.get(cid)
                if found and _chunk_visible_for_sources(found.video_stem, norms, bases):
                    raw.append((float(dist), cid))
            else:
                raw.append((float(dist), cid))

        out_legacy: list[RetrievedChunk] = []
        for dist, cid in raw:
            if cid not in self._by_id:
                continue
            base = self._by_id[cid]
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
