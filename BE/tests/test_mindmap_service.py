import os

os.environ["SKIP_MODEL_LOAD"] = "1"

from app.clients.mindmap_client import consume_generate_events
from shared.proto.gen import mindmap_pb2


def test_consume_generate_events_reports_progress_and_result():
    progress_calls = []
    events = iter(
        [
            mindmap_pb2.GenerateEvent(
                progress=mindmap_pb2.Progress(percent=42, message="working")
            ),
            mindmap_pb2.GenerateEvent(
                result=mindmap_pb2.MindmapResult(
                    record_json='{"id":"mm-1","title":"Demo","nodes":[{"id":"root","parent":null,"title":"Demo"}]}'
                )
            ),
        ]
    )

    record = consume_generate_events(events, progress_cb=progress_calls.append)

    assert progress_calls == [42]
    assert record["id"] == "mm-1"
    assert record["nodes"][0]["title"] == "Demo"
