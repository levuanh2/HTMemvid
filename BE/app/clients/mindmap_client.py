from __future__ import annotations

import json
import os
from pathlib import Path

import grpc

from shared.config import get_settings
from shared.proto.gen import mindmap_pb2, mindmap_pb2_grpc


class GrpcMindmapPipeline:
    """# Sẽ thay bằng GrpcMindmapPipeline (per-stage RPC) ở task sau."""

    def __init__(self, addr: str | None = None) -> None:
        self.addr = (addr or get_settings().mindmap_service_addr or "").strip()

    def _addr(self) -> str:
        if not self.addr:
            raise RuntimeError("MINDMAP_SERVICE_ADDR is not configured")
        return self.addr

    def skeleton(self, mm_input: dict) -> tuple[list[dict], str]:
        record = self._generate(mm_input)
        return list(record.get("nodes") or []), "grpc-placeholder"

    def enrich(self, mm_input, skeleton_nodes, progress_cb=None, cancel_cb=None):
        if cancel_cb is not None and cancel_cb():
            return list(skeleton_nodes or []), True
        return list(skeleton_nodes or []), True

    def relations(self, nodes, cancel_cb=None):
        if cancel_cb is not None and cancel_cb():
            return [], True
        return [], True

    def _generate(self, mm_input: dict) -> dict:
        source_names = list(mm_input.get("sources") or [])
        index_meta_path = str(mm_input.get("index_meta_path") or "")
        channel = grpc.insecure_channel(self._addr())
        stub = mindmap_pb2_grpc.MindmapServiceStub(channel)
        try:
            for event in stub.Generate(
                mindmap_pb2.GenerateRequest(
                    index_meta_path=index_meta_path,
                    source_names=source_names,
                    strategy="auto",
                    generation_mode="",
                )
            ):
                kind = event.WhichOneof("event")
                if kind == "result":
                    return json.loads(event.result.record_json or "{}")
                if kind == "error":
                    raise RuntimeError((event.error or "").strip() or "mindmap service error")
        finally:
            channel.close()
        raise RuntimeError("mindmap service stream ended without result")
