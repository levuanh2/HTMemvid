from __future__ import annotations

import json

import grpc

from shared.config import get_settings
from shared.proto.gen import mindmap_pb2, mindmap_pb2_grpc


class GrpcMindmapPipeline:
    def __init__(self, addr: str | None = None) -> None:
        self.addr = (addr or get_settings().mindmap_service_addr or "").strip()
        self._channel: grpc.Channel | None = None
        self._stub: mindmap_pb2_grpc.MindmapPipelineStub | None = None

    def _addr(self) -> str:
        if not self.addr:
            raise RuntimeError("MINDMAP_SERVICE_ADDR is not configured")
        return self.addr

    def _client(self) -> mindmap_pb2_grpc.MindmapPipelineStub:
        if self._stub is None:
            self._channel = grpc.insecure_channel(self._addr())
            self._stub = mindmap_pb2_grpc.MindmapPipelineStub(self._channel)
        return self._stub

    def skeleton(self, mm_input: dict) -> tuple[list[dict], str]:
        reply = self._client().Skeleton(
            mindmap_pb2.MindmapInput(
                mm_input_json=json.dumps(mm_input, ensure_ascii=False),
            )
        )
        return json.loads(reply.nodes_json or "[]"), reply.method or ""

    def enrich(self, mm_input, skeleton_nodes, progress_cb=None, cancel_cb=None):
        if cancel_cb is not None and cancel_cb():
            return list(skeleton_nodes or []), True
        final_event = None
        for event in self._client().EnrichBranches(
            mindmap_pb2.EnrichRequest(
                mm_input_json=json.dumps(mm_input, ensure_ascii=False),
                skeleton_json=json.dumps(skeleton_nodes, ensure_ascii=False),
            )
        ):
            if event.final:
                final_event = event
                break
            if progress_cb is not None:
                progress_cb(int(event.progress), event.message or "")
        if final_event is None:
            raise RuntimeError("mindmap service stream ended without final event")
        return json.loads(final_event.nodes_json or "[]"), bool(final_event.degraded)

    def relations(self, nodes, cancel_cb=None):
        if cancel_cb is not None and cancel_cb():
            return [], True
        reply = self._client().Relations(
            mindmap_pb2.RelationsRequest(
                nodes_json=json.dumps(nodes, ensure_ascii=False),
            )
        )
        return json.loads(reply.relations_json or "[]"), bool(reply.degraded)

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
            self._channel = None
            self._stub = None
