from __future__ import annotations

import os

from shared.config import get_settings


class LocalMindmapPipeline:
    def _model(self) -> str:
        return os.getenv("MINDMAP_MODEL", "qwen2.5:14b").strip() or "qwen2.5:14b"

    def _timeout(self) -> float:
        return float(os.getenv("MINDMAP_LLM_TIMEOUT_SEC", "120"))

    def skeleton(self, mm_input):
        from services.mindmap.pipeline.skeleton import build_skeleton
        return build_skeleton(mm_input)

    def enrich(self, mm_input, skeleton_nodes, progress_cb=None, cancel_cb=None):
        from services.mindmap.pipeline.enrich import enrich_branches
        return enrich_branches(mm_input, skeleton_nodes, model=self._model(),
                               timeout_sec=self._timeout(),
                               max_workers=int(os.getenv("MINDMAP_ENRICH_PARALLEL", "2")),
                               progress_cb=progress_cb, cancel_cb=cancel_cb)

    def relations(self, nodes, cancel_cb=None):
        from services.mindmap.pipeline.relations import extract_relations
        return extract_relations(nodes, model=self._model(),
                                 timeout_sec=self._timeout(), cancel_cb=cancel_cb)


def get_mindmap_pipeline():
    settings = get_settings()
    if settings.mindmap_service_addr:
        try:
            from app.clients.mindmap_client import GrpcMindmapPipeline  # Task 15
            return GrpcMindmapPipeline(settings.mindmap_service_addr)
        except Exception:
            pass
    return LocalMindmapPipeline()
