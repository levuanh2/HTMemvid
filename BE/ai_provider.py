import os

# Nếu có OLLAMA_HOST (dù rỗng hay không), ưu tiên chạy offline với Ollama.
USE_OLLAMA = os.getenv("OLLAMA_HOST") is not None

if USE_OLLAMA:
    # Local/offline
    from ollama_utils import ask_ollama as _ask_ollama
    from ollama_utils import run_ollama_chat as _run_ollama_chat
    from ollama_utils import summarize_whole_document as _summarize_whole_document
    from ollama_utils import summarize_results as _summarize_results
else:
    _gemini_model = None


def ask_ai(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
    """
    Gọi AI theo môi trường:
    - Nếu có OLLAMA_HOST → dùng Ollama (offline)
    - Ngược lại → dùng Gemini API (production)

    `system_prompt` và `model` là optional để không phá logic hiện có (Ollama dùng được model; Gemini sẽ bỏ qua model).
    """
    if USE_OLLAMA:
        if system_prompt:
            return _run_ollama_chat(system_prompt, prompt, model=model)
        return _ask_ollama(prompt, model=model)

    global _gemini_model
    if _gemini_model is None:
        try:
            import google.generativeai as genai
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Gemini mode requires package 'google-generativeai'. "
                "Install it or set OLLAMA_HOST for local mode."
            ) from exc

        api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("Missing GEMINI_API_KEY (required when OLLAMA_HOST is not set).")

        genai.configure(api_key=api_key)
        _gemini_model = genai.GenerativeModel("gemini-2.5-flash")

    if system_prompt:
        full_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{prompt}"
    else:
        full_prompt = prompt

    response = _gemini_model.generate_content(full_prompt)
    return (getattr(response, "text", None) or "").strip()


def summarize_whole_document(text: str, model: str | None = None) -> str:
    """
    API tương thích ngược với `ollama_utils.summarize_whole_document`:
    - Local: gọi hàm cũ (Ollama)
    - Production: gọi Gemini với prompt tóm tắt theo ngôn ngữ
    """
    if USE_OLLAMA:
        return _summarize_whole_document(text, model=model)

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
    """
    API tương thích ngược với `ollama_utils.summarize_results`:
    - Local: gọi hàm cũ (Ollama)
    - Production: gọi Gemini với prompt tổng hợp từ chunks
    """
    if USE_OLLAMA:
        return _summarize_results(query, chunks, model=model)

    sources = "\n".join(f"{i+1}. {c}" for i, c in enumerate(chunks))
    system_prompt = (
        "Bạn là trợ lý nghiên cứu cá nhân. Trả lời bằng tiếng Việt, tự nhiên, đi thẳng vào nội dung.\n"
        "Chỉ dùng thông tin có trong các đoạn trích người dùng cung cấp; nếu thiếu thì nói rõ thiếu."
    )
    user_msg = (
        f"Câu hỏi: {query}\n\n"
        f"Nội dung liên quan từ tài liệu:\n{sources}\n\n"
        "Hãy trả lời trực tiếp câu hỏi, mạch lạc."
    )
    return ask_ai(user_msg, system_prompt=system_prompt, model=model)

