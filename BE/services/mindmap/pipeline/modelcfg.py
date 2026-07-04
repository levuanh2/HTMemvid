"""Một nguồn sự thật cho model mindmap: MINDMAP_MODEL > SLM_MODEL > default."""
from __future__ import annotations
import os


def resolve_mindmap_model() -> str:
    for var in ("MINDMAP_MODEL", "SLM_MODEL"):
        v = (os.getenv(var) or "").strip()
        if v:
            return v
    return "qwen2.5:14b"
