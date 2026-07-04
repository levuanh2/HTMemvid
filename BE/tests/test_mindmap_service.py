from __future__ import annotations

from concurrent import futures
import json

import grpc

from app.clients.mindmap_client import GrpcMindmapPipeline
from app.clients.mindmap_factory import get_mindmap_pipeline
from services.mindmap.server import MindmapPipelineService
from shared.config import reload
from shared.proto.gen import mindmap_pb2, mindmap_pb2_grpc


MM_INPUT = {
    "title": "Doc",
    "sources": ["s1"],
    "chunks": [{"text": "alpha", "chunk_keys": ["c1"]}],
    "tree_sections": [],
}
SKELETON_NODES = [
    {"id": "n0", "parent": None, "kind": "root", "title": "Doc", "note": "", "chunk_refs": [], "order": 0},
    {"id": "n1", "parent": "n0", "kind": "section", "title": "Overview", "note": "", "chunk_refs": ["c1"], "order": 0},
]
ENRICHED_NODES = SKELETON_NODES + [
    {"id": "n2", "parent": "n1", "kind": "idea", "title": "Detail", "note": "extra", "chunk_refs": ["c1"], "order": 0},
]


class _FakePipeline:
    def skeleton(self, mm_input):
        assert mm_input == MM_INPUT
        return SKELETON_NODES, "headings"

    def enrich(self, mm_input, skeleton_nodes, progress_cb=None, cancel_cb=None):
        assert mm_input == MM_INPUT
        assert skeleton_nodes == SKELETON_NODES
        assert cancel_cb is not None
        assert cancel_cb() is False
        if progress_cb is not None:
            progress_cb(55, "enriching")
        return ENRICHED_NODES, False

    def relations(self, nodes, cancel_cb=None):
        assert nodes == ENRICHED_NODES
        assert cancel_cb is not None
        assert cancel_cb() is False
        return [], False


def _start_test_server() -> tuple[grpc.Server, str]:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    mindmap_pb2_grpc.add_MindmapPipelineServicer_to_server(
        MindmapPipelineService(pipeline=_FakePipeline()),
        server,
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, f"127.0.0.1:{port}"


def test_mindmap_service_stage_rpcs(monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    server, addr = _start_test_server()
    channel = grpc.insecure_channel(addr)
    stub = mindmap_pb2_grpc.MindmapPipelineStub(channel)
    try:
        skeleton = stub.Skeleton(
            mindmap_pb2.MindmapInput(mm_input_json=json.dumps(MM_INPUT, ensure_ascii=False))
        )
        assert json.loads(skeleton.nodes_json) == SKELETON_NODES
        assert skeleton.method == "headings"

        events = list(
            stub.EnrichBranches(
                mindmap_pb2.EnrichRequest(
                    mm_input_json=json.dumps(MM_INPUT, ensure_ascii=False),
                    skeleton_json=json.dumps(SKELETON_NODES, ensure_ascii=False),
                )
            )
        )
        assert len(events) == 2
        assert events[0].progress == 55
        assert events[0].message == "enriching"
        assert events[0].final is False
        assert json.loads(events[-1].nodes_json) == ENRICHED_NODES
        assert events[-1].degraded is False
        assert events[-1].final is True

        relations = stub.Relations(
            mindmap_pb2.RelationsRequest(
                nodes_json=json.dumps(ENRICHED_NODES, ensure_ascii=False),
            )
        )
        assert relations.relations_json == "[]"
        assert relations.degraded is False
    finally:
        channel.close()
        server.stop(None).wait()


def test_grpc_mindmap_pipeline_matches_local_interface(monkeypatch):
    monkeypatch.setenv("SKIP_MODEL_LOAD", "1")
    server, addr = _start_test_server()
    monkeypatch.setenv("MINDMAP_SERVICE_ADDR", addr)
    reload()
    pipeline = get_mindmap_pipeline()
    progress_events = []
    try:
        assert isinstance(pipeline, GrpcMindmapPipeline)

        nodes, method = pipeline.skeleton(MM_INPUT)
        assert nodes == SKELETON_NODES
        assert method == "headings"

        enriched, degraded = pipeline.enrich(
            MM_INPUT,
            SKELETON_NODES,
            progress_cb=lambda progress, message: progress_events.append((progress, message)),
            cancel_cb=lambda: False,
        )
        assert progress_events == [(55, "enriching")]
        assert enriched == ENRICHED_NODES
        assert degraded is False

        relations, degraded_rel = pipeline.relations(ENRICHED_NODES, cancel_cb=lambda: False)
        assert relations == []
        assert degraded_rel is False
    finally:
        pipeline.close()
        server.stop(None).wait()
