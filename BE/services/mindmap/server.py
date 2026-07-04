from __future__ import annotations

from concurrent import futures
import json
import os

import grpc

from app.clients.mindmap_factory import LocalMindmapPipeline
from shared.proto.gen import mindmap_pb2, mindmap_pb2_grpc


class MindmapPipelineService(mindmap_pb2_grpc.MindmapPipelineServicer):
    def __init__(self, pipeline: LocalMindmapPipeline | None = None) -> None:
        self._pipeline = pipeline or LocalMindmapPipeline()

    def Skeleton(self, request, context):
        mm_input = json.loads(request.mm_input_json or "{}")
        nodes, method = self._pipeline.skeleton(mm_input)
        return mindmap_pb2.SkeletonReply(
            nodes_json=json.dumps(nodes, ensure_ascii=False),
            method=method,
        )

    def EnrichBranches(self, request, context):
        mm_input = json.loads(request.mm_input_json or "{}")
        skeleton_nodes = json.loads(request.skeleton_json or "[]")
        events: list[mindmap_pb2.EnrichEvent] = []

        def progress_cb(progress: int, message: str) -> None:
            if context.is_active():
                events.append(
                    mindmap_pb2.EnrichEvent(
                        progress=int(progress),
                        message=message or "",
                        final=False,
                    )
                )

        def cancel_cb() -> bool:
            return not context.is_active()

        nodes, degraded = self._pipeline.enrich(
            mm_input,
            skeleton_nodes,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
        for event in events:
            yield event
        yield mindmap_pb2.EnrichEvent(
            nodes_json=json.dumps(nodes, ensure_ascii=False),
            degraded=bool(degraded),
            final=True,
        )

    def Relations(self, request, context):
        nodes = json.loads(request.nodes_json or "[]")
        relations, degraded = self._pipeline.relations(
            nodes,
            cancel_cb=lambda: not context.is_active(),
        )
        return mindmap_pb2.RelationsReply(
            relations_json=json.dumps(relations, ensure_ascii=False),
            degraded=bool(degraded),
        )


def serve() -> None:
    port = int((os.getenv("MINDMAP_SERVICE_PORT") or "50052").strip() or "50052")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    mindmap_pb2_grpc.add_MindmapPipelineServicer_to_server(MindmapPipelineService(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"[mindmap-service] listening on :{port}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
