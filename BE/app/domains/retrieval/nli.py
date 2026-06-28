"""
NLI / contradiction-check — khử trùng context TRƯỚC khi sinh đáp án.

Vì sao cần: bi-encoder (embedding) có "điểm mù" — cosine cao nhưng nghĩa ngược
(phủ định "được nghỉ" vs "không được nghỉ", đổi thực thể, thời gian/con số cũ-mới).
Rerank cross-encoder giúp một phần, nhưng KHÔNG bắt được hai chunk *mâu thuẫn nhau*
cùng lọt vào context. Tầng NLI dùng mDeBERTa chấm cặp (premise, hypothesis) →
{entailment, neutral, contradiction}, phát hiện cặp chunk xung đột rồi để node
VerifyContext hạ/loại chunk hạng thấp, giữ chunk hạng cao.

Thiết kế (mirror app.domains.retrieval.rerank):
  - lazy-load + cache model theo model-name, guard SKIP_MODEL_LOAD,
  - MỌI lỗi load/predict → trả "không phát hiện xung đột" (passthrough), KHÔNG
    bao giờ làm vỡ pipeline truy hồi.

Dùng `transformers` + `torch` (đã có sẵn) — không thêm dep nặng (chỉ `sentencepiece`
cho tokenizer DebertaV2, là leaf-dep, xem .playbook).
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable

from shared.config import get_settings

logger = logging.getLogger(__name__)

# 3 nhãn NLI chuẩn (chuẩn hoá lowercase khi đọc id2label của model).
_LABELS = ("entailment", "neutral", "contradiction")


@runtime_checkable
class NliEngine(Protocol):
    def predict(self, pairs: List[Tuple[str, str]]) -> List[Dict[str, float]]:
        """Mỗi (premise, hypothesis) → {entailment, neutral, contradiction} (prob)."""
        ...


class NullNli:
    """Passthrough an toàn: mọi cặp là 'neutral' → không có xung đột nào."""

    def predict(self, pairs: List[Tuple[str, str]]) -> List[Dict[str, float]]:
        return [{"entailment": 0.0, "neutral": 1.0, "contradiction": 0.0} for _ in pairs]


class MDebertaNli:
    """mDeBERTa multilingual NLI qua transformers (self-host, offline, CPU)."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._tok = None
        self._model = None
        self._id2label: Dict[int, str] = {}

    def _ensure_model(self):
        if self._model is None:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._tok = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            self._model.eval()
            raw = getattr(self._model.config, "id2label", None) or {}
            # Chuẩn hoá: index -> nhãn lowercase ('ENTAILMENT' → 'entailment').
            self._id2label = {int(i): str(lbl).strip().lower() for i, lbl in raw.items()}
        return self._model

    def predict(self, pairs: List[Tuple[str, str]]) -> List[Dict[str, float]]:
        if not pairs:
            return []
        import torch

        model = self._ensure_model()
        premises = [(p or "")[:2000] for p, _ in pairs]
        hypotheses = [(h or "")[:2000] for _, h in pairs]
        inputs = self._tok(
            premises, hypotheses,
            return_tensors="pt", truncation=True, padding=True, max_length=512,
        )
        with torch.no_grad():
            logits = model(**inputs).logits
        probs = torch.softmax(logits, dim=-1).tolist()

        out: List[Dict[str, float]] = []
        for row in probs:
            scores = {lbl: 0.0 for lbl in _LABELS}
            for idx, p in enumerate(row):
                lbl = self._id2label.get(idx)
                if lbl in scores:
                    scores[lbl] = float(p)
            out.append(scores)
        return out


# --- Factory (cache theo model-name) ---
_nli_cache: Optional[NliEngine] = None
_nli_key: Optional[str] = None
_NULL = NullNli()


def get_nli() -> NliEngine:
    """Singleton. SKIP_MODEL_LOAD=1, NLI tắt, hoặc lỗi build → NullNli."""
    global _nli_cache, _nli_key

    if os.getenv("SKIP_MODEL_LOAD") == "1":
        return _NULL

    s = get_settings()
    if not s.nli_enabled:
        return _NULL

    key = s.nli_model
    if _nli_cache is not None and _nli_key == key:
        return _nli_cache

    try:
        _nli_cache = MDebertaNli(s.nli_model)
    except Exception as exc:  # pragma: no cover - phòng thủ
        logger.warning("get_nli: build thất bại (%s) → null.", exc)
        _nli_cache = _NULL
    _nli_key = key
    return _nli_cache


def classify(premise: str, hypothesis: str) -> Dict[str, float]:
    """Chấm 1 cặp; mọi lỗi → neutral (không xung đột)."""
    try:
        out = get_nli().predict([(premise, hypothesis)])
        if out:
            return out[0]
    except Exception as exc:
        logger.warning("classify: predict thất bại (%s) → neutral.", exc)
    return {"entailment": 0.0, "neutral": 1.0, "contradiction": 0.0}


def _candidate_pairs(n: int, max_pairs: int) -> List[Tuple[int, int]]:
    """Sinh cặp (i<j) ưu tiên các chunk hạng cao trước (tổng index nhỏ), cắt max_pairs."""
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    pairs.sort(key=lambda p: (p[0] + p[1], p[1]))
    if max_pairs and max_pairs > 0:
        return pairs[:max_pairs]
    return pairs


def detect_conflicts(
    chunks: List[str],
    *,
    max_pairs: int = 10,
    threshold: float = 0.6,
) -> List[Dict[str, object]]:
    """
    Trả danh sách cặp chunk MÂU THUẪN: [{'i': int, 'j': int, 'score': float}] với i<j
    (i là hạng cao hơn vì chunks đã được xếp hạng). Mỗi cặp chấm cả 2 chiều, lấy
    prob 'contradiction' lớn nhất. Mọi lỗi → [] (không xung đột → passthrough).
    """
    texts = [c for c in (chunks or []) if isinstance(c, str)]
    if len(texts) < 2:
        return []
    try:
        cand = _candidate_pairs(len(texts), max_pairs)
        if not cand:
            return []
        # Chấm 2 chiều cùng 1 batch để tận dụng vectorhoá.
        directed: List[Tuple[str, str]] = []
        for i, j in cand:
            directed.append((texts[i], texts[j]))
            directed.append((texts[j], texts[i]))
        scored = get_nli().predict(directed)
        if not scored:
            return []

        conflicts: List[Dict[str, object]] = []
        for k, (i, j) in enumerate(cand):
            c1 = scored[2 * k].get("contradiction", 0.0)
            c2 = scored[2 * k + 1].get("contradiction", 0.0)
            best = max(float(c1), float(c2))
            if best >= threshold:
                conflicts.append({"i": i, "j": j, "score": best})
        return conflicts
    except Exception as exc:
        logger.warning("detect_conflicts: thất bại (%s) → không xung đột.", exc)
        return []


def resolve_conflicts(n: int, conflicts: List[Dict[str, object]]) -> List[int]:
    """
    Cho n chunk (đã xếp hạng) + danh sách cặp xung đột, trả về index NÊN GIỮ theo
    thứ tự cũ: với mỗi cặp (i<j) loại j (hạng thấp hơn). Idempotent, giữ ổn định.
    """
    drop = {int(c["j"]) for c in conflicts if int(c["j"]) > int(c["i"])}
    return [idx for idx in range(n) if idx not in drop]


def reset_cache() -> None:
    """Cho test sau khi đổi env (giống config.reload)."""
    global _nli_cache, _nli_key
    _nli_cache = None
    _nli_key = None
