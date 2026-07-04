from __future__ import annotations

from concurrent import futures
import json
import os
import queue
import threading

import grpc

from app.clients.mindmap_factory import LocalMindmapPipeline
from shared.proto.gen import mindmap_pb2, mindmap_pb2_grpc

_SENTINEL = object()


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
        """Stream progress AS IT HAPPENS: enrich() runs on a worker thread, progress_cb
        puts events on a queue, this generator yields from the queue until the worker
        finishes, then yields the final event. (Previously buffered everything into a
        list during the synchronous call and yielded it all at once afterwards —
        defeated `stream EnrichEvent`.)"""
        mm_input = json.loads(request.mm_input_json or "{}")
        skeleton_nodes = json.loads(request.skeleton_json or "[]")
        events: "queue.Queue" = queue.Queue()
        result_box: dict = {}

        def progress_cb(progress: int, message: str) -> None:
            events.put(
                mindmap_pb2.EnrichEvent(
                    progress=int(progress),
                    message=message or "",
                    final=False,
                )
            )

        def cancel_cb() -> bool:
            return not context.is_active()

        def _run() -> None:
            try:
                nodes, degraded = self._pipeline.enrich(
                    mm_input,
                    skeleton_nodes,
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                )
                result_box["nodes"] = nodes
                result_box["degraded"] = degraded
            except Exception as exc:  # pragma: no cover - defensive
                result_box["error"] = exc
            finally:
                events.put(_SENTINEL)

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()

        while True:
            item = events.get()
            if item is _SENTINEL:
                break
            yield item

        worker.join()
        if "error" in result_box:
            raise result_box["error"]
        yield mindmap_pb2.EnrichEvent(
            nodes_json=json.dumps(result_box["nodes"], ensure_ascii=False),
            degraded=bool(result_box["degraded"]),
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
