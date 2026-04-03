
import json
import csv
import time
import requests
import numpy as np
import os
from typing import List, Dict
from sentence_transformers import SentenceTransformer, util
from ollama import Client

# Configuration
API_URL = "http://localhost:5000/query"
GROUND_TRUTH_FILE = os.path.join(os.path.dirname(__file__), "ground_truth.json")
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "evaluation_results.csv")
OLLAMA_MODEL = "gemma2:2b"  # Or any other model you have installed
EMBEDDING_MODEL_NAME = "keepitreal/vietnamese-sbert"

def load_ground_truth(file_path: str) -> List[Dict]:
    """Loads ground truth data from a JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: Ground truth file not found at {file_path}")
        return []

def query_system(question: str, source_file: str) -> str:
    """Sends a query to the system API."""
    payload = {
        "q": question,
        "sources": [source_file],
        "use_memory_tree": True
    }
    try:
        response = requests.post(API_URL, json=payload)
        response.raise_for_status() # Raise error for bad status codes
        data = response.json()
        return data.get("answer", "No answer provided")
    except requests.exceptions.RequestException as e:
        print(f"Error querying system: {e}")
        return f"Error: {str(e)}"

def calculate_cosine_similarity(text1: str, text2: str, model) -> float:
    """Calculates cosine similarity between two texts."""
    embeddings = model.encode([text1, text2], convert_to_tensor=True)
    cosine_score = util.cos_sim(embeddings[0], embeddings[1])
    return float(cosine_score[0][0])

def llm_judge(question: str, manual_answer: str, system_answer: str) -> float:
    """Uses an LLM to judge the quality of the system answer (1-10)."""
    client = Client(host='http://localhost:11434')
    
    prompt = f"""
    Bạn là một giám khảo công tâm. Hãy chấm điểm câu trả lời của hệ thống dựa trên đáp án chuẩn.
    
    Câu hỏi: {question}
    
    Đáp án chuẩn (Ground Truth): {manual_answer}
    
    Câu trả lời của hệ thống (System Answer): {system_answer}
    
    Hãy chấm điểm trên thang từ 1 đến 10. 
    - 1: Hoàn toàn sai hoặc không liên quan.
    - 10: Hoàn hảo, đầy đủ ý như đáp án chuẩn (có thể diễn đạt khác).
    
    Chỉ trả về MỘT CON SỐ DUY NHẤT (ví dụ: 7, 8.5, 9). Không giải thích thêm.
    """
    
    try:
        response = client.chat(model=OLLAMA_MODEL, messages=[
            {'role': 'user', 'content': prompt}
        ])
        content = response['message']['content'].strip()
        # Attempt to extract a number
        import re
        match = re.search(r"[-+]?\d*\.\d+|\d+", content)
        if match:
            return float(match.group())
        return 0.0
    except Exception as e:
        print(f"Error calling LLM judge: {e}")
        return 0.0

def main():
    print("--- Starting Evaluation ---")
    
    # 1. Load Ground Truth
    data = load_ground_truth(GROUND_TRUTH_FILE)
    if not data:
        return

    # 2. Initialize Models
    print(f"Loading embedding model: {EMBEDDING_MODEL_NAME}...")
    try:
        embed_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    except Exception as e:
        print(f"Failed to load embedding model. Ensure 'sentence-transformers' is installed. Error: {e}")
        return

    results = []
    
    # 3. Iterate and Evaluate
    print(f"Processing {len(data)} questions...")
    for item in data:
        q_id = item.get("id")
        question = item.get("question")
        manual_ans = item.get("manual_answer")
        src_file = item.get("source_file")
        
        print(f"Processing Q{q_id}: {question[:50]}...")
        
        # Query System
        start_time = time.time()
        sys_ans = query_system(question, src_file)
        latency = time.time() - start_time
        
        # Calculate Metrics
        print("  - Calculating similarity...")
        similarity = calculate_cosine_similarity(manual_ans, sys_ans, embed_model)
        
        print("  - Judging with LLM...")
        score = llm_judge(question, manual_ans, sys_ans)
        
        results.append({
            "id": q_id,
            "question": question,
            "manual_answer": manual_ans,
            "system_answer": sys_ans,
            "cosine_similarity": round(similarity, 4),
            "llm_score": score,
            "latency_seconds": round(latency, 2)
        })

    # 4. Save Results
    print(f"Saving results to {RESULTS_FILE}...")
    headers = ["id", "question", "manual_answer", "system_answer", "cosine_similarity", "llm_score", "latency_seconds"]
    
    try:
        with open(RESULTS_FILE, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(results)
        print("Results saved successfully.")
    except Exception as e:
        print(f"Error saving results: {e}")

    # 5. Summary
    if results:
        avg_sim = np.mean([r["cosine_similarity"] for r in results])
        avg_score = np.mean([r["llm_score"] for r in results])
        avg_latency = np.mean([r["latency_seconds"] for r in results])
        
        print("\n--- Summary ---")
        print(f"Average Cosine Similarity: {avg_sim:.4f}")
        print(f"Average LLM Score: {avg_score:.2f} / 10")
        print(f"Average Latency: {avg_latency:.2f} seconds")
    else:
        print("No results to summarize.")

if __name__ == "__main__":
    main()
