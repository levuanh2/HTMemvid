from __future__ import annotations

import json
import os
import queue
import threading
from concurrent import futures
from pathlib import Path

import grpc

from app.domains.jobs import jobs_store
from services.mindmap.worker import attach_mindmap_job_context, run_mindmap_generation
from shared.proto.gen import mindmap_pb2, mindmap_pb2_grpc

_PATCH_LOCK = threading.Lock()
_STREAM_DONE = object()


class MindmapService(mindmap_pb2_grpc.MindmapServiceServicer):
    def Generate(self, request, context):
        events: "queue.Queue[object]" = queue.Queue()
        result_box: dict[str, object] = {}

        def _emit_progress(percent: int, message: str) -> None:
            events.put(
                mindmap_pb2.GenerateEvent(
                    progress=mindmap_pb2.Progress(
                        percent=int(percent),
                        message=message or "",
                    )
                )
            )

        def _worker() -> None:
            with _PATCH_LOCK:
                original_update_job = jobs_store.update_job
                progress_cb = None

                def _capture_update_job(job_id: str, **kwargs) -> None:
                    if request.job_id and job_id != request.job_id:
                        return
                    if "progress" in kwargs or "current_node" in kwargs:
                        _emit_progress(
                            int(kwargs.get("progress", 0) or 0),
                            str(kwargs.get("current_node") or ""),
                        )

                try:
                    jobs_store.update_job = _capture_update_job
                    attach_mindmap_job_context(request.job_id or None)
                    if not request.job_id:
                        progress_cb = lambda p: _emit_progress(int(p), "")
                    result_box["record"] = run_mindmap_generation(
                        Path(request.index_meta_path),
                        list(request.source_names),
                        request.strategy or "auto",
                        append_mindmap=lambda _record: None,
                        progress_cb=progress_cb,
                        generation_mode=request.generation_mode or None,
                    )
                except Exception as exc:
                    result_box["error"] = str(exc)
                finally:
                    jobs_store.update_job = original_update_job
                    attach_mindmap_job_context(None)
                    events.put(_STREAM_DONE)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        while True:
            item = events.get()
            if item is _STREAM_DONE:
                break
            yield item

        err = str(result_box.get("error") or "").strip()
        if err:
            yield mindmap_pb2.GenerateEvent(error=err)
            return

        record = result_box.get("record")
        yield mindmap_pb2.GenerateEvent(
            result=mindmap_pb2.MindmapResult(
                record_json=json.dumps(record or {}, ensure_ascii=False)
            )
        )


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
