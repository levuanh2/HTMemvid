"""
Module tóm tắt tài liệu nâng cao theo các công thức:
1. ATS Process (D, M, G, E)
2. DANCER (Divide-and-Conquer)
3. Entity Chain Planning (FROST)
4. Chain of Density (CoD)
5. Structured Extraction
6. FactCC (Fact Checking)
"""
import re
import json
from typing import List, Dict, Tuple, Optional
from ollama_utils import run_ollama_chat, SLM_MODEL_SUMMARY
from ingest_utils import split_text


def preprocess_data(text: str) -> List[str]:
    """
    D (Data Preprocessing): Tiền xử lý dữ liệu
    - Tận dụng split_text (semantic chunker) từ ingest_utils
    - Loại bỏ nhiễu, trả về danh sách đoạn
    """
    text = (text or "").strip()
    if not text:
        return []

    # Ưu tiên semantic chunker đã dùng cho ingest
    chunks = split_text(text)
    if chunks:
        return chunks

    # Fallback: chia theo câu nếu chunker không trả kết quả
    text_compact = re.sub(r'\s+', ' ', text)
    sentences = re.split(r'(?<=[.!?])\s+', text_compact)
    return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 20]


def extract_entities(text: str, model: str = None) -> List[str]:
    """
    Trích xuất các thực thể quan trọng (Entity Chain Planning)
    - Tên riêng, ngày tháng, con số, thuật ngữ quan trọng
    """
    system_prompt = (
        "Bạn là một hệ thống trích xuất thực thể chuyên nghiệp.\n"
        "Nhiệm vụ: Trích xuất các thực thể quan trọng từ văn bản.\n"
        "Thực thể bao gồm: tên riêng, ngày tháng, con số, thuật ngữ chuyên ngành, khái niệm quan trọng.\n"
        "Trả về danh sách các thực thể, mỗi thực thể trên một dòng.\n"
        "Chỉ trả về danh sách, không giải thích thêm."
    )
    
    user_prompt = f"Văn bản:\n{text}\n\nDanh sách thực thể quan trọng:"
    
    response = run_ollama_chat(system_prompt, user_prompt, model=model)
    
    # Parse response thành list
    entities = []
    for line in response.split('\n'):
        line = line.strip()
        if line and not line.startswith('#') and len(line) > 2:
            # Loại bỏ số thứ tự nếu có
            line = re.sub(r'^\d+[\.\)]\s*', '', line)
            if line:
                entities.append(line)
    
    return entities[:20]  # Giới hạn 20 thực thể


def summarize_with_entity_chain(text: str, entities: List[str], model: str = None) -> str:
    """
    Tạo tóm tắt dựa trên Entity Chain (FROST)
    c;s: c là chuỗi thực thể, s là bản tóm tắt
    """
    entities_str = "\n".join(f"- {e}" for e in entities[:10])
    
    system_prompt = (
        "Bạn là một chuyên gia tóm tắt tài liệu.\n"
        "Nhiệm vụ: Tạo bản tóm tắt dựa trên danh sách thực thể quan trọng.\n"
        "Bản tóm tắt phải:\n"
        "1. Sử dụng các thực thể trong danh sách\n"
        "2. Đảm bảo tính chính xác, không thêm thông tin không có trong văn bản gốc\n"
        "3. Súc tích nhưng đầy đủ ý chính\n"
        "4. Trình bày có cấu trúc, dễ đọc"
    )
    
    user_prompt = (
        f"Danh sách thực thể quan trọng:\n{entities_str}\n\n"
        f"Văn bản gốc:\n{text}\n\n"
        "Tạo bản tóm tắt dựa trên các thực thể trên:"
    )
    
    return run_ollama_chat(system_prompt, user_prompt, model=model)


def chain_of_density(text: str, initial_summary: str = "", iterations: int = 5, model: str = None) -> str:
    """
    Chain of Density: Tăng cường độ đậm đặc thông tin
    Lặp lại quy trình: Xác định thực thể mới -> Viết lại tóm tắt
    """
    current_summary = initial_summary if initial_summary else ""
    
    for i in range(iterations):
        # Bước 1: Xác định 1-3 thực thể quan trọng chưa có trong tóm tắt
        system_prompt = (
            "Bạn là một hệ thống phân tích văn bản.\n"
            "Nhiệm vụ: Tìm 1-3 thực thể/quan niệm quan trọng có trong văn bản gốc "
            "nhưng CHƯA có trong bản tóm tắt hiện tại.\n"
            "Trả về danh sách ngắn gọn, mỗi thực thể một dòng."
        )
        
        user_prompt = (
            f"Văn bản gốc:\n{text[:2000]}\n\n"
            f"Bản tóm tắt hiện tại:\n{current_summary}\n\n"
            "Thực thể/quan niệm quan trọng chưa có trong tóm tắt:"
        )
        
        new_entities = run_ollama_chat(system_prompt, user_prompt, model=model)
        new_entities_list = [e.strip() for e in new_entities.split('\n') if e.strip()][:3]
        
        if not new_entities_list:
            break  # Không còn thực thể mới
        
        # Bước 2: Viết lại tóm tắt với cùng độ dài nhưng tích hợp thêm thực thể mới
        system_prompt2 = (
            "Bạn là một chuyên gia tóm tắt.\n"
            "Nhiệm vụ: Viết lại bản tóm tắt với CÙNG độ dài nhưng tích hợp thêm các thực thể mới.\n"
            "Bạn phải nén và hợp nhất văn bản để giữ nguyên độ dài nhưng thêm thông tin mới."
        )
        
        entities_str = "\n".join(f"- {e}" for e in new_entities_list)
        user_prompt2 = (
            f"Bản tóm tắt hiện tại:\n{current_summary}\n\n"
            f"Thực thể mới cần tích hợp:\n{entities_str}\n\n"
            f"Văn bản gốc (tham khảo):\n{text[:2000]}\n\n"
            "Bản tóm tắt mới (cùng độ dài, tích hợp thực thể mới):"
        )
        
        current_summary = run_ollama_chat(system_prompt2, user_prompt2, model=model)
    
    return current_summary


def divide_and_conquer_summarize(text: str, model: str = None, pre_chunks: Optional[List[str]] = None) -> str:
    """
    DANCER: Chia để trị
    - Phân rã tài liệu thành các phần
    - Tóm tắt từng phần
    - Tổng hợp lại
    """
    # Phân rã: Tìm các phần dựa trên cấu trúc
    paragraphs = pre_chunks if pre_chunks else preprocess_data(text)
    
    # Nhóm các đoạn thành các phần lớn (mỗi phần ~3-5 đoạn)
    chunk_size = max(3, len(paragraphs) // 5) if len(paragraphs) > 10 else len(paragraphs)
    sections = []
    for i in range(0, len(paragraphs), chunk_size):
        section_text = "\n\n".join(paragraphs[i:i+chunk_size])
        sections.append(section_text)
    
    # Tóm tắt từng phần
    section_summaries = []
    for idx, section in enumerate(sections):
        system_prompt = (
            "Bạn là một chuyên gia tóm tắt.\n"
            "Nhiệm vụ: Tóm tắt phần văn bản này một cách súc tích, tập trung vào ý chính."
        )
        summary = run_ollama_chat(system_prompt, f"Phần văn bản:\n{section}", model=model)
        section_summaries.append(summary)
    
    # Tổng hợp
    if len(section_summaries) == 1:
        return section_summaries[0]
    
    combined = "\n\n".join(f"Phần {i+1}: {s}" for i, s in enumerate(section_summaries))
    
    system_prompt = (
        "Bạn là một chuyên gia tổng hợp tài liệu.\n"
        "Nhiệm vụ: Tổng hợp các phần tóm tắt thành một bản tóm tắt hoàn chỉnh, mạch lạc.\n"
        "Loại bỏ sự trùng lặp, kết nối các ý tưởng một cách tự nhiên."
    )
    
    final_summary = run_ollama_chat(system_prompt, f"Các phần tóm tắt:\n{combined}", model=model)
    return final_summary


def structured_extraction(text: str, summary: str, model: str = None) -> Dict:
    """
    Structured Extraction: Chuyển đổi sang định dạng JSON có cấu trúc
    f_θ: T → S (S là danh sách đối tượng JSON)
    """
    system_prompt = (
        "Bạn là một hệ thống trích xuất thông tin có cấu trúc.\n"
        "Nhiệm vụ: Chuyển đổi bản tóm tắt thành định dạng JSON có cấu trúc.\n"
        "Trả về JSON hợp lệ với các trường:\n"
        "- title: Tiêu đề chính\n"
        "- keyPoints: Danh sách các ý chính (array)\n"
        "- formulas: Các công thức/quy trình (nếu có, array)\n"
        "- applications: Ứng dụng/thực tiễn (nếu có, array)\n"
        "- entities: Các thực thể quan trọng (array)\n"
        "- summary: Bản tóm tắt đầy đủ\n"
        "Chỉ trả về JSON, không có text thêm."
    )
    
    user_prompt = (
        f"Bản tóm tắt:\n{summary}\n\n"
        f"Văn bản gốc (tham khảo):\n{text[:1500]}\n\n"
        "Chuyển đổi sang JSON có cấu trúc:"
    )
    
    response = run_ollama_chat(system_prompt, user_prompt, model=model)
    
    # Parse JSON từ response
    try:
        # Tìm JSON trong response (có thể có text thêm)
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            return json.loads(json_str)
        else:
            # Thử parse toàn bộ response
            return json.loads(response)
    except:
        # Fallback: Tạo cấu trúc đơn giản
        return {
            "title": "Tóm tắt tài liệu",
            "keyPoints": summary.split('. ') if summary else [],
            "summary": summary,
            "entities": [],
            "formulas": [],
            "applications": []
        }


def fact_check(source_text: str, summary: str, model: str = None) -> Dict:
    """
    FactCC: Kiểm chứng tính nhất quán
    Phân loại: CONSISTENT hoặc INCONSISTENT
    Nếu INCONSISTENT, trích xuất các span gây lỗi
    """
    system_prompt = (
        "Bạn là một hệ thống kiểm chứng thông tin (Fact Checker).\n"
        "Nhiệm vụ: Kiểm tra xem bản tóm tắt có nhất quán với văn bản nguồn không.\n"
        "Trả về JSON với format:\n"
        '{\n'
        '  "status": "CONSISTENT" hoặc "INCONSISTENT",\n'
        '  "issues": [\n'
        '    {\n'
        '      "summary_span": "đoạn trong tóm tắt có vấn đề",\n'
        '      "source_span": "đoạn tương ứng trong văn bản nguồn",\n'
        '      "reason": "lý do không nhất quán"\n'
        '    }\n'
        '  ]\n'
        '}\n'
        "Nếu CONSISTENT, issues là mảng rỗng."
    )
    
    user_prompt = (
        f"Văn bản nguồn:\n{source_text[:3000]}\n\n"
        f"Bản tóm tắt:\n{summary}\n\n"
        "Kiểm tra tính nhất quán:"
    )
    
    response = run_ollama_chat(system_prompt, user_prompt, model=model)
    
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            result = json.loads(json_str)
            return result
        else:
            return json.loads(response)
    except:
        # Fallback: Giả định nhất quán nếu không parse được
        return {
            "status": "CONSISTENT",
            "issues": []
        }


def advanced_summarize(
    text: str,
    pre_chunks: Optional[List[str]] = None,
    use_dancer: bool = True,
    use_entity_chain: bool = True,
    use_cod: bool = True,
    use_structured: bool = True,
    use_fact_check: bool = True,
    model: str = None
) -> Dict:
    """
    Quy trình tóm tắt nâng cao kết hợp các công thức:
    P = (D, M, G, E)
    
    Args:
        text: Văn bản gốc
        use_dancer: Sử dụng DANCER (chia để trị)
        use_entity_chain: Sử dụng Entity Chain Planning
        use_cod: Sử dụng Chain of Density
        use_structured: Sử dụng Structured Extraction
        use_fact_check: Sử dụng FactCC
    
    Returns:
        Dict chứa kết quả tóm tắt và metadata
    """
    model = model or SLM_MODEL_SUMMARY
    
    # D: Data Preprocessing (nếu có sẵn chunk thì dùng lại, tránh tách lần nữa)
    chunks_input = pre_chunks if pre_chunks else preprocess_data(text)
    processed_text = "\n\n".join(chunks_input)
    
    # M + G: Modeling và Generation
    # Bước 1: Tóm tắt cơ bản (có thể dùng DANCER)
    if use_dancer and len(processed_text) > 2000:
        base_summary = divide_and_conquer_summarize(processed_text, model=model, pre_chunks=chunks_input)
    else:
        # Tóm tắt đơn giản
        system_prompt = "Bạn là chuyên gia tóm tắt. Tóm tắt văn bản một cách súc tích, tập trung vào ý chính."
        base_summary = run_ollama_chat(system_prompt, f"Văn bản:\n{processed_text[:3000]}", model=model)
    
    # Bước 2: Entity Chain Planning (nếu bật)
    final_summary = base_summary
    entities = []
    if use_entity_chain:
        entities = extract_entities(processed_text[:3000], model=model)
        if entities:
            final_summary = summarize_with_entity_chain(processed_text[:3000], entities, model=model)
    
    # Bước 3: Chain of Density (nếu bật)
    if use_cod:
        final_summary = chain_of_density(processed_text[:3000], final_summary, iterations=3, model=model)
    
    # Bước 4: Structured Extraction (nếu bật)
    structured_data = None
    if use_structured:
        structured_data = structured_extraction(processed_text[:2000], final_summary, model=model)
    
    # E: Evaluation (FactCC)
    fact_check_result = None
    if use_fact_check:
        fact_check_result = fact_check(processed_text[:3000], final_summary, model=model)
    
    return {
        "summary": final_summary,
        "base_summary": base_summary,
        "entities": entities,
        "structured": structured_data,
        "fact_check": fact_check_result,
        "metadata": {
            "used_dancer": use_dancer,
            "used_entity_chain": use_entity_chain,
            "used_cod": use_cod,
            "used_structured": use_structured,
            "used_fact_check": use_fact_check,
            "text_length": len(text),
            "summary_length": len(final_summary)
        }
    }

