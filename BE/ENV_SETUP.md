# Backend Environment Setup

## Quick Start

```bash
cd BE

# 1. Sao chép file mẫu thành .env
cp .env.example .env

# 2. Chỉnh sửa .env (điền API key nếu cần)
# GEMINI_API_KEY=your_key_here
# GROQ_API_KEY=your_key_here
```

## Dependencies cần cài

```bash
pip install -U python-dotenv
```

Các package liên quan đến embedding model (đã có trong `requirements.txt`):

```bash
pip install -U sentence-transformers transformers accelerate langchain-huggingface
```

## Đổi Embedding Model

Mặc định: `BAAI/bge-m3` (dimension 1024, đa ngôn ngữ)

```bash
# Trong .env
EMBEDDING_MODEL_NAME=BAAI/bge-m3
```

Model cũ (dimension 384, chỉ tiếng Anh tốt):

```bash
# EMBEDDING_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2
```

## Quan trọng: Rebuild FAISS Index khi đổi Embedding Model

Khi đổi `EMBEDDING_MODEL_NAME`, dimension vector thay đổi -> FAISS index cũ **KHÔNG tương thích**.

### PowerShell Commands để xóa artifacts cũ

```powershell
$base = "e:/memvid_NCKH/MemVid_New/BE"

# Xóa index FAISS chunk (bắt buộc khi đổi embedding model)
Remove-Item -Recurse -Force "$base/index" -ErrorAction SilentlyContinue

# Xóa memory artifacts (bắt buộc vì chứa embeddings)
Remove-Item -Force "$base/memory/memory_index.faiss" -ErrorAction SilentlyContinue
Remove-Item -Force "$base/memory/memory_index.json" -ErrorAction SilentlyContinue
Remove-Item -Force "$base/memory/memory_trees.json" -ErrorAction SilentlyContinue
Remove-Item -Force "$base/memory/mindmaps.json" -ErrorAction SilentlyContinue
Remove-Item -Force "$base/memory/mindmap_content_cache.json" -ErrorAction SilentlyContinue

Write-Host "Đã xóa toàn bộ embedding artifacts. Restart backend và upload lại document."
```

### Bash Commands (Linux/Mac/WSL)

```bash
cd BE
rm -rf index/
rm -rf memory/memory_index.faiss memory/memory_index.json
rm -rf memory/memory_trees.json
rm -rf memory/mindmaps.json memory/mindmap_content_cache.json
```

### File KHÔNG cần xóa (an toàn)

- `BE/.env` - cấu hình (chỉ đổi EMBEDDING_MODEL_NAME)
- `input_docs/` - tài liệu gốc
- `videos/` - video QR codes
- `jobs.sqlite` - job status
- `sessions.sqlite` - lịch sử chat
- `logs.sqlite` - logs
- `summaries.json` - không chứa embeddings

### File CÓ THỂ giữ (không bắt buộc xóa)

- `source_registry.json` - có thể giữ, source status sẽ được update khi upload lại

## Env Loading Priority

```
BE/.env           (ưu tiên cao nhất - file cùng thư mục BE/)
../.env           (root project - dùng cho docker-compose)
os.environ        (Docker/K8s environment variables - ghi đè tất cả khi override=True)
```

## Biến quan trọng nhất

| Biến | Mô tả | Mặc định |
|---|---|---|
| `OLLAMA_HOST` | Ollama server | `http://localhost:11434` |
| `SLM_MODEL_CHAT` | Model chat | `qwen3.5:9b` |
| `SLM_MODEL_SUMMARY` | Model summarize | `qwen2.5:14b` |
| `EMBEDDING_MODEL_NAME` | Model embedding | `BAAI/bge-m3` |
| `SKIP_MODEL_LOAD` | CI/testing mode | `0` |
| `DATA_DIR` | Thư mục data | `BE/` |

## Troubleshooting

**Ollama không kết nối:**
```bash
# Kiểm tra Ollama đang chạy
ollama list

# Pull model nếu chưa có
ollama pull qwen3.5:9b
```

**Embedding model lỗi:**
```bash
# Bật CI mode (dùng FakeEmbeddings)
SKIP_MODEL_LOAD=1 python -c "from llm_factory import get_embeddings; print(get_embeddings())"
```

**Dotenv không load:**
```bash
# Bỏ qua dotenv hoàn toàn
SKIP_DOTENV=1 python main.py
```
