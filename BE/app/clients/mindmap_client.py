from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable, Optional

import grpc

from shared.config import get_settings
from shared.proto.gen import mindmap_pb2, mindmap_pb2_grpc


def consume_generate_events(
    events: Iterable[mindmap_pb2.GenerateEvent],
    *,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> dict:
    for event in events:
        kind = event.WhichOneof("event")
        if kind == "progress":
            if progress_cb is not None:
                progress_cb(int(event.progress.percent))
            continue
        if kind == "result":
            return json.loads(event.result.record_json or "{}")
        if kind == "error":
            raise RuntimeError((event.error or "").strip() or "mindmap service error")
    raise RuntimeError("mindmap service stream ended without result")


def run_mindmap_generation_via_grpc(
    index_meta_path: Path,
    source_names: list[str],
    strategy_requested: str = "auto",
    append_mindmap: Callable[[dict], None] | None = None,
    progress_cb: Optional[Callable[[int], None]] = None,
    generation_mode: str | None = None,
) -> dict:
    addr = get_settings().mindmap_service_addr
    if not addr:
        raise RuntimeError("MINDMAP_SERVICE_ADDR is not configured")

    channel = grpc.insecure_channel(addr)
    stub = mindmap_pb2_grpc.MindmapServiceStub(channel)
    try:
        record = consume_generate_events(
            stub.Generate(
                mindmap_pb2.GenerateRequest(
                    index_meta_path=str(index_meta_path),
                    source_names=list(source_names or []),
                    strategy=strategy_requested or "auto",
                    generation_mode=generation_mode or "",
                )
            ),
            progress_cb=progress_cb,
        )
    finally:
        channel.close()

    if append_mindmap is not None:
        append_mindmap(record)
    return record
