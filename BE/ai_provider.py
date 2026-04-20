import os

PROVIDERS: list[str] = []

if os.getenv("OLLAMA_HOST"):
    PROVIDERS.append("ollama")

if os.getenv("GEMINI_API_KEY"):
    PROVIDERS.append("gemini")

if os.getenv("GROQ_API_KEY"):
    PROVIDERS.append("groq")

print("Active providers:", PROVIDERS)

_gemini_model = None
_groq_client = None


def ask_ollama(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
    from ollama_utils import ask_ollama as _ask_ollama
    from ollama_utils import run_ollama_chat as _run_ollama_chat

    if system_prompt:
        return _run_ollama_chat(system_prompt, prompt, model=model)
    return _ask_ollama(prompt, model=model)


def ask_gemini(prompt: str) -> str:
    global _gemini_model
    if _gemini_model is None:
        try:
            import google.generativeai as genai
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency 'google-generativeai' for Gemini provider.") from exc

        api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("Missing GEMINI_API_KEY for Gemini provider.")

        genai.configure(api_key=api_key)
        _gemini_model = genai.GenerativeModel("gemini-2.5-flash")

    res = _gemini_model.generate_content(prompt)
    return (getattr(res, "text", None) or "").strip()


def ask_groq(prompt: str) -> str:
    global _groq_client
    if _groq_client is None:
        try:
            from groq import Groq
        except ModuleNotFoundError as exc:
            raise RuntimeError("Missing dependency 'groq' for Groq provider.") from exc

        api_key = (os.getenv("GROQ_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("Missing GROQ_API_KEY for Groq provider.")

        _groq_client = Groq(api_key=api_key)

    res = _groq_client.chat.completions.create(
        model="llama3-8b-8192",
        messages=[{"role": "user", "content": prompt}],
    )
    return (res.choices[0].message.content or "").strip()


def ask_ai(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
    """
    Gọi AI theo môi trường:
    - Nếu có OLLAMA_HOST → dùng Ollama (offline)
    - Ngược lại → dùng Gemini API (production)

    `system_prompt` và `model` là optional để không phá logic hiện có (Ollama dùng được model; Gemini sẽ bỏ qua model).
    """
    last_error: Exception | None = None

    full_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{prompt}" if system_prompt else prompt

    for provider in PROVIDERS:
        try:
            if provider == "ollama":
                return ask_ollama(prompt, system_prompt=system_prompt, model=model)

            if provider == "gemini":
                return ask_gemini(full_prompt)

            if provider == "groq":
                return ask_groq(full_prompt)
        except Exception as e:
            last_error = e
            continue

    raise Exception(f"All AI providers failed: {last_error}")


def summarize_whole_document(text: str, model: str | None = None) -> str:
    """
    API tương thích ngược với `ollama_utils.summarize_whole_document`:
    - Local: gọi hàm cũ (Ollama)
    - Production: gọi Gemini với prompt tóm tắt theo ngôn ngữ
    """
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

