# MemVidX - Architecture & Workflow Documentation

## Mục lục
1. [Tổng quan](#tổng-quan)
2. [Cấu trúc dự án](#cấu-trúc-dự-án)
3. [Luồng dữ liệu End-to-End](#luồng-dữ-liệu-end-to-end)
4. [Ingest Workflow](#ingest-workflow)
5. [Query Workflow](#query-workflow)
6. [Mindmap & Summarize](#mindmap--summarize)
7. [Storage Layer](#storage-layer)
8. [API Endpoints](#api-endpoints)
9. [Environment Configuration](#environment-configuration)

---

## Tổng quan

MemVidX là hệ thống RAG (Retrieval-Augmented Generation) kết hợp:
- Xử lý tài liệu đa định dạng (PDF, DOCX, TXT, Video)
- Mã hóa video thành QR code
- Xây dựng cây phân cấp kiến thức (Memory Tree)
- Tạo mindmap tự động
- Hỏi đáp thông minh với streaming SSE

### Công nghệ chính

| Phần | Công nghệ |
|------|-----------|
| **Backend** | FastAPI/Flask, LangGraph, LangChain, FAISS, ChromaDB |
| **LLM** | Ollama, OpenAI-compatible API, Gemini, Groq |
| **Document Processing** | PyMuPDF, python-docx, Whisper |
| **Frontend** | React, Vite, Tailwind CSS |
| **Storage** | FAISS, SQLite, JSON files |

---

## Cấu trúc dự án

```
MemVid_New/
├── BE/                          # Backend (Python)
│   ├── main.py                  # FastAPI/Flask entry point
│   ├── graphs/                  # LangGraph workflows
│   │   ├── ingest_graph.py      # Document ingestion workflow
│   │   ├── query_graph.py       # Query/answer workflow
│   │   ├── mindmap_graph.py     # Mindmap generation
│   │   ├── state.py             # Shared state definitions
│   │   └── logger.py            # Logging utilities
│   ├── retrieval/               # Retrieval logic
│   │   ├── ensemble_retriever.py
│   │   └── hybrid.py            # BM25 + FAISS + RRF
│   ├── memory/                  # Memory system
│   │   ├── memory_trees.json    # Hierarchical memory trees
│   │   ├── summaries.json       # Document summaries
│   │   ├── mindmaps.json        # Generated mindmaps
│   │   └── memory_index.faiss   # Memory vector index
│   ├── index/                   # Document vector store
│   │   ├── index.faiss          # FAISS index
│   │   └── index.json           # Metadata
│   ├── tests/                   # Pytest test suite
│   ├── document_loader.py       # Multi-format document loading
│   ├── chunk_processor.py       # Text chunking
│   ├── summarize_advanced.py    # LLM summarization
│   ├── memory_tree.py           # Memory tree management
│   ├── qa_chain.py              # QA chain
│   ├── jobs_store.py            # Job management
│   ├── sessions_store.py        # Session management
│   ├── llm_factory.py           # LLM provider factory
│   ├── Dockerfile
│   └── requirements.txt
│
├── FE/                          # Frontend (React)
│   ├── src/
│   │   ├── components/
│   │   │   └── Layout/
│   │   │       ├── ChatArea.jsx     # Main chat interface
│   │   │       ├── MindMapModal.jsx  # Mindmap visualization
│   │   │       ├── SummaryModal.jsx  # Document summary view
│   │   │       ├── SidebarLeft.jsx   # Session/source list
│   │   │       └── SidebarRight.jsx  # Context/documents
│   │   ├── hooks/
│   │   │   └── useTheme.js          # Theme management
│   │   └── utils/
│   │       └── api.js               # Backend API client
│   ├── tailwind.config.js
│   └── package.json
│
├── docker-compose.yml           # Container orchestration
├── .env                         # Environment configuration
└── README.md
```

---

## Luồng dữ liệu End-to-End

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                   │
│                    React + Vite + Tailwind CSS                             │
│              ChatArea · EventSource SSE · apiFetch                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              API LAYER                                      │
│                         Flask/FastAPI - main.py                            │
│                   /upload · /query · /query-stream                         │
│                   /generate-mindmap · /health                              │
│                                    │                                        │
│           ┌────────────────────────┼────────────────────────┐              │
│           │                        │                        │              │
│           ▼                        ▼                        ▼              │
│   ┌──────────────┐        ┌──────────────┐        ┌──────────────┐       │
│   │ jobs_store   │        │ sessions_    │        │   Env flags  │       │
│   │   .sqlite    │        │   store      │        │              │       │
│   └──────────────┘        └──────────────┘        └──────────────┘       │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌─────────────────────────────────┐   ┌─────────────────────────────────────┐
│      INGEST GRAPH               │   │         QUERY GRAPH                 │
│      (LangGraph)                │   │         (LangGraph)                 │
└─────────────────────────────────┘   └─────────────────────────────────────┘
```

---

## Ingest Workflow

Quy trình nạp tài liệu vào hệ thống.

### Sơ đồ luồng

```
┌─────────────────┐
│  File Upload    │  (PDF, DOCX, MP4, TXT, Video)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  ingest_utils   │  extract_text() + split_text()
│  (extract/split)│  Trích xuất & chia nhỏ text
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌─────────────────┐    ┌─────────────────┐
│ chunk_processor │───▶│  vector_store   │
│ (QR/MP4/metadata│    │  FAISS Index    │
└────────┬────────┘    └────────┬────────┘
         │                       │
         │                       ▼
         │              ┌─────────────────┐
         │              │   memory_tree    │
         │              │   build_tree()   │
         │              └────────┬─────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│   Videos / QR   │     │   FAISS index   │
│   videos/       │     │   index.faiss    │
└─────────────────┘     │   + index.json   │
                       └─────────────────┘
```

### Chi tiết các bước

| Bước | Module | Chức năng |
|------|--------|-----------|
| 1 | **Upload** | Nhận file từ client |
| 2 | **ingest_utils** | Extract text từ file, split thành chunks |
| 3 | **chunk_processor** | Xử lý metadata, tạo QR cho video |
| 4 | **vector_store** | Tạo embeddings và lưu vào FAISS |
| 5 | **memory_tree** | Xây dựng cây phân cấp kiến thức |

### Các file quan trọng

- `BE/ingest_utils.py` - Trích xuất text và chia chunks
- `BE/chunk_processor.py` - Xử lý chunks, metadata, video
- `BE/vector_store.py` - Lưu trữ vector với FAISS
- `BE/memory_tree.py` - Xây dựng memory tree

---

## Query Workflow

Quy trình xử lý câu hỏi và trả lời.

### Sơ đồ luồng

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         QueryGraph (LangGraph)                              │
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                 │
│  │ Cache lookup │───▶│ Memory tree  │───▶│ Hybrid       │                 │
│  │ hit / miss   │    │ (conditional)│    │ retrieval    │                 │
│  └──────────────┘    └──────────────┘    └──────┬───────┘                 │
│                                                  │                          │
│                                                  ▼                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                 │
│  │ Conv.history │───▶│   Generate   │◀───│  Context     │                 │
│  │sessions_store│    │   answer     │    │  builder     │                 │
│  └──────────────┘    └──────┬───────┘    │  (citation)  │                 │
│                             │            └──────────────┘                 │
│                             ▼                                               │
│                    ┌──────────────┐                                         │
│                    │  Finalize    │───▶ SSE Streaming / jobs_store         │
│                    └──────────────┘                                         │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Chi tiết các node

| Node | Mô tả | Chi tiết |
|------|-------|----------|
| **Cache lookup** | Kiểm tra cache | Nếu hit → trả ngay, miss → tiếp tục |
| **Memory tree** | Truy xuất context | Conditional - chỉ chạy khi cần |
| **Hybrid retrieval** | Tìm docs liên quan (Stage 1 / Recall) | BM25 + FAISS + RRF; khi rerank bật → lấy `RERANK_CANDIDATE_K` ứng viên |
| **Rerank** | Lọc tinh (Stage 2 / Precision) | Cross-encoder chấm cặp (query, passage), lọc xuống `RERANK_TOP_N`; conditional - chỉ khi `RERANK_ENABLED=1` |
| **Context builder** | Tạo context | Thêm citation, truncate nếu quá dài |
| **Generate answer** | Sinh câu trả | Gọi qa_chain với context + history |
| **Finalize** | Hoàn thiện | Format output, gửi SSE |

### Retrieval Chi tiết (Hybrid)

```
Query ─┬─▶ BM25 (keyword search)
       ├─▶ FAISS (vector similarity)
       └─▶ RRF (Reciprocal Rank Fusion)
            │
            ▼
       Combined Results
```

**RRF Formula:**
```
RRF_score = Σ (1 / (k + rank_i)) / n
```

### Rerank — Two-Stage Retrieval (Stage 2 / Precision)

```
Stage 1 (Recall)            Stage 2 (Precision)
Hybrid ─▶ RERANK_CANDIDATE_K ─▶ Cross-encoder (query, passage) ─▶ top RERANK_TOP_N ─▶ LLM
  ~20 ứng viên                    chấm điểm đồng thời                ~4 tốt nhất
```

- Bật bằng `RERANK_ENABLED=1` (mặc định tắt → graph chạy y như cũ).
- Backend cắm-rút (`RERANK_BACKEND`): `cross_encoder` (self-host, offline — mặc định `BAAI/bge-reranker-v2-m3`), `cohere`, `llm`, `none`.
- An toàn: lỗi load/predict hoặc quá `RERANK_TIMEOUT_SEC` → giữ nguyên thứ tự (Identity), không làm vỡ pipeline.
- Lưu ý: rerank chỉ sắp xếp lại tài liệu Stage 1 đưa cho — KHÔNG tìm tài liệu mới (Recall thấp thì rerank vô dụng).
- Code: `BE/app/domains/retrieval/rerank.py`, node `RerankDocuments` trong `query_graph.py`.
- `k` = 60 (constant)
- `rank_i` = thứ hạng trong retrieval method i
- `n` = số retrieval methods

### Các file quan trọng

- `BE/graphs/query_graph.py` - Query workflow
- `BE/retrieval/hybrid.py` - Hybrid retrieval
- `BE/retrieval/ensemble_retriever.py` - Ensemble retriever
- `BE/qa_chain.py` - QA chain
- `BE/sessions_store.py` - Conversation history

---

## Mindmap & Summarize

### MindmapGraph

```
┌─────────────────────────────────────────────────┐
│                 MindmapGraph                     │
│  mindmap_utils · JSON schema · LLM             │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
              ┌───────────────┐
              │  llm_factory  │ Ollama · Gemini · Groq
              └───────────────┘
```

### Summarize

```
┌─────────────────────────────────────────────────┐
│                  Summarize                       │
│            summarize_advanced.py                 │
└─────────────────────┬───────────────────────────┘
                      │
                      ▼
              ┌───────────────┐
              │  llm_factory  │
              └───────────────┘
```

### Các file quan trọng

- `BE/graphs/mindmap_graph.py` - Mindmap workflow
- `BE/mindmap_utils.py` - Mindmap utilities
- `BE/summarize_advanced.py` - Advanced summarization

---

## Storage Layer

### Sơ đồ Storage

```
┌─────────────────┐
│   FAISS index   │  index.faiss · index.json
│  Document Store │  Vector embeddings
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Memory store   │  memory/ folder
│                 │  memory_trees.json
│                 │  summaries.json
│                 │  mindmaps.json
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Videos / QR    │  videos/ folder
│                 │  Video files & QR codes
└─────────────────┘
         │
         ▼
┌─────────────────┐
│     SQLite       │  jobs.sqlite
│                 │  logs.sqlite
│                 │  checkpoints.sqlite
└─────────────────┘
```

### Chi tiết Storage

| Storage | Đường dẫn | Mô tả |
|---------|-----------|-------|
| **FAISS Index** | `BE/index/` | Vector embeddings của documents |
| **Memory Store** | `BE/memory/` | Memory trees, summaries, mindmaps |
| **Videos** | `BE/videos/` | Video files và QR codes |
| **SQLite** | `BE/*.sqlite` | Jobs, logs, checkpoints |

---

## API Endpoints

### Endpoints chính

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `POST` | `/upload` | Upload tài liệu/video |
| `POST` | `/query` | Query thường (sync) |
| `POST` | `/query-stream` | Query với SSE streaming |
| `POST` | `/generate-mindmap` | Tạo mindmap |
| `GET` | `/health` | Health check |
| `GET` | `/sessions` | Lấy danh sách sessions |
| `DELETE` | `/sources/{id}` | Xóa source |

### Response Format

```json
{
  "answer": "Câu trả lời...",
  "sources": [
    {
      "id": "source_1",
      "content": "Nội dung trích dẫn...",
      "score": 0.95
    }
  ],
  "session_id": "session_xxx"
}
```

### SSE Streaming

```javascript
// Frontend
const eventSource = new EventSource(`/query-stream?query=${query}`);
eventSource.onmessage = (event) => {
  const data = JSON.parse(event.data);
  // Handle streaming chunks
};
```

---

## Environment Configuration

### Các biến môi trường

```bash
# Vector Store
USE_LC_VECTOR_STORE=true        # Dùng LangChain VectorStore

# Retrieval
USE_LC_ENSEMBLE=true           # Dùng LangChain Ensemble Retriever

# QA Chain
USE_LC_QA_CHAIN=true          # Dùng LangChain QA Chain

# Evaluation
EVAL_ENABLED=false             # Bật evaluation mode

# Models
MINDMAP_MODEL=gpt-4            # Model cho mindmap generation
OLLAMA_MODEL=llama3            # Model cho Ollama

# LLM Providers
OLLAMA_BASE_URL=http://localhost:11434
OPENAI_API_KEY=sk-xxx
GEMINI_API_KEY=xxx
GROQ_API_KEY=xxx

# Storage
FAISS_INDEX_PATH=./index
MEMORY_PATH=./memory
VIDEOS_PATH=./videos
```

### Database Connection

```bash
# Jobs Store
JOBS_DB_PATH=./jobs.sqlite

# Sessions Store
SESSIONS_DB_PATH=./sessions.sqlite

# Logs
LOGS_DB_PATH=./logs.sqlite
```

---

## Cài đặt & Chạy

### Development

```bash
# Clone repo
git clone https://github.com/your-repo/MemVid_New.git
cd MemVid_New

# Backend
cd BE
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Frontend
cd FE
npm install
npm run dev
```

### Docker

```bash
docker-compose up --build
```

### Testing

```bash
cd BE
pytest tests/ -v
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              MEMVIDX ARCHITECTURE                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ CLIENT LAYER                                                        │   │
│  │  React + Vite + Tailwind CSS                                        │   │
│  │  ChatArea · EventSource SSE · apiFetch                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ API LAYER                                                           │   │
│  │  Flask/FastAPI - main.py                                            │   │
│  │  /upload · /query · /query-stream · /generate-mindmap · /health    │   │
│  │  jobs_store · sessions_store                                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                    │                               │                        │
│                    ▼                               ▼                        │
│  ┌───────────────────────────┐     ┌───────────────────────────────────┐   │
│  │ INGEST GRAPH (LangGraph)  │     │ QUERY GRAPH (LangGraph)          │   │
│  │                           │     │                                   │   │
│  │ ingest_utils              │     │ Cache lookup ──┐                  │   │
│  │   extract · split_text    │     │ Memory tree   │── Hybrid ──┐    │   │
│  │ chunk_processor           │     │                │  retrieval │    │   │
│  │   QR · MP4 · metadata     │     │                └─────┬──────┘    │   │
│  │ vector_store              │     │              Context builder     │   │
│  │   FAISS · LC FAISS        │     │                Generate answer  │   │
│  │ memory_tree               │     │ Conv.history ── Finalize ── SSE │   │
│  │   build · query_with_     │     │                                   │   │
│  │       memory              │     └───────────────────────────────────┘   │
│  └───────────────────────────┘                       │                     │
│                    │                                 │                     │
│                    ▼                                 ▼                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ LLM FACTORY                                                          │   │
│  │  Ollama · Gemini · Groq · OpenAI                                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ STORAGE LAYER                                                        │   │
│  │  FAISS index · Memory store · Videos/QR · SQLite                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## License

MIT License
