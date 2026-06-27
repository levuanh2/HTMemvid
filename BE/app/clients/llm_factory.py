"""
LLM và embedding factory — LangChain (thay ai_provider / ollama_utils).

- get_llm(feature): một ChatModel theo kế hoạch migrate (Gemini > Groq > Ollama).
- ask_ai / summarize_*: giữ thứ tự PROVIDERS (fallback giữa provider như trước).
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from typing import Any, Iterator, Optional

import numpy as np

try:
    from shared.env_loader import load_project_env
    load_project_env(override=False)
except Exception:
    pass

PROVIDERS: list[str] = []

has_gemini = bool((os.getenv("GEMINI_API_KEY") or "").strip())
has_groq = bool((os.getenv("GROQ_API_KEY") or "").strip())
has_any_remote = has_gemini or has_groq

if (os.getenv("OLLAMA_HOST") or "").strip() or (not has_any_remote):
    PROVIDERS.append("ollama")

if has_gemini:
    PROVIDERS.append("gemini")

if has_groq:
    PROVIDERS.append("groq")

print("Active providers:", PROVIDERS)

# Ollama num_predict / Groq max_tokens — thấp quá thì cắt giữa câu; mặc định 8192 cho trả lời dài.
_DEFAULT_LLM_OUT = int((os.getenv("LLM_MAX_TOKENS") or "8192").strip() or "8192")


# === llm-gateway chokepoint ===
# Khi LLM_GATEWAY_ADDR được set, mọi lệnh gọi LLM/embedding của monolith (và của
# mindmap-service) đi qua llm-gateway gRPC thay vì gọi trực tiếp. Khi KHÔNG set
# (mặc định, test, và CHÍNH process llm-gateway), giữ nguyên hành vi cũ — nên tiến
# trình gateway không tự gọi lại chính nó.
_GRPC_LLM_CACHE: dict = {}
_GRPC_EMB_CACHE: dict = {}


def _gateway_addr() -> str:
    return (os.getenv("LLM_GATEWAY_ADDR") or "").strip()


def _grpc_llm_provider(addr: str):
    p = _GRPC_LLM_CACHE.get(addr)
    if p is None:
        from app.clients.llm_client import GrpcLLMProvider
        p = GrpcLLMProvider(addr)
        _GRPC_LLM_CACHE[addr] = p
    return p


def _grpc_embedding_provider(addr: str, name: str):
    key = (addr, name)
    p = _GRPC_EMB_CACHE.get(key)
    if p is None:
        from app.clients.llm_client import GrpcEmbeddingProvider
        p = GrpcEmbeddingProvider(addr, model_name=name)
        _GRPC_EMB_CACHE[key] = p
    return p


def _model_map(feature: str) -> str:
    # Mặc định qwen3.5:9b (Ollama). Không dùng qwen3:9b — tag không tồn tại.
    _chat = os.getenv("SLM_MODEL_CHAT", os.getenv("SLM_MODEL", "qwen3.5:9b"))
    return {
        "chat": _chat,
        "summary": os.getenv("SLM_MODEL_SUMMARY", "qwen2.5:14b"),
        "mindmap": os.getenv("MINDMAP_MODEL", "qwen2.5:14b"),
    }.get(feature, _chat)


def _invoke_chat(llm: Any, user: str, system_prompt: str | None, timeout: float | None = None) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    if system_prompt:
        msgs = [SystemMessage(content=system_prompt), HumanMessage(content=user)]
    else:
        msgs = [HumanMessage(content=user)]
    # ChatOllama mặc định stream=True — một số model (Qwen 3.x + think) có chunk content rỗng; stream=False lấy 1 response đầy đủ.

    def _call():
        return llm.invoke(msgs, stream=False)

    if timeout is not None and timeout > 0:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_call)
            try:
                out = fut.result(timeout=timeout)
            except FuturesTimeout:
                raise TimeoutError(f"LLM call timed out after {timeout:.0f}s") from None
    else:
        out = _call()
    return lc_ai_message_text(out).strip()


def _ollama_reasoning_param() -> Any:
    """Map OLLAMA_REASONING / OLLAMA_THINK -> ChatOllama reasoning (think=...). Empty = để None (behavior model)."""
    raw = (os.getenv("OLLAMA_REASONING") or os.getenv("OLLAMA_THINK") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def _ollama_chat_llm(model: str | None, feature: str, options: dict | None, timeout: float | None = None) -> Any:
    from langchain_ollama import ChatOllama

    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    m = model or _model_map(feature)
    kw: dict[str, Any] = {
        "model": m,
        "base_url": host,
        "temperature": float(os.getenv("LLM_TEMPERATURE", "0.3")),
        "num_predict": _DEFAULT_LLM_OUT,
        "num_ctx": int(os.getenv("LLM_CTX_SIZE", "4096")),
        "reasoning": _ollama_reasoning_param(),
    }
    if options:
        if "num_predict" in options:
            kw["num_predict"] = int(options["num_predict"])
        if "temperature" in options:
            kw["temperature"] = float(options["temperature"])
        if "num_ctx" in options:
            kw["num_ctx"] = int(options["num_ctx"])
    
    # IMPORTANT: Truyền timeout vào ChatOllama để HTTP request có timeout thật.
    # Nếu không truyền, HTTP request có thể treo vĩnh viễn nếu Ollama không respond.
    # ThreadPoolExecutor.fut.result(timeout=x) chỉ kill thread sau x giây,
    # NHƯNG HTTP request bên trong vẫn tiếp tục chạy và chiếm tài nguyên.
    if timeout is not None and timeout > 0:
        kw["timeout"] = timeout
    
    return ChatOllama(**kw)


def _gemini_chat_llm() -> Any:
    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY for Gemini provider.")
    model = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
    max_out = _DEFAULT_LLM_OUT
    try:
        return ChatGoogleGenerativeAI(
            model=model,
            api_key=api_key,
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
            max_output_tokens=max_out,
        )
    except TypeError:
        return ChatGoogleGenerativeAI(model=model, api_key=api_key, temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")))


def _groq_chat_llm() -> Any:
    from langchain_groq import ChatGroq

    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY for Groq provider.")
    model = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
    return ChatGroq(
        model=model,
        api_key=api_key,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
        max_tokens=_DEFAULT_LLM_OUT,
    )


def lc_message_content_text(content: Any) -> str:
    """Chuẩn hoá message.content (str | list blocks | None) thành một chuỗi — stream/invoke LC đôi khi trả list."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text") or block.get("content")
                if isinstance(t, str):
                    parts.append(t)
            else:
                tx = getattr(block, "text", None)
                if isinstance(tx, str):
                    parts.append(tx)
        return "".join(parts)
    return str(content)


def lc_ai_message_text(message: Any) -> str:
    """Lấy text hiển thị từ AIMessage — kể cả reasoning_content/thinking nếu content rỗng (Ollama think)."""
    base = lc_message_content_text(getattr(message, "content", None))
    if base.strip():
        return base
    ak = getattr(message, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "thinking"):
        v = ak.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return base


def lc_ai_chunk_text(chunk: Any) -> str:
    """Tương tự lc_ai_message_text cho chunk khi stream."""
    base = lc_message_content_text(getattr(chunk, "content", None))
    if base.strip():
        return base
    ak = getattr(chunk, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "thinking"):
        v = ak.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return base


def stream_chat_tokens(llm: Any, messages: list) -> Iterator[str]:
    """Yield nội dung token/chunk từ LangChain chat model (.stream)."""
    for chunk in llm.stream(messages):
        piece = lc_ai_chunk_text(chunk)
        if piece:
            yield piece


def get_llm(feature: str = "chat") -> Any:
    """
    LLM chính cho chain (LangChain) — luôn dùng Ollama local.
    feature: 'chat' -> SLM_MODEL_CHAT (mặc định qwen3.5:9b)
    """
    return _ollama_chat_llm(None, feature, None)


_emb_instance: Any = None
_emb_bound_name: str | None = None


def get_embeddings() -> Any:
    """
    Lazy singleton embeddings (HuggingFace hoặc Fake khi SKIP_MODEL_LOAD=1).
    """
    global _emb_instance, _emb_bound_name

    from langchain_core.embeddings import Embeddings

    if os.getenv("SKIP_MODEL_LOAD") == "1":
        from langchain_core.embeddings.fake import FakeEmbeddings

        return FakeEmbeddings(size=384)

    model_name = os.getenv("EMBEDDING_MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
    if _emb_instance is not None and _emb_bound_name == model_name:
        return _emb_instance

    from langchain_huggingface import HuggingFaceEmbeddings

    _emb_instance = HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True, "batch_size": 32},
    )
    _emb_bound_name = model_name
    assert isinstance(_emb_instance, Embeddings)
    return _emb_instance


_EMB_QUERY_VEC_CACHE: OrderedDict[str, np.ndarray] = OrderedDict()
QUERY_EMBED_CACHE_MAX = int(os.getenv("QUERY_EMBED_CACHE_MAX", "512"))


def clear_embeddings_cache() -> None:
    global _emb_instance, _emb_bound_name, _EMB_QUERY_VEC_CACHE
    _emb_instance = None
    _emb_bound_name = None
    _EMB_QUERY_VEC_CACHE.clear()


def encode_query_cached(query: str, model_name: Optional[str] = None) -> Optional[np.ndarray]:
    """
    Cache embedding vector cho một câu query (retrieve). LRU theo md5(query_normalized).
    Trả về float32 shape (1, dim) hoặc None (CI / lỗi).
    """
    global _EMB_QUERY_VEC_CACHE

    if os.getenv("SKIP_MODEL_LOAD") == "1":
        return None
    q = (query or "").strip()
    if not q:
        return None
    key = hashlib.md5(q.lower().encode("utf-8")).hexdigest()
    if key in _EMB_QUERY_VEC_CACHE:
        _EMB_QUERY_VEC_CACHE.move_to_end(key)
        return _EMB_QUERY_VEC_CACHE[key]

    m = get_embedding_model(model_name)
    if m is None:
        return None
    v = m.encode([q], convert_to_numpy=True).astype("float32")
    _EMB_QUERY_VEC_CACHE[key] = v
    _EMB_QUERY_VEC_CACHE.move_to_end(key)
    while len(_EMB_QUERY_VEC_CACHE) > QUERY_EMBED_CACHE_MAX:
        _EMB_QUERY_VEC_CACHE.popitem(last=False)
    return v


# --- Embedding adapter (.encode tương thích SentenceTransformer) ---
DEFAULT_EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MODEL_NAME = DEFAULT_EMBEDDING_MODEL_NAME


class LangChainEmbeddingAdapter:
    """Tương thích .encode() như SentenceTransformer cho index / memory_tree / mindmap."""

    def encode(
        self,
        texts: list[str] | str,
        convert_to_numpy: bool = True,
        batch_size: int = 32,
        show_progress_bar: bool = False,
        **kwargs: Any,
    ) -> Any:
        emb = get_embeddings()
        if isinstance(texts, str):
            v = emb.embed_query(texts)
            return np.array(v, dtype=np.float32)

        vecs: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            vecs.extend(emb.embed_documents(batch))
        out = np.array(vecs, dtype=np.float32)
        return out if convert_to_numpy else out.tolist()


_emb_adapter_cache: Optional[LangChainEmbeddingAdapter] = None
_emb_adapter_name: Optional[str] = None


def get_embedding_model(model_name: Optional[str] = None) -> Optional[LangChainEmbeddingAdapter]:
    """SKIP_MODEL_LOAD=1 → None. Đổi model_name → cập nhật env + clear cache embeddings."""
    global _emb_adapter_cache, _emb_adapter_name

    if os.getenv("SKIP_MODEL_LOAD") == "1":
        return None

    _addr = _gateway_addr()
    if _addr:
        # Embed qua llm-gateway (giữ MỘT model embedding cho cả hệ thống).
        _name = model_name or os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_EMBEDDING_MODEL_NAME)
        return _grpc_embedding_provider(_addr, _name)

    name = model_name or os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_EMBEDDING_MODEL_NAME)
    env_name = os.environ.get("EMBEDDING_MODEL_NAME", DEFAULT_EMBEDDING_MODEL_NAME)

    if name != env_name:
        os.environ["EMBEDDING_MODEL_NAME"] = name
        clear_embeddings_cache()
        _emb_adapter_cache = None
        _emb_adapter_name = None

    if _emb_adapter_cache is not None and _emb_adapter_name == name:
        return _emb_adapter_cache

    clear_embeddings_cache()
    get_embeddings()
    _emb_adapter_cache = LangChainEmbeddingAdapter()
    _emb_adapter_name = name
    return _emb_adapter_cache


def get_sentence_transformer(model_name: Optional[str] = None) -> LangChainEmbeddingAdapter:
    m = get_embedding_model(model_name)
    if m is None:
        raise RuntimeError("Embedding model not available (CI mode)")
    return m


def ask_ai(
    prompt: str,
    system_prompt: str | None = None,
    model: str | None = None,
    options: dict | None = None,
    feature: str = "chat",
    timeout: float | None = None,
) -> str:
    """Gọi AI qua Ollama. `feature` xác định model mặc định khi `model` không truyền.
    feature='chat'    -> SLM_MODEL_CHAT (mặc định qwen3.5:9b)
    feature='summary' -> qwen2.5:14b  (dùng cho summarize_advanced, memory_tree)
    feature='mindmap' -> qwen2.5:14b
    timeout: số giây tối đa cho LLM call (None = không giới hạn)
    """
    _addr = _gateway_addr()
    if _addr:
        # Định tuyến qua llm-gateway (fallback provider xử lý phía server).
        return _grpc_llm_provider(_addr).ask(
            prompt,
            system_prompt=system_prompt,
            model=model,
            options=options,
            feature=feature,
            timeout=timeout,
        )

    last_error: Exception | None = None

    # Nếu không truyền model cụ thể, tự động chọn theo feature
    effective_model = model or _model_map(feature)

    for provider in PROVIDERS:
        try:
            if provider == "ollama":
                llm = _ollama_chat_llm(effective_model, feature, options)
                return _invoke_chat(llm, prompt, system_prompt, timeout=timeout)

            if provider == "gemini":
                llm = _gemini_chat_llm()
                return _invoke_chat(llm, prompt, system_prompt, timeout=timeout)

            if provider == "groq":
                llm = _groq_chat_llm()
                return _invoke_chat(llm, prompt, system_prompt, timeout=timeout)
        except Exception as e:
            last_error = e
            continue

    if not PROVIDERS:
        raise RuntimeError(
            "No AI provider configured. Set OLLAMA_HOST for local Ollama, or set GEMINI_API_KEY/GROQ_API_KEY."
        )

    raise RuntimeError(f"All AI providers failed (tried {PROVIDERS}): {last_error}")


def summarize_whole_document(text: str, model: str | None = None) -> str:
    from langdetect import detect

    try:
        lang = detect(text)
    except Exception:
        lang = "vi"

    if lang == "vi":
        system_prompt = "Bạn là trợ lý tóm tắt tài liệu. Hãy tóm tắt ngắn gọn, mạch lạc, ưu tiên ý chính."
    elif lang.startswith("zh"):
        system_prompt = "你是专业助手，请用中文简洁总结主要内容，3-6句。"
    else:
        system_prompt = "You are a concise assistant. Summarize the document in 3-6 sentences."

    return ask_ai(text, system_prompt=system_prompt, model=model)


def summarize_results(query: str, chunks: list[str], model: str | None = None) -> str:
    sources = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(chunks))
    system_prompt = (
        "Bạn là trợ lý nghiên cứu cá nhân. Trả lời bằng tiếng Việt, tự nhiên, đi thẳng vào nội dung.\n"
        "Chỉ dùng thông tin có trong các đoạn trích người dùng cung cấp; nếu thiếu thì nói rõ thiếu."
    )
    _cite_on = (os.getenv("ENABLE_QUERY_CITATION_PROMPT", "0") or "").strip().lower() in ("1", "true", "yes", "on")
    if _cite_on:
        extra = (os.getenv("QUERY_CITATION_SYSTEM_SUFFIX") or "").strip()
        if not extra:
            extra = (
                "\n\nQuy tắc trích dẫn:\n"
                "- Các đoạn tài liệu có dạng [Nguồn: tên_file, đoạn N].\n"
                "- Khi dùng ý từ một đoạn, ghi rõ (Nguồn: tên_file, đoạn N) trong câu trả lời.\n"
                "- Nếu không có trong đoạn đã cho, nói rõ không tìm thấy trong tài liệu."
            )
        system_prompt = system_prompt + "\n" + extra
    user_msg = (
        f"Câu hỏi: {query}\n\n"
        f"Nội dung liên quan từ tài liệu:\n{sources}\n\n"
        "Hãy trả lời trực tiếp câu hỏi, mạch lạc."
    )
    return ask_ai(user_msg, system_prompt=system_prompt, model=model)
