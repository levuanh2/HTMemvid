# ollama_utils.py
import os
import time
from ollama import Client
import traceback
from typing import List
from langdetect import detect

# Default SLM model (thay đổi theo model bạn cài trên Ollama)
SLM_MODEL = 'gemma4:e4b'  
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
def _safe_chat(messages: list[dict], model: str = None) -> str:
    """
    Hàm gọi Ollama an toàn (low-level).
    messages: list of {"role":..., "content":...}
    model: override model (nếu None sẽ dùng SLM_MODEL).
    Trả về raw text (hoặc chuỗi lỗi để debug).
    """
    if os.environ.get("SKIP_MODEL_LOAD") == "1":
        return "[CI MODE] Skipped LLM response"

    model = model or SLM_MODEL
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            cli = Client(host=OLLAMA_HOST)
            resp = cli.chat(model=model, messages=messages)
            raw = resp.get("message", {}).get("content", "")
            if raw is None:
                raw = ""
            raw = raw.strip()

            # Giảm log noise trong production
            if attempt == 1:
                print("=== RAW OLLAMA RESPONSE (first 500 chars) ===")
                print(raw[:500])
                print("=== END RAW ===")

            if not raw:
                return "⚠️ LLM trả về rỗng."
            return raw
        except Exception as exc:
            last_err = exc
            print(f"[OLLAMA] call failed attempt={attempt}/3: {exc}")
            time.sleep(1.0 * attempt)

    traceback.print_exc()
    return "⚠️ Lỗi khi gọi Ollama."


def run_ollama_chat(system_prompt: str, user_prompt: str, model: str = None) -> str:
    """
    Wrapper high-level: truyền system + user, có thể override model.
    SLM-friendly: dùng model nhỏ theo SLM_MODEL nếu không override.
    """
    if os.environ.get("SKIP_MODEL_LOAD") == "1":
        return "[CI MODE] Skipped LLM response"

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
    Trả lời câu hỏi dựa trên các đoạn văn đã lưu (fallback khi không có Memory Tree).
    Đảm bảo trả lời bằng tiếng Việt và theo style NotebookLM.
    """
    # Base system prompt
    system_prompt = (
        "Bạn là người đã đọc, hiểu và ghi chú toàn bộ nội dung tài liệu thay cho người dùng.\n\n"
        
        "Bạn KHÔNG giới thiệu vai trò của mình.\n"
        "Bạn KHÔNG nói về cách bạn trả lời.\n"
        "Bạn CHỈ nói về nội dung, như một người vừa đọc xong và đang giải thích lại.\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "CÁCH SUY NGHĨ (KHÔNG ĐƯỢC VIẾT RA):\n\n"
        
        "- Người dùng đang hỏi để làm gì?\n"
        "- Họ muốn nghe một câu trả lời TỰ NHIÊN như người thật nói chuyện\n"
        "- Cấu trúc câu trả lời phải mượt, liền mạch, không lộ khung\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "CÁCH VIẾT BẮT BUỘC:\n\n"
        
        "- BẮT ĐẦU TRỰC TIẾP vào nội dung, KHÔNG mở đầu chung chung\n"
        "- Viết như đang kể lại, giải thích, hoặc tóm tắt cho một người khác\n"
        "- Ý phải nối tiếp nhau, không rời rạc\n"
        "- Không dùng bullet trừ khi bắt buộc (quy trình, các bước)\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "XỬ LÝ CÂU HỎI CHUNG CHUNG (VD: \"file này là gì\", \"này nói gì\"):\n\n"
        
        "- Trả lời gọn ý chính trước\n"
        "- Sau đó diễn giải thêm để người nghe hiểu bản chất\n"
        "- Nếu có kiến thức nền → lồng vào tự nhiên, không dạy đời\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "VỚI NỘI DUNG KỸ THUẬT:\n\n"
        
        "- Giải thích theo kiểu \"ý tưởng là…\"\n"
        "- So sánh với ví dụ đời thường nếu hợp lý\n"
        "- Tránh thuật ngữ, hoặc giải thích ngay khi dùng\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "GỢI Ý HỎI TIẾP:\n\n"
        
        "- CHỈ thêm nếu thật sự hợp lý\n"
        "- Viết như một câu nói thêm, không phải lời mời gọi máy móc\n"
        "- Ví dụ: \"Nếu bạn muốn đào sâu hơn phần này, mình có thể giải thích kỹ hơn.\"\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "CẤM TUYỆT ĐỐI:\n\n"
        
        "- Không nói \"bạn đang muốn…\"\n"
        "- Không nói \"dưới đây là…\"\n"
        "- Không nói \"tài liệu đề cập…\"\n"
        "- Không liệt kê kiểu slide\n"
        "- Không để lộ Answer Mode, intent, hay cấu trúc suy nghĩ\n"
        "- Không copy nguyên văn\n"
        "- Không nói mình là AI hay LLM\n"
        "- Không nhắc chunk, node, embedding, tìm kiếm\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "NGÔN NGỮ:\n\n"
        
        "- Luôn là tiếng Việt\n"
        "- Tự nhiên, giống người thật\n"
        "- Giống trợ lý nghiên cứu cá nhân, không giống chatbot\n\n"
        
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "OUTPUT:\n"
        "- Chỉ trả về đoạn trả lời cho người dùng\n"
        "- Văn xuôi, mạch lạc\n"
        "- Nếu tài liệu không đủ thông tin, nói ngắn gọn và tự nhiên"
    )

    sources = "\n".join(f"{i+1}. {c}" for i, c in enumerate(chunks))
    user_msg = f"Câu hỏi: {query}\n\nNội dung liên quan từ tài liệu:\n{sources}\n\nHãy trả lời trực tiếp vào câu hỏi, viết như đang giải thích lại cho người khác một cách tự nhiên, mạch lạc."

    return _safe_chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ], model=model)
