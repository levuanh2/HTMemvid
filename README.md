# MemVidX - Hệ thống Trí nhớ Thị giác & RAG

## Mục lục

1. [Tổng quan](#tổng-quan)
2. [Kiến trúc hệ thống](#kiến-trúc-hệ-thống)
3. [Cấu trúc dự án](#cấu-trúc-dự-án)
4. [Hướng dẫn cài đặt](#hướng-dẫn-cài-đặt)
5. [API Endpoints](#api-endpoints)
6. [Các tính năng chính](#các-tính-năng-chính)
7. [Mô hình AI/ML](#mô-hình-aiml)
8. [Lưu trữ dữ liệu](#lưu-trữ-dữ-liệu)
9. [Docker Deployment](#docker-deployment)
10. [Development](#development)

---

## Tổng quan

**MemVidX** là một hệ thống RAG (Retrieval-Augmented Generation) tích hợp trí nhớ thị giác, cho phép người dùng:

- **Upload tài liệu** (PDF, DOCX, TXT, hình ảnh) và mã hoá thành video QR
- **Tạo index vector** với FAISS để tìm kiếm ngữ nghĩa
- **Xây dựng Memory Tree** - cấu trúc phân cấp trí nhớ theo document/section/topic
- **Sinh Mind Map** tự động từ nội dung tài liệu
- **Tóm tắt thông minh** theo nhiều phương pháp nâng cao
- **Hỏi đáp thông minh** với ngữ cảnh từ tài liệu đã ingest

---

## Kiến trúc hệ thống

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT (FE)                                    │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────────────┐  │
│  │  SidebarLeft │  │    ChatArea      │  │       SidebarRight          │  │
│  │  - Upload    │  │  - Hỏi đáp      │  │  - Mind Map Viewer          │  │
│  │  - File List│  │  - Streaming     │  │  - Summary Viewer           │  │
│  │  - Selection │  │  - Progress      │  │  - History                 │  │
│  └──────────────┘  └──────────────────┘  └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ HTTP/REST API
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           BACKEND (BE) - Flask                               │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                        LangGraph Pipelines                            │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────┐   │  │
│  │  │ IngestGraph │  │ QueryGraph   │  │    MindmapGraph          │   │  │
│  │  │ - Extract   │  │ - Retrieve   │  │    - Generate MindMap    │   │  │
│  │  │ - Chunk     │  │ - Memory     │  │    - CMGN Strategy       │   │  │
│  │  │ - Embed     │  │ - Generate   │  │    - Iterative Expand   │   │  │
│  │  └─────────────┘  └─────────────┘  └──────────────────────────┘   │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                      Core Services                                    │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐   │  │
│  │  │ vector_store │  │ memory_tree   │  │  mindmap_utils        │   │  │
│  │  │ - FAISS      │  │ - Nodes      │  │  - CMGN Algorithm     │   │  │
│  │  │ - Embeddings │  │ - Intent     │  │  - Critics (3-phase)   │   │  │
│  │  └──────────────┘  └──────────────┘  └────────────────────────┘   │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐   │  │
│  │  │ llm_factory  │  │ chunk_proc   │  │  summarize_advanced    │   │  │
│  │  │ - Ollama     │  │ - QR Gen     │  │  - DANCER             │   │  │
│  │  │ - Gemini     │  │ - Video      │  │  - Chain of Density   │   │  │
│  │  │ - Groq       │  │ - Metadata   │  │  - Entity Chain       │   │  │
│  │  └──────────────┘  └──────────────┘  └────────────────────────┘   │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA STORAGE                                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐   │
│  │   BE/index/     │  │   BE/memory/    │  │   BE/videos/              │   │
│  │  - index.faiss  │  │  - memory_index │  │  - *.mp4 (QR videos)      │   │
│  │  - index.json   │  │  - memory_trees │  │                           │   │
│  │  - source_reg   │  │  - summaries    │  │                           │   │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘   │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐   │
│  │   BE/input_docs│  │   BE/jobs.sqlite │  │   BE/sessions.sqlite       │   │
│  │  - *.pdf/docx  │  │  - Job tracking  │  │  - Chat history           │   │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Cấu trúc dự án

```
MemVid_New/
├── BE/                          # Backend (Python/Flask)
│   ├── main.py                  # Flask app, all API endpoints
│   ├── requirements.txt          # Python dependencies
│   │
│   ├── graphs/                  # LangGraph pipelines
│   │   ├── ingest_graph.py     # Document ingestion pipeline
│   │   ├── query_graph.py       # Query & retrieval pipeline
│   │   ├── mindmap_graph.py     # Mind map generation pipeline
│   │   ├── state.py            # Shared state definitions
│   │   ├── sqlite_checkpointer.py
│   │   └── logger.py
│   │
│   ├── retrieval/                # Advanced retrieval
│   │   ├── hybrid.py           # Hybrid search (vector + keyword)
│   │   └── ensemble_retriever.py
│   │
│   ├── memory/                 # Memory layer
│   │   ├── lc_memory_tree.py   # LangChain memory adapter
│   │   └── __init__.py
│   │
│   ├── core_modules/           # Core business logic
│   │   ├── vector_store.py     # FAISS indexing & search
│   │   ├── memory_tree.py      # Memory tree structure
│   │   ├── mindmap_utils.py    # Mind map generation (CMGN)
│   │   ├── summarize_advanced.py # Advanced summarization
│   │   ├── chunk_processor.py  # QR code generation
│   │   ├── video_utils.py      # Video encoding/decoding
│   │   ├── ingest_utils.py     # Document text extraction
│   │   └── qa_chain.py        # Q&A chain
│   │
│   ├── services/               # LLM & external services
│   │   ├── llm_factory.py      # Multi-provider LLM factory
│   │   ├── ai_provider.py      # AI provider abstraction
│   │   └── ollama_utils.py     # Ollama-specific utils
│   │
│   ├── storage/               # Data persistence
│   │   ├── jobs_store.py       # Job tracking (SQLite)
│   │   └── sessions_store.py    # Session history
│   │
│   ├── index/                # Vector index storage
│   │   ├── index.faiss         # FAISS vector index
│   │   ├── index.json          # Chunk metadata
│   │   └── source_registry.json # Source tracking
│   │
│   ├── memory/                # Memory storage
│   │   ├── memory_index.faiss  # Memory vectors
│   │   ├── memory_index.json
│   │   ├── memory_trees.json    # Memory tree nodes
│   │   ├── mindmaps.json       # Generated mind maps
│   │   └── summaries.json       # Saved summaries
│   │
│   ├── videos/               # QR-encoded videos
│   │   └── *.mp4
│   │
│   ├── input_docs/           # Uploaded documents
│   │   └── *.pdf, *.docx, *.txt
│   │
│   ├── Dockerfile            # Backend container
│   ├── env_loader.py        # Environment config loader
│   └── rebuild_index_from_video.py
│
├── FE/                         # Frontend (React + Tailwind)
│   ├── src/
│   │   ├── App.jsx            # Main app component
│   │   ├── App.css
│   │   ├── main.jsx
│   │   ├── index.css
│   │   │
│   │   ├── components/Layout/
│   │   │   ├── MainLayout.jsx  # 3-column layout
│   │   │   ├── ChatArea.jsx    # Main chat interface
│   │   │   ├── SidebarLeft.jsx # Document management
│   │   │   ├── SidebarRight.jsx # Tools (MindMap/Summary)
│   │   │   ├── MindMapModal.jsx
│   │   │   └── SummaryModal.jsx
│   │   │
│   │   ├── hooks/
│   │   │   └── useTheme.js     # Dark/Light theme
│   │   │
│   │   └── utils/
│   │       └── api.js          # API client
│   │
│   ├── Dockerfile
│   ├── tailwind.config.js
│   ├── package.json
│   └── vite.config.js
│
├── docker-compose.yml          # Multi-container orchestration
├── .env                        # Environment variables
├── requirements.txt            # Python dependencies (root)
└── memvid_architecture_flow.svg # Architecture diagram
```

---

## Hướng dẫn cài đặt

### Yêu cầu hệ thống

- **Python 3.10+**
- **Node.js 18+** (cho Frontend)
- **Ollama** (chạy local) hoặc **API Key** (Gemini/Groq)
- **Docker & Docker Compose** (optional)

### 1. Cài đặt Backend

```bash
# Di chuyển vào thư mục Backend
cd BE

# Tạo virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# Hoặc: venv\Scripts\activate  # Windows

# Cài đặt dependencies
pip install -r requirements.txt

# Cài đặt Ollama models (cần thiết nếu dùng local)
ollama pull qwen3.5:9b
ollama pull qwen2.5:14b
ollama pull gemma2:2b
```

### 2. Cài đặt Frontend

```bash
# Di chuyển vào thư mục Frontend
cd FE

# Cài đặt dependencies
npm install

# Copy environment file
cp .env.example .env  # Chỉnh sửa VITE_API_URL nếu cần
```

### 3. Cấu hình Environment

Tạo file `.env` trong thư mục `BE/`:

```env
# AI Provider Configuration
OLLAMA_HOST=http://localhost:11434

# LLM Models
SLM_MODEL_CHAT=qwen3.5:9b
SLM_MODEL_SUMMARY=qwen2.5:14b
MINDMAP_MODEL=qwen2.5:14b
SLM_MODEL_INTENT=gemma2:2b

# Alternative: Gemini
# GEMINI_API_KEY=your_gemini_api_key

# Alternative: Groq
# GROQ_API_KEY=your_groq_api_key

# Storage Paths
DATA_DIR=./BE
VIDEO_DIR=./BE/videos
INPUT_DOCS_DIR=./BE/input_docs
INDEX_DIR=./BE/index
MEMORY_DIR=./BE/memory

# Embedding Model
EMBEDDING_MODEL_NAME=sentence-transformers/all-MiniLM-L6-v2

# Optional: Skip model loading for CI testing
# SKIP_MODEL_LOAD=1
```

### 4. Chạy Ứng dụng

**Development Mode:**

```bash
# Terminal 1: Backend
cd BE
python main.py

# Terminal 2: Frontend
cd FE
npm run dev
```

**Docker Mode:**

```bash
docker-compose up --build
```

---

## API Endpoints

### Health & Stats

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| GET | `/health` | Detailed health status |
| GET | `/stats` | Index statistics (documents, chunks, videos) |

### Document Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/upload` | Upload single file |
| POST | `/upload-file` | Upload file (alias) |
| POST | `/upload-multiple` | Upload multiple files |
| POST | `/process-doc` | Process raw text |
| POST | `/delete-source` | Delete source (legacy) |
| DELETE | `/sources/<id>` | Delete source (v2, clean delete) |
| GET | `/list-indexed` | List all indexed sources |
| GET | `/sources/<id>/status` | Get source processing status |

### Query & Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/query` | Submit query (async, returns job_id) |
| GET | `/query-status/<job_id>` | Poll query job status |
| GET | `/query-stream/<job_id>` | SSE stream for query progress |

### Mind Map

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/generate-mindmap` | Generate mind map async |
| GET | `/mindmap-status/<job_id>` | Poll mind map generation status |
| GET | `/mindmaps` | List saved mind maps |
| DELETE | `/mindmaps/<id>` | Delete mind map |

### Summary

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/summarize-file` | Summarize uploaded file |
| POST | `/summarize-documents` | Advanced summarize (multi-method) |
| GET | `/summaries` | List saved summaries |
| POST | `/summaries` | Save summary |
| DELETE | `/summaries/<id>` | Delete summary |

### Memory Tree

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/memory-tree-status` | Get all memory tree status |
| GET | `/memory-tree/<stem>` | Get memory tree for specific source |

### Index Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/rebuild-index` | Rebuild FAISS index from videos |
| GET | `/rebuild-status/<job_id>` | Poll rebuild job status |

---

## Các tính năng chính

### 1. Document Ingestion Pipeline

```
Document Upload → Text Extraction → Semantic Chunking → Embedding → FAISS Index
                                        ↓
                               QR Code Generation → Video Encoding
                                        ↓
                               Memory Tree Construction
```

**Chi tiết:**
- **Text Extraction**: Hỗ trợ PDF (PyMuPDF), DOCX (python-docx), TXT, Images (OCR via Tesseract)
- **Semantic Chunking**: Sử dụng `SemanticChunker` từ LangChain với embedding model
- **QR Encoding**: Mỗi chunk được mã hoá thành QR code frame, ghép thành video MP4
- **Metadata**: Parent-child relationships, order, checksum cho data integrity

### 2. Memory Tree Architecture

```
MemoryTree
├── Document Node (root)
│   ├── Summary (LLM-generated)
│   ├── Intent Type (definition/procedure/argument/comparison/reference)
│   └── Embedding (384-dim vector)
│
└── Section Nodes (children)
    ├── Title
    ├── Summary
    ├── Chunk References
    ├── Intent Type
    └── Embedding
```

**Query Routing:**
- **Overview**: Ưu tiên document-level nodes
- **Main Points**: Lấy cả document + section summaries
- **Detail/How**: Ưu tiên section nodes, nhiều chunks
- **Compare**: Nhiều section nodes để so sánh
- **Locate**: Fallback sang chunk-level search

### 3. Mind Map Generation (CMGN Algorithm)

**Coreference-Guided Mind-Map Network** sử dụng 3-phase pipeline:

```
1. Sentence Extraction
   └── Parse document → list of sentences with IDs

2. Coreference Graph Building
   ├── Identify entities
   ├── Cluster co-referential mentions
   └── Build semantic edges

3. Mind Map Generation
   └── Tree structure with:
       - Root (topic)
       - Branch 1 (coreference cluster)
       ├── Sub-branch 1.1
       └── Sub-branch 1.2
       - Branch 2
       └── ...
```

**Critics (3-phase refinement):**
1. **Factuality Critic**: Kiểm tra độ chính xác vs source
2. **Local Structure Critic**: Đảm bảo specificity của nodes
3. **Global Structure Critic**: Cân bằng bố cục toàn cục

### 4. Advanced Summarization

Hệ thống tóm tắt đa phương pháp:

| Method | Description |
|--------|-------------|
| **DANCER** | Divide-and-Conquer: Chia tài liệu → tóm tắt từng phần → tổng hợp |
| **Entity Chain** | Trích xuất entities → tạo summary dựa trên chain |
| **Chain of Density** | Iterative enrichment với increasing entity density |
| **Structured Extraction** | Chuyển đổi sang JSON có cấu trúc |
| **FactCC** | Kiểm chứng tính nhất quán vs source |

### 5. Hybrid Retrieval

```python
# Retrieval strategy
Final_Results = α × Semantic_Scores + β × BM25_Scores + γ × MemoryTree_Scores
```

- **Semantic Search**: FAISS vector similarity
- **Keyword Search**: BM25 sparse retrieval
- **Memory Tree**: Summary-level retrieval với query routing

---

## Mô hình AI/ML

### Embedding Models

| Model | Dimension | Use Case |
|-------|-----------|----------|
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | Default, fast |
| `sentence-transformers/all-mpnet-base-v2` | 768 | High quality |

### LLM Models

| Model | Provider | Use Case |
|-------|----------|----------|
| `qwen3.5:9b` | Ollama | Chat & general Q&A |
| `qwen2.5:14b` | Ollama | Summary & Mind Map |
| `gemma2:2b` | Ollama | Intent classification |
| `gemini-2.5-flash` | Google | Cloud alternative |
| `llama-3.3-70b-versatile` | Groq | Cloud alternative |

### Query Routing (No-LLM Heuristics)

```python
def classify_query_type(query: str) -> str:
    # Fast keyword-based classification (~1ms)
    if "tóm tắt" in query: return "overview"
    if "ý chính" in query: return "main_points"
    if "chi tiết" in query: return "detail"
    if "so sánh" in query: return "compare"
    # ... more patterns
```

---

## Lưu trữ dữ liệu

### Directory Structure

```
BE/
├── index/                      # Vector index
│   ├── index.faiss             # FAISS index file
│   ├── index.json              # Metadata (chunk_id → text, video, etc.)
│   └── source_registry.json    # Upload status tracking
│
├── memory/                     # High-level memory artifacts
│   ├── memory_index.faiss      # Memory vectors
│   ├── memory_index.json       # Memory metadata
│   ├── memory_trees.json       # Tree nodes (document + sections)
│   ├── mindmaps.json           # Generated mind maps
│   └── summaries.json          # Saved summaries
│
├── videos/                     # QR-encoded videos
│   └── *.mp4                   # One video per upload
│
└── input_docs/                # Original uploads
    └── *.pdf, *.docx, *.txt
```

### SQLite Databases

| Database | Tables | Purpose |
|----------|--------|---------|
| `jobs.sqlite` | jobs | Job tracking (ingest, query, mindmap) |
| `sessions.sqlite` | sessions, messages | Chat history |
| `checkpoints.sqlite` | checkpoints | LangGraph state persistence |

### Index JSON Schema

```json
{
  "123": {
    "text": "Chunk content...",
    "video": "source_filename_timestamp.mp4",
    "timestamp": "2025-05-23T12:00:00",
    "parent_id": null,
    "sub_order": 1,
    "total_parts": 1,
    "is_subchunk": false,
    "embedding": [0.123, ...]
  },
  "__meta__": {
    "version": "1.0",
    "created_at": "2025-05-23T12:00:00",
    "num_chunks": 150,
    "vector_backend": "langchain_faiss"
  }
}
```

### Memory Tree Node Schema

```json
{
  "tree_id": "memtree_source1",
  "source_stem": "report_20250523",
  "built_at": "2025-05-23T12:00:00Z",
  "version": "1.0",
  "status": "completed",
  "nodes": [
    {
      "memory_id": "mem_doc_source1",
      "type": "document",
      "title": "Tài liệu: report_20250523",
      "summary": "Generated document summary...",
      "embedding": [0.456, ...],
      "chunk_refs": ["0", "1", "2"],
      "children": ["mem_sec_source1_0", "mem_sec_source1_1"],
      "metadata": {"source_stem": "report_20250523", "num_chunks": 45},
      "intent_type": "argument"
    }
  ]
}
```

---

## Docker Deployment

### docker-compose.yml

```yaml
services:
  backend:
    build: ./BE
    ports:
      - "8080:8080"
    volumes:
      - ./data/videos:/app/videos
      - ./data/index:/app/index
      - ./data/memory:/app/memory
      - ./data/input_docs:/app/input_docs
    environment:
      DATA_DIR: /app
      PORT: "8080"
      OLLAMA_HOST: http://host.docker.internal:11434
      SLM_MODEL_CHAT: qwen3.5:9b
      USE_LC_VECTOR_STORE: "1"
      USE_LC_QA_CHAIN: "1"
    extra_hosts:
      - "host.docker.internal:host-gateway"

  frontend:
    build: .
    ports:
      - "3000:3000"
    depends_on:
      - backend
```

### Docker Commands

```bash
# Build and start
docker-compose up --build

# Start in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down

# Rebuild after code changes
docker-compose up --build --force-recreate
```

### Production Considerations

1. **Volume Mounts**: Data persists in `./data/` on host
2. **OLLAMA_HOST**: Use `host.docker.internal` on Windows/Mac
3. **CORS**: Set `CORS_ORIGINS` for production domains
4. **Health Check**: Backend health endpoint at `/health`

---

## Development

### Project Structure Guidelines

```
BE/
├── core_modules/     # Pure business logic, no Flask imports
├── services/         # External integrations (LLM, embedding)
├── storage/          # Data persistence
├── graphs/          # LangGraph pipelines
└── main.py          # Flask app + routes only
```

### Adding New Features

1. **New API Endpoint**: Add to `main.py`
2. **New Service**: Add to appropriate directory under `BE/`
3. **New Frontend Component**: Add to `FE/src/components/Layout/`

### Testing

```bash
# Run all tests
cd BE
pytest tests/

# Run specific test
pytest tests/test_query.py -v

# With coverage
pytest tests/ --cov=. --cov-report=html
```

### Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_DIR` | `./BE` | Root data directory |
| `VIDEO_DIR` | `$DATA_DIR/videos` | QR video storage |
| `INDEX_DIR` | `$DATA_DIR/index` | Vector index |
| `MEMORY_DIR` | `$DATA_DIR/memory` | Memory artifacts |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |
| `SLM_MODEL_CHAT` | `qwen3.5:9b` | Chat model |
| `SLM_MODEL_SUMMARY` | `qwen2.5:14b` | Summary model |
| `EMBEDDING_MODEL_NAME` | `all-MiniLM-L6-v2` | Embedding model |
| `QUERY_CACHE_TTL_SEC` | `1800` | Query cache TTL |
| `USE_LC_VECTOR_STORE` | `0` | Use LangChain FAISS |

---

## License & Credits

Dự án được phát triển cho mục đích nghiên cứu khoa học.

**Authors**: Lê Vũ Anh, Nguyễn Minh Hiếu

**Tech Stack**:
- Backend: Python, Flask, LangChain, LangGraph, FAISS
- Frontend: React, TailwindCSS, Vite
- AI: Ollama, Gemini, Groq, HuggingFace
