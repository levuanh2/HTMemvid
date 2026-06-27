# Mindmap Generation Workflow

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Sơ đồ luồng tổng thể](#2-sơ-đồ-luồng-tổng-thể)
3. [Chi tiết từng bước](#3-chi-tiết-từng-bước)
   - [Bước 1: Tiền xử lý chunks](#bước-1-tiền-xử-lý-chunks)
   - [Bước 2: Chọn Strategy](#bước-2-chọn-strategy)
   - [Bước 3: Merge sub-chunks](#bước-3-merge-sub-chunks)
   - [Bước 4: Sinh Mindmap (4 strategies)](#bước-4-sinh-mindmap-4-strategies)
   - [Bước 5: Tạo Visual Diagram](#bước-5-tạo-visual-diagram)
4. [Các Strategy chi tiết](#4-các-strategy-chi-tiết)
   - [Strategy 1: single_call_schema](#strategy-1-single_call_schema)
   - [Strategy 2: mindmap_v2](#strategy-2-mindmap_v2)
   - [Strategy 3: cmgn](#strategy-3-cmgn-coreference-guided)
   - [Strategy 4: iterative](#strategy-4-iterative)
   - [Strategy 5: multilevel (fallback)](#strategy-5-multilevel-fallback)
5. [Cache mechanism](#5-cache-mechanism)
6. [Thời gian ước tính](#6-thời-gian-ước-tính)
7. [Fallback chain](#7-fallback-chain)

---

## 1. Tổng quan

Mindmap generation là quá trình tạo sơ đồ tư duy từ nội dung tài liệu đã được chunk và embed. Hệ thống sử dụng **adaptive strategy selection** để chọn phương pháp phù hợp dựa trên kích thước dữ liệu.

### Thông số quan trọng

| Tham số | Giá trị |
|----------|---------|
| Embedding model | `BAAI/bge-m3` (1024 dim) |
| Mindmap LLM | `qwen2.5:14b` (configurable qua `MINDMAP_MODEL`) |
| Temperature | `0.1-0.2` (low randomness) |
| Timeout mỗi LLM call | 90 giây (configurable qua `MINDMAP_TIMEOUT_SEC`) |

---

## 2. Sơ đồ luồng tổng thể

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    run_mindmap_generation()                            │
│                    ==========================                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Bước 1: Đọc index metadata từ index.json                            │
│  - Load all chunks từ index metadata                                   │
│  - Normalize video names (loại timestamp)                              │
│  - Filter chunks theo selected_sources                                 │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Bước 2: Merge sub-chunks (nếu có)                                   │
│  - Gom các sub-chunk thành logical chunks                               │
│  - Tính average embedding cho merged chunk                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Bước 3: Chọn Strategy (Adaptive Selection)                           │
│  - Dựa trên số chunks và tổng ký tự                                 │
│  - Kiểm tra cache (content_hash)                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
                    ▼                               ▼
           ┌─────────────┐                 ┌─────────────┐
           │ Cache HIT  │                 │ Cache MISS │
           │ → Return   │                 │ → Generate │
           │   cached   │                 │   new      │
           └─────────────┘                 └─────────────┘
                                                    │
                                    ┌───────────────┴───────────────┐
                                    │                               │
                                    ▼                               ▼
                          ┌─────────────────┐             ┌─────────────────┐
                          │ single_call     │             │ mindmap_v2       │
                          │ mindmap_v2      │             │ cmgn            │
                          │ cmgn            │────────────▶│ iterative       │
                          │ iterative       │             │ multilevel       │
                          └─────────────────┘             └─────────────────┘
                                    │                               │
                                    └───────────────┬───────────────┘
                                                    │
                                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Bước 4: Tạo Visual Diagram (Napkin AI style)                       │
│  - Gọi LLM với outline mindmap                                       │
│  - Fallback sang _flat_nodes_to_visual_diagram() nếu fail            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Bước 5: Lưu vào mindmaps.json                                       │
│  - Tạo record với nodes, diagram, sources, strategy, createdAt       │
│  - Update cache (mindmap_content_cache.json)                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Chi tiết từng bước

### Bước 1: Tiền xử lý chunks

```python
# File: mindmap_generation_worker.py - run_mindmap_generation()

# Đọc metadata từ index.json
with open(index_meta_path, encoding="utf-8") as f:
    meta = json.load(f)

# Normalize video names
def normalize_video_name(name: str) -> str:
    # Loại bỏ timestamp format: _YYYYMMDD_HHMMSS
    cleaned = re.sub(r'_\d{8}_\d{6}$', '', name)
    return cleaned.strip().lower()

# Filter chunks theo nguồn được chọn
all_chunks_with_meta = []
for key, m in meta.items():
    video_clean = normalize_video_name(m.get("video", ""))
    if video_clean in normalized_sources:
        all_chunks_with_meta.append({
            "text": m.get("text", ""),
            "embedding": m.get("embedding"),
            "parent_id": m.get("parent_id"),
            "sub_order": m.get("sub_order"),
            "is_subchunk": m.get("is_subchunk", False),
        })
```

**Output của bước này:**
- `all_chunks_with_meta`: Danh sách tất cả chunks từ các file được chọn
- Mỗi chunk có: `text`, `embedding`, `parent_id`, `sub_order`, `is_subchunk`

---

### Bước 2: Chọn Strategy

```python
# File: mindmap_generation_worker.py - select_mindmap_strategy()

def select_mindmap_strategy(chunks, force_strategy=None):
    n_chunks = len(chunks)
    total_chars = sum(len(c.get("text", "")) for c in chunks)
    
    # Rất nhỏ: < 2500 ký tự
    if total_chars < 2500:
        return "single_call_schema"
    
    # Nhỏ: 1-4 chunks, < 8000 ký tự
    if n_chunks <= 4 and total_chars < 8000:
        return "single_call_schema"
    
    # Trung bình: 5-18 chunks, < 18000 ký tự
    if n_chunks <= 18 and total_chars < 18000:
        return "mindmap_v2"
    
    # Lớn: 19-45 chunks, < 50000 ký tự
    if n_chunks <= 45 and total_chars < 50000:
        return "cmgn"
    
    # Rất lớn
    return "iterative"
```

**Chiến lược được chọn:**

| Strategy | Điều kiện | Mô tả |
|----------|------------|-------|
| `single_call_schema` | < 8000 chars, ≤ 4 chunks | 1 LLM call với JSON schema |
| `mindmap_v2` | 8000-18000 chars, 5-18 chunks | TF-IDF + KMeans clustering + 1 LLM call |
| `cmgn` | 18000-50000 chars, 19-45 chunks | Coreference graph + iterative prompting |
| `iterative` | > 50000 chars, > 45 chunks | Nhiều LLM calls, phân tích sâu |

---

### Bước 3: Merge sub-chunks

```python
# File: mindmap_generation_worker.py - run_mindmap_generation()

# Phân loại chunks
merged_logical = []
sub_groups = {}
logical_normal = []

for item in all_chunks_with_meta:
    if item.get("is_subchunk") and item.get("parent_id"):
        # Gom sub-chunk vào group
        parent_key = str(item["parent_id"]).strip()
        if parent_key not in sub_groups:
            sub_groups[parent_key] = []
        sub_groups[parent_key].append(item)
    else:
        # Chunk bình thường
        logical_normal.append({"text": item["text"], "embedding": item.get("embedding")})

# Merge sub-chunks trong mỗi group
for parent_key, subs in sub_groups.items():
    merged_text = "\n\n".join(sub.get("text", "").strip() for sub in subs)
    
    # Tính average embedding
    emb_vecs = [sub["embedding"] for sub in subs if sub.get("embedding")]
    if emb_vecs:
        stacked = safe_stack_vectors(emb_vecs)  # Tránh lỗi dim mismatch
        avg_emb = np.mean(stacked, axis=0).tolist()
    else:
        avg_emb = None
    
    merged_logical.append({"text": merged_text, "embedding": avg_emb})

# Kết quả cuối cùng
final_logical_chunks = logical_normal + merged_logical
final_chunks = [c["text"] for c in final_logical_chunks]
```

**Output của bước này:**
- `final_logical_chunks`: Danh sách chunks đã merge (cả normal + merged)
- `final_chunks`: Chỉ text của các chunks (để embed)

---

### Bước 4: Sinh Mindmap (4 strategies)

#### Strategy 1: single_call_schema

```
┌─────────────────────────────────────────────────────────────────┐
│              single_call_schema                                  │
│  ============================                                   │
│                                                                 │
│  1. Embed all chunks (BAAI/bge-m3)                             │
│  2. KMeans clustering (k = sqrt(n_chunks), max 12)             │
│  3. Concatenate chunks trong mỗi cluster                        │
│  4. Gọi LLM 1 lần với full prompt                            │
│     - system_prompt: Yêu cầu MindmapOutput JSON schema          │
│     - user_prompt: Cluster summaries + sources                  │
│  5. Parse JSON → flat_nodes                                    │
│  6. Retry up to 3 lần nếu fail                                │
└─────────────────────────────────────────────────────────────────┘
```

**LLM Calls:** 1-3 lần (retry if failed)

**Thời gian ước tính:**
- Embedding: ~1-3 giây (tùy số chunks)
- LLM call: ~5-15 giây
- **Total: ~6-20 giây**

---

#### Strategy 2: mindmap_v2

```
┌─────────────────────────────────────────────────────────────────┐
│                    mindmap_v2                                   │
│  ==============                                                 │
│                                                                 │
│  1. Clustering với embeddings CÓ SẴN từ index.json              │
│     - Dùng safe_stack_vectors() để tránh lỗi dim               │
│     - KMeans với 6 clusters cố định                            │
│  2. TF-IDF để trích keywords cho mỗi cluster                   │
│  3. Concatenate chunks để tạo summary                          │
│  4. Gọi LLM 1 lần với cluster info                           │
│     - system: "Tạo MindmapOutput từ TF-IDF clusters"            │
│     - user: Cluster summaries + keywords + sources              │
│  5. Parse JSON → flat_nodes                                    │
│  6. Fallback sang single_call nếu fail                         │
└─────────────────────────────────────────────────────────────────┘
```

**LLM Calls:** 1 lần

**Thời gian ước tính:**
- Clustering: < 1 giây
- TF-IDF: < 1 giây
- LLM call: ~5-15 giây
- **Total: ~7-18 giây**

---

#### Strategy 3: cmgn (Coreference-Guided)

```
┌─────────────────────────────────────────────────────────────────┐
│                        cmgn                                     │
│  ==================                                             │
│                                                                 │
│  PHASE 1: Coreference Graph                                     │
│  ────────────────────                                           │
│  1. Extract sentences từ chunks (max 48)                        │
│  2. Gọi LLM để tạo coreference graph:                        │
│     - Entities (thực thể trong câu)                            │
│     - Clusters (các câu cùng thực thể)                         │
│     - Edges (quan hệ giữa các câu)                              │
│     - Root candidates (câu trung tâm)                           │
│                                                                 │
│  PHASE 2: Mindmap Generation                                   │
│  ────────────────────────────                                   │
│  3. Gọi LLM với coreference graph để tạo mindmap:           │
│     - Root từ rootCandidates                                   │
│     - Branches từ clusters                                    │
│     - Details từ edges                                         │
│                                                                 │
│  PHASE 3: Critics (3 bước)                                    │
│  ──────────────────────────                                     │
│  4. Factuality Critic: Lọc branches không có dẫn chứng        │
│  5. Local Structure Critic: Cải thiện chi tiết cục bộ         │
│  6. Global Structure Critic: Cân bằng cấu trúc toàn cục        │
└─────────────────────────────────────────────────────────────────┘
```

**LLM Calls:** 5 lần (1 coreference + 1 mindmap + 3 critics)

**Thời gian ước tính:**
- Coreference graph: ~10-20 giây
- Mindmap generation: ~10-20 giây
- 3 Critics: ~15-30 giây (5-10 giây mỗi cái)
- **Total: ~35-70 giây**

---

#### Strategy 4: iterative

```
┌─────────────────────────────────────────────────────────────────┐
│                      iterative                                   │
│  ==============                                                 │
│                                                                 │
│  PHASE 1: Root Topic                                         │
│  ─────────────────                                             │
│  1. Gọi LLM để xác định root topic:                        │
│     - "Xác định chủ đề trung tâm của tài liệu"              │
│                                                                 │
│  PHASE 2: Iterative Expansion                                  │
│  ─────────────────────────────                                 │
│  2. Queue-based BFS: mở rộng từng node                       │
│     - Với mỗi node: gọi LLM để suggest children              │
│     - Tiếp tục đệ quy đến khi đạt max_depth               │
│     - Noise detection: loại bỏ metadata hành chính            │
│                                                                 │
│  PHASE 3: Critics (3 bước)                                    │
│  ──────────────────────────                                    │
│  3. Factuality Critic: Đối chiếu với văn bản gốc            │
│  4. Local Structure Critic: Chi tiết hóa cục bộ               │
│  5. Global Structure Critic: Cân bằng toàn cục                │
└─────────────────────────────────────────────────────────────────┘
```

**LLM Calls:** 2 + N*2 + 3 lần (N = số nodes mở rộng)

**Thời gian ước tính:**
- Root topic: ~5-10 giây
- Iterative expansion: ~15-60 giây (tùy kích thước)
- 3 Critics: ~15-30 giây
- **Total: ~35-100 giây**

---

#### Strategy 5: multilevel (fallback)

```
┌─────────────────────────────────────────────────────────────────┐
│                    multilevel (fallback)                         │
│  ==============================                                 │
│                                                                 │
│  1. Context selection: top-sim + random diversity               │
│     - 18 chunks most similar to centroid                        │
│     - 8 random chunks                                          │
│  2. Topic extraction LLM call                                 │
│  3. Semantic deduplication (cosine similarity > 0.85)          │
│  4. Soft clustering: gán chunks vào topics                     │
│  5. Structure classification: 5 labels chuẩn                   │
│     - overview, components, process, applications, issues        │
│  6. Subtopic expansion LLM call                                │
│  7. Key points extraction (level 3) - tùy chọn               │
└─────────────────────────────────────────────────────────────────┘
```

**LLM Calls:** 3-5 lần

**Thời gian ước tính:**
- Topic extraction: ~10-15 giây
- Subtopic expansion: ~20-40 giây (8 topics × 5 giây)
- Key points: ~10-20 giây (nếu enable)
- **Total: ~40-75 giây**

---

### Bước 5: Tạo Visual Diagram

```python
# File: mindmap_generation_worker.py - _build_visual_diagram()

def _build_visual_diagram(flat_nodes, final_chunks, root_title, sources):
    # 1. Chuẩn bị outline
    outline_lines = [f"- id={n['id']} | title={n['title']}" for n in flat_nodes[:80]]
    
    # 2. Context: 12 chunks đầu tiên
    context = "\n\n---\n\n".join(final_chunks[:12])
    
    # 3. Gọi LLM với system prompt Napkin AI style
    system_prompt = """
    Bạn là AI visual diagram designer.
    Chọn diagramType phù hợp:
    - concept_map: khái niệm nhiều nhánh
    - flowchart: quy trình/các bước
    - comparison: so sánh
    - cycle: vòng lặp
    - timeline: trình tự thời gian
    - cause_effect: nguyên nhân-kết quả
    """
    
    # 4. Parse output thành VisualDiagramOutput
    # 5. Fallback: _flat_nodes_to_visual_diagram() nếu fail
```

**LLM Calls:** 1 lần

**Thời gian ước tính:** ~10-20 giây

**Fallback:** Nếu LLM fail → convert flat_nodes sang diagram format đơn giản

---

## 4. Cache Mechanism

```python
# Content hash key bao gồm:
content_hash = SHA256(
    sources |        # sorted source names
    n_chunks |      # số chunks
    total_chars |    # tổng ký tự
    chunks_hash |    # SHA256 của toàn bộ nội dung
    strategy |      # strategy được chọn
    model_suffix |  # MINDMAP_MODEL
    schema_version |
    EMBEDDING_MODEL_NAME |  # ← Quan trọng: tránh dùng cache từ model cũ
    embedding_dim   # ← Quan trọng: tránh dùng cache từ dim cũ
)

# Cache path: memory/mindmap_content_cache.json
{
    "<hash>": {
        "nodes": [...],
        "strategy": "single_call_schema"
    }
}
```

---

## 5. Thời gian ước tính

| Strategy | Dữ liệu | LLM Calls | Thời gian ước tính |
|----------|----------|----------|---------------------|
| `single_call_schema` | < 8K chars | 1-3 | **6-20 giây** |
| `mindmap_v2` | 8K-18K chars | 1 | **7-18 giây** |
| `cmgn` | 18K-50K chars | 5 | **35-70 giây** |
| `iterative` | > 50K chars | 2 + N*2 + 3 | **35-100 giây** |
| `multilevel` (fallback) | Any | 3-5 | **40-75 giây** |

**Thành phần thời gian:**
- Embedding: 1-3 giây (BAAI/bge-m3)
- LLM call: 5-20 giây (qwen2.5:14b)
- Python processing: < 1 giây

---

## 6. Fallback Chain

```
┌─────────────────────────────────────────────────────────────────┐
│                     Fallback Flow                                │
└─────────────────────────────────────────────────────────────────┘

Primary Strategy fail
        │
        ▼
┌───────────────────┐
│ Try next strategy │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│ Try multilevel    │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│ Try iterative    │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│ Try single_call  │
└───────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ Final Fallback: get_main_branches() → flat nodes cơ bản │
│ (Chỉ lấy 3-5 main topics, không có hierarchical)          │
└───────────────────────────────────────────────────────────────┘
```

---

## 7. Tổng kết các LLM calls theo strategy

| Strategy | LLM Calls | Chi tiết |
|----------|-----------|----------|
| `single_call_schema` | 1-3 | 1 mindmap + retries |
| `mindmap_v2` | 1 | 1 mindmap (dùng embeddings có sẵn) |
| `cmgn` | 5 | 1 coreference + 1 mindmap + 3 critics |
| `iterative` | 2 + N*2 + 3 | root + N expand + 3 critics |
| `multilevel` | 3-5 | topics + N*subtopics + key_points |

**N là số nodes được mở rộng** (thường 5-15)

---

## 8. Ví dụ thực tế

### Ví dụ 1: Tài liệu nhỏ (single_call_schema)

```
Input: 3 chunks, 5000 ký tự

Bước 1: Load 3 chunks
Bước 2: Chọn single_call_schema (n_chunks=3, chars=5000)
Bước 3: No sub-chunks
Bước 4: 
  - Embed 3 chunks: ~1 giây
  - KMeans(3): ~0.1 giây
  - LLM call: ~8 giây
  - Parse: ~0.1 giây
Bước 5: Visual diagram LLM: ~10 giây

Total: ~19 giây
```

### Ví dụ 2: Tài liệu trung bình (mindmap_v2)

```
Input: 12 chunks, 15000 ký tự

Bước 1: Load 12 chunks
Bước 2: Chọn mindmap_v2 (n_chunks=12, chars=15000)
Bước 3: No sub-chunks
Bước 4:
  - Dùng embeddings có sẵn từ index.json: ~0 giây
  - KMeans(6): ~0.2 giây
  - TF-IDF: ~0.5 giây
  - LLM call: ~12 giây
Bước 5: Visual diagram LLM: ~10 giây

Total: ~23 giây
```

### Ví dụ 3: Tài liệu lớn (cmgn)

```
Input: 30 chunks, 40000 ký tự

Bước 1: Load 30 chunks
Bước 2: Chọn cmgn (n_chunks=30, chars=40000)
Bước 3: No sub-chunks
Bước 4:
  - Extract sentences (48): ~0.5 giây
  - Coreference graph LLM: ~15 giây
  - Mindmap LLM: ~15 giây
  - Factuality critic: ~8 giây
  - Local critic: ~8 giây
  - Global critic: ~8 giây
Bước 5: Visual diagram LLM: ~10 giây

Total: ~65 giây
```
