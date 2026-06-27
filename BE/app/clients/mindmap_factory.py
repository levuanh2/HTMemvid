from __future__ import annotations

from shared.config import get_settings


def get_mindmap_runner():
    settings = get_settings()
    if settings.mindmap_service_addr:
        from app.clients.mindmap_client import run_mindmap_generation_via_grpc

        return run_mindmap_generation_via_grpc

    from services.mindmap.worker import run_mindmap_generation

    return run_mindmap_generation
