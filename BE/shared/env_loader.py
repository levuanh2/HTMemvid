from __future__ import annotations

import os
from pathlib import Path


def load_project_env(*, override: bool = False) -> None:
    """
    Load environment variables from BE/.env (same dir as this file) first,
    then fall back to project root ../.env.

    Priority: BE/.env > ../.env > os.environ (when override=False).
    This makes BE self-contained while preserving root .env for docker-compose.
    """
    if (os.getenv("SKIP_DOTENV") or "").strip() in {"1", "true", "True", "yes", "on"}:
        return

    try:
        from dotenv import load_dotenv
    except Exception:
        return

    from shared.paths import BE_ROOT

    # 1. Load BE/.env first — higher priority (neo theo BE_ROOT, không theo __file__)
    be_env = BE_ROOT / ".env"
    if be_env.exists():
        load_dotenv(dotenv_path=be_env, override=override)

    # 2. Fall back to project root ../.env (for docker-compose compatibility)
    root_env = BE_ROOT.parent / ".env"
    if root_env.exists() and root_env.resolve() != be_env.resolve():
        load_dotenv(dotenv_path=root_env, override=override)

    _apply_memvid_langchain_defaults()


def _apply_memvid_langchain_defaults() -> None:
    """
    Mặc định bật pipeline LangChain/LangGraph (có thể ghi đè trong .env).
    Đặt MEMVID_DISABLE_LC_DEFAULTS=1 để không set default.
    """
    if (os.getenv("MEMVID_DISABLE_LC_DEFAULTS") or "").strip().lower() in ("1", "true", "yes", "on"):
        return
    os.environ.setdefault("USE_LC_VECTOR_STORE", "1")
    os.environ.setdefault("USE_LC_QA_CHAIN", "1")
    os.environ.setdefault("USE_LC_ENSEMBLE", "1")
    os.environ.setdefault("USE_LC_INGEST", "1")

