# ollama_utils.py
from ollama import Client
import traceback
from typing import List
from langdetect import detect

# Default SLM model (thay đổi theo model bạn cài trên Ollama)
SLM_MODEL = 'gemma2:2b'  
OLLAMA_HOST = "http://localhost:11434"
def _safe_chat(messages: list[dict], model: str = None) -> str:
    """
    Hàm gọi Ollama an toàn (low-level).
    messages: list of {"role":..., "content":...}
    model: override model (nếu None sẽ dùng SLM_MODEL).
    Trả về raw text (hoặc chuỗi lỗi để debug).
    """
    model = model or SLM_MODEL
    try:
        cli = Client(host=OLLAMA_HOST)
        resp = cli.chat(model=model, messages=messages)
        raw = resp.get("message", {}).get("content", "")
        if raw is None:
            raw = ""
        raw = raw.strip()

        print("=== RAW OLLAMA RESPONSE (first 1500 chars) ===")
        print(raw[:1500])
        print("=== END RAW ===")

        if not raw:
            return "⚠️ LLM trả về rỗng."
        return raw
    except Exception:
        traceback.print_exc()
        return "⚠️ Lỗi khi gọi Ollama."


def run_ollama_chat(system_prompt: str, user_prompt: str, model: str = None) -> str:
    """
    Wrapper high-level: truyền system + user, có thể override model.
    SLM-friendly: dùng model nhỏ theo SLM_MODEL nếu không override.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    return _safe_chat(messages, model=model)


def summarize_whole_document(text: str, model: str = None) -> str:
    """Tóm tắt toàn bộ tài liệu bằng ngôn ngữ phù hợp."""
    try:
        lang = detect(text)
    except:
        lang = "vi"

    if lang == "vi":
        system_prompt = (
            "Bạn là một Chuyên gia Phân tích Tài liệu (tương tự NotebookLM).\n"
            "Nhiệm vụ của bạn là giúp người dùng hiểu sâu sắc nội dung từ các nguồn được cung cấp.\n\n"

            "HƯỚNG DẪN CHIẾN THUẬT:\n"
            "1. Nguồn tin: Chỉ sử dụng thông tin trong các đoạn tài liệu bên dưới. Nếu có thông tin trái ngược giữa các file, hãy nêu rõ sự khác biệt đó.\n"
            "2. Trích dẫn chính xác: Mỗi khi đưa ra một sự kiện/số liệu/ý chính, phải kèm theo ký hiệu nguồn (Ví dụ: [File A], [File B]).\n"
            "3. Tư duy tổng hợp: Không chỉ liệt kê, hãy kết nối các ý tưởng từ nhiều đoạn khác nhau để tạo ra một bức tranh toàn cảnh rõ ràng.\n"
            "4. Trình bày:\n"
            "   - Bắt đầu bằng Tóm tắt ý chính (Key Insights).\n"
            "   - Tiếp theo là Giải đáp chi tiết (Deep Dive).\n"
            "   - Kết thúc bằng Danh mục nguồn tham khảo (nếu có nhiều nguồn).\n\n"

            "CẤU TRÚC PHẢN HỒI:\n"
            "- Trả lời trực tiếp câu hỏi của người dùng một cách súc tích ở đầu.\n"
            "- Phân tích chi tiết dựa trên các bằng chứng từ tài liệu.\n"
            "- Cuối cùng, gợi ý 2 câu hỏi tiếp theo để người dùng khám phá thêm tài liệu.\n\n"

            "Tài liệu liên quan:\n"
            "{sources}\n\n"

            "Câu hỏi của người dùng: {query}"
        )
    elif lang.startswith("zh"):
        system_prompt = "你是专业助手，请用中文简洁总结主要内容，3-6句。"
    else:
        system_prompt = "You are a concise assistant. Summarize the document in 3-6 sentences."

    return _safe_chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ], model=model)


def summarize_results(query: str, chunks: List[str], model: str = None) -> str:
    """
    Trả lời câu hỏi dựa trên các đoạn văn đã lưu.
    SLM: ép model chỉ dùng nội dung cung cấp, trả lời ngắn.
    """
    try:
        lang = detect(query)
    except:
        lang = "vi"

    if lang == "vi":
        system_prompt = (
            "Bạn là một Chuyên gia Phân tích Tài liệu (tương tự NotebookLM).\n"
            "Nhiệm vụ của bạn là giúp người dùng hiểu sâu sắc nội dung từ các nguồn được cung cấp.\n\n"

            "HƯỚNG DẪN CHIẾN THUẬT:\n"
            "1. Nguồn tin: Chỉ sử dụng thông tin trong các đoạn tài liệu bên dưới. Nếu có thông tin trái ngược giữa các file, hãy nêu rõ sự khác biệt đó.\n"
            "2. Trích dẫn chính xác: Mỗi khi đưa ra một sự kiện/số liệu/ý chính, phải kèm theo ký hiệu nguồn (Ví dụ: [File A], [File B]).\n"
            "3. Tư duy tổng hợp: Không chỉ liệt kê, hãy kết nối các ý tưởng từ nhiều đoạn khác nhau để tạo ra một bức tranh toàn cảnh rõ ràng.\n"
            "4. Trình bày:\n"
            "   - Bắt đầu bằng Tóm tắt ý chính (Key Insights).\n"
            "   - Tiếp theo là Giải đáp chi tiết (Deep Dive).\n"
            "   - Kết thúc bằng Danh mục nguồn tham khảo (nếu có nhiều nguồn).\n\n"

            "CẤU TRÚC PHẢN HỒI:\n"
            "- Trả lời trực tiếp câu hỏi của người dùng một cách súc tích ở đầu.\n"
            "- Phân tích chi tiết dựa trên các bằng chứng từ tài liệu.\n"
            "- Cuối cùng, gợi ý 2 câu hỏi tiếp theo để người dùng khám phá thêm tài liệu.\n\n"

            "Tài liệu liên quan:\n"
            "{sources}\n\n"

            "Câu hỏi của người dùng: {query}"
        )
    elif lang.startswith("zh"):
        system_prompt = "你是AI助手。请用中文简洁回答，仅基于提供内容，若信息不足请说明。"
    else:
        system_prompt = "You are an AI assistant. Answer concisely using only provided content. If insufficient, say so."

    sources = "\n".join(f"{i+1}. {c}" for i, c in enumerate(chunks))
    user_msg = f"Relevant content:\n{sources}\n\nQuestion: {query}"

    return _safe_chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ], model=model)
