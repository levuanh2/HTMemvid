import importlib
import os
import time
from pathlib import Path

import pytest


class _MockQueryGraph:
    def invoke(self, state, config=None, **_kwargs):
        return {"payload": {"answer": "mock answer"}, "status_code": 200}


class _MockMindmapGraph:
    def invoke(self, state, config=None, **_kwargs):
        return {
            "result": {
                "id": "mock",
                "title": "mock mindmap",
                "nodes": [{"id": "root", "parent": None, "title": "mock mindmap"}],
                "sources": state.get("source_names") or [],
                "createdAt": time.time(),
                "strategy": state.get("strategy") or "iterative",
            },
            "status_code": 200,
        }


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    """
    Smoke-test client cho các endpoint hiện tại.

    - Set env trước khi import để `llm_factory.PROVIDERS` build đúng.
    - Patch các tác vụ nặng (ingest/query/mindmap) để test không phụ thuộc model/FAISS/OCR.
    """
    data_dir = tmp_path_factory.mktemp("data_dir")
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["SKIP_MODEL_LOAD"] = "1"
    os.environ["USE_SQLITE_JOBS"] = "0"
    os.environ["MEMVID_DISABLE_LC_DEFAULTS"] = "1"

    # Ưu tiên provider local (để không yêu cầu GEMINI/GROQ keys trong test)
    os.environ["OLLAMA_HOST"] = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    os.environ.pop("GEMINI_API_KEY", None)
    os.environ.pop("GROQ_API_KEY", None)

    # Import sau khi set env
    import app.clients.llm_factory as llm_factory  # noqa: F401
    import app.main as be_main

    # Reload để đảm bảo PROVIDERS đúng theo env hiện tại
    importlib.reload(llm_factory)
    importlib.reload(be_main)

    # Phase 5: luôn có graph giả để không gọi pipeline legacy (đã xóa).
    be_main.QUERY_GRAPH = _MockQueryGraph()
    be_main.MINDMAP_GRAPH = _MockMindmapGraph()

    # --- Patch các pipeline nặng ---
    def _fast_ingest(source_id: str, file_path: str, filename: str):
        reg = be_main._load_source_registry()
        if source_id in reg:
            reg[source_id]["status"] = "index_ready"
            reg[source_id]["progress"] = 1.0
            reg[source_id]["capabilities"] = {"chunk_query": True}
        be_main._save_source_registry(reg)

    be_main._trigger_background_ingest = lambda sid, fp, fn: _fast_ingest(sid, fp, fn)

    # Đảm bảo thư mục input tồn tại trong DATA_DIR tmp
    Path(be_main.INPUT_DIR).mkdir(parents=True, exist_ok=True)

    with be_main.app.test_client() as c:
        yield c
