from __future__ import annotations

from concurrent import futures
import os

import grpc

from shared.proto.gen import mindmap_pb2_grpc


class MindmapService(mindmap_pb2_grpc.MindmapServiceServicer):
    def Generate(self, request, context):
        context.set_code(grpc.StatusCode.UNIMPLEMENTED)
        context.set_details("Legacy mindmap worker was removed; use the skeleton-first pipeline.")
        return iter(())


def serve() -> None:
    port = int((os.getenv("MINDMAP_SERVICE_PORT") or "50052").strip() or "50052")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    mindmap_pb2_grpc.add_MindmapServiceServicer_to_server(MindmapService(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    print(f"[mindmap-service] listening on :{port}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
