from __future__ import annotations

import os
import threading
import time
from concurrent import futures
from contextlib import contextmanager
from typing import Iterator, Optional

import grpc

import app.clients.llm_factory as llm_factory
from app.clients.local_providers import ProviderPool
from shared.config import get_settings
from shared.proto.gen import common_pb2, llm_pb2, llm_pb2_grpc

_OFFLINE_EMBED_DIM = 384


# === Phase 2: global LLM concurrency cap (DR-3 D2) ===
# The gateway is a single process through which ALL generation funnels onto ONE
# CPU Ollama. Bound how many generations run at once here, in ONE place. Overflow
# waits a bounded time then fails with a controlled busy error the caller already
# degrades on (job error, never empty). Limiter only — never changes correctness.
class LlmBusyError(Exception):
    """All LLM slots busy past the wait timeout. Caller treats as a transient failure."""


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


_llm_gate_lock = threading.Lock()
_llm_active = 0  # in-flight generations, for observability only (semaphore is the real gate)

# === PR#4: query-priority lanes ===
# Lane suy từ AskRequest.feature CÓ SẴN trong proto (không cần metadata/proto change):
# batch pipeline luôn gửi feature tường minh ("summary"/"mindmap" — kể cả từ
# mindmap-service ngoài process); "answer" = sinh đáp án RAG tương tác. Feature
# thiếu/lạ ("chat", "") → batch (an toàn: memory-tree BUILD dùng default "chat").
# Bật lanes: batch chỉ được dùng max_calls - reserved slot → luôn còn slot cho
# query, và hàng đợi Ollama không bao giờ bị batch chiếm kín.
_QUERY_LANE_FEATURES = frozenset({"answer"})


def _lanes_enabled() -> bool:
    return (os.getenv("LLM_PRIORITY_LANES_ENABLED", "false") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _load_llm_gate() -> tuple[threading.BoundedSemaphore, threading.BoundedSemaphore, int, int, float]:
    max_calls = max(1, _env_int("MAX_CONCURRENT_LLM_CALLS", 2))
    wait = max(0.0, _env_float("LLM_QUEUE_WAIT_TIMEOUT_SECONDS", 30.0))
    # reserved kẹp [0, max_calls-1] — batch lane không bao giờ dưới 1 slot.
    reserved = min(max(0, _env_int("LLM_RESERVED_QUERY_SLOTS", 1)), max_calls - 1)
    batch_max = max(1, max_calls - reserved)
    return (
        threading.BoundedSemaphore(max_calls),
        threading.BoundedSemaphore(batch_max),
        max_calls,
        batch_max,
        wait,
    )


_llm_semaphore, _batch_semaphore, _LLM_MAX, _BATCH_MAX, _LLM_WAIT = _load_llm_gate()


def configure_llm_gate(max_calls: int | None = None, wait_timeout: float | None = None) -> None:
    """Rebuild the process-global gate from env (or explicit values). Ops/test hook —
    NOT called per request. Resets the in-flight counter."""
    global _llm_semaphore, _batch_semaphore, _LLM_MAX, _BATCH_MAX, _LLM_WAIT, _llm_active
    if max_calls is not None:
        os.environ["MAX_CONCURRENT_LLM_CALLS"] = str(max_calls)
    if wait_timeout is not None:
        os.environ["LLM_QUEUE_WAIT_TIMEOUT_SECONDS"] = str(wait_timeout)
    with _llm_gate_lock:
        _llm_semaphore, _batch_semaphore, _LLM_MAX, _BATCH_MAX, _LLM_WAIT = _load_llm_gate()
        _llm_active = 0


def _log(event: str, **kv: object) -> None:
    parts = " ".join(f"{k}={v}" for k, v in kv.items())
    print(f"llm_gateway {event} {parts}".rstrip(), flush=True)


@contextmanager
def _llm_slot(label: str, feature: str = ""):
    """Acquire one global generation slot (bounded wait) or raise LlmBusyError.
    Always releases in finally. Retries go through the same slot (no bypass).

    PR#4 lanes (LLM_PRIORITY_LANES_ENABLED): batch (summary/mindmap/unknown) phải
    lấy batch-slot (max_calls - reserved) TRƯỚC global slot; query ("answer") chỉ
    lấy global → batch không bao giờ chiếm hết slot, query luôn còn đường chạy.
    Tắt (mặc định) → hành vi cũ y hệt: một semaphore global."""
    global _llm_active
    lanes = _lanes_enabled()
    lane = "query" if feature in _QUERY_LANE_FEATURES else "batch"
    t0 = time.time()
    _log("llm_semaphore_waiting", label=label, lane=lane, feature=feature or "unknown",
         active=_llm_active, max=_LLM_MAX)
    batch_held = False
    if lanes and lane == "batch":
        if not _batch_semaphore.acquire(timeout=_LLM_WAIT):
            _log("llm_semaphore_timeout", label=label, lane=lane, waited=_LLM_WAIT,
                 batch_max=_BATCH_MAX)
            raise LlmBusyError(
                f"LLM gateway busy: all {_BATCH_MAX} batch slots in use, waited {_LLM_WAIT}s"
            )
        batch_held = True
    remaining = max(0.0, _LLM_WAIT - (time.time() - t0))
    if not _llm_semaphore.acquire(timeout=remaining):
        if batch_held:
            _batch_semaphore.release()
        _log("llm_semaphore_timeout", label=label, lane=lane, waited=_LLM_WAIT, max=_LLM_MAX)
        raise LlmBusyError(
            f"LLM gateway busy: all {_LLM_MAX} slots in use, waited {_LLM_WAIT}s"
        )
    with _llm_gate_lock:
        _llm_active += 1
        active = _llm_active
    _log("llm_semaphore_acquired", label=label, lane=lane,
         waited_ms=round((time.time() - t0) * 1000, 1), active=active, max=_LLM_MAX)
    started = time.time()
    try:
        _log("llm_generation_started", label=label, lane=lane)
        yield
        _log("llm_generation_finished", label=label, lane=lane,
             elapsed_s=round(time.time() - started, 1))
    finally:
        _llm_semaphore.release()
        if batch_held:
            _batch_semaphore.release()
        with _llm_gate_lock:
            _llm_active -= 1
            active = _llm_active
        _log("llm_semaphore_released", label=label, lane=lane, active=active)


def _options_to_dict(options: common_pb2.LlmOptions) -> Optional[dict]:
    out: dict[str, float | int] = {}
    if options.num_predict:
        out["num_predict"] = options.num_predict
    if options.temperature:
        out["temperature"] = options.temperature
    if options.num_ctx:
        out["num_ctx"] = options.num_ctx
    return out or None


class LlmGatewayService(llm_pb2_grpc.LlmGatewayServicer):
    def __init__(self, pool: Optional[ProviderPool] = None):
        self._pool = pool or ProviderPool()

    def Ask(self, request: llm_pb2.AskRequest, context) -> llm_pb2.AskResponse:
        try:
            with _llm_slot("Ask", feature=request.feature or ""):
                text = self._pool.ask(
                    request.prompt,
                    system_prompt=request.system_prompt or None,
                    model=request.model or None,
                    options=_options_to_dict(request.options),
                    feature=request.feature or "chat",
                    timeout=request.timeout_sec or None,
                )
        except LlmBusyError as exc:
            # Controlled busy signal — backend job logic marks this as error, never empty/hang.
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(exc))
        return llm_pb2.AskResponse(
            text=text,
            provider_used=self._pool.last_provider_used or "",
        )

    def AskStream(
        self, request: llm_pb2.AskRequest, context
    ) -> Iterator[llm_pb2.Token]:
        from langchain_core.messages import HumanMessage, SystemMessage

        # Build the LLM object (cheap, no generation) BEFORE taking a slot.
        llm = llm_factory.get_llm(request.feature or "chat")
        messages = (
            [SystemMessage(content=request.system_prompt), HumanMessage(content=request.prompt)]
            if request.system_prompt
            else [HumanMessage(content=request.prompt)]
        )
        try:
            with _llm_slot("AskStream", feature=request.feature or ""):
                for piece in llm_factory.stream_chat_tokens(llm, messages):
                    yield llm_pb2.Token(text=piece)
        except LlmBusyError as exc:
            context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(exc))

    def Embed(self, request: llm_pb2.EmbedRequest, context) -> llm_pb2.EmbedResponse:
        texts = list(request.texts)
        model_name = request.model_name or None
        if os.getenv("SKIP_MODEL_LOAD") == "1":
            vectors = [
                llm_pb2.FloatVector(values=[0.0] * _OFFLINE_EMBED_DIM) for _ in texts
            ]
            return llm_pb2.EmbedResponse(vectors=vectors, dim=_OFFLINE_EMBED_DIM)

        model = llm_factory.get_embedding_model(model_name)
        if model is None:
            vectors = [
                llm_pb2.FloatVector(values=[0.0] * _OFFLINE_EMBED_DIM) for _ in texts
            ]
            return llm_pb2.EmbedResponse(vectors=vectors, dim=_OFFLINE_EMBED_DIM)

        encoded = model.encode(texts, convert_to_numpy=True)
        vectors = [
            llm_pb2.FloatVector(values=row.astype("float32").tolist()) for row in encoded
        ]
        dim = int(encoded.shape[1]) if len(texts) else 0
        return llm_pb2.EmbedResponse(vectors=vectors, dim=dim)

    def GetProviders(
        self, request: common_pb2.Empty, context
    ) -> llm_pb2.ProvidersResponse:
        return llm_pb2.ProvidersResponse(
            providers=self._pool.providers,
            embedding_model=get_settings().embedding_model_name,
        )

    def SetProviders(
        self, request: llm_pb2.ProvidersResponse, context
    ) -> llm_pb2.ProvidersResponse:
        providers = self._pool.set_providers(list(request.providers))
        embedding_model = request.embedding_model or get_settings().embedding_model_name
        return llm_pb2.ProvidersResponse(
            providers=providers,
            embedding_model=embedding_model,
        )


def serve() -> grpc.Server:
    port = int((os.getenv("LLM_GATEWAY_PORT") or "50051").strip() or "50051")
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    llm_pb2_grpc.add_LlmGatewayServicer_to_server(LlmGatewayService(), server)
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    return server


if __name__ == "__main__":
    serve().wait_for_termination()
