# 📋 FLOW XỬ LÝ UPLOAD FILE - CHI TIẾT

## 🎯 Tổng quan
Hệ thống xử lý upload file theo mô hình **2 pha**:
1. **Pha 1 (Nhanh)**: Trả UI ngay, không block
2. **Pha 2 (Background)**: Xử lý ingest pipeline chạy ngầm

---

## 🔄 FLOW CHI TIẾT

### **PHASE 1: UPLOAD & RESPONSE NGAY** (< 500ms)

#### **1.1 Frontend - User Upload File**
```
📁 User chọn file → Click "Thêm"
   ↓
🔄 handleAddFiles() được gọi
   ↓
📤 Loop qua từng file:
   - Tạo FormData
   - Gọi POST /upload
```

**Code:** `FE/src/components/Layout/SidebarLeft.jsx:109-150`

```javascript
const handleAddFiles = async (e) => {
  const files = e.target.files;
  setUploading(true);
  
  for (let file of files) {
    const formData = new FormData();
    formData.append("file", file);
    
    const res = await fetch(`${API_BASE}/upload`, {
      method: "POST",
      body: formData,
    });
    
    const data = await res.json();
    // Optimistic UI: Thêm vào list ngay
    setSources((prev) => [...prev, {
      source_id: data.source_id,
      filename: data.filename,
      status: "processing",
      progress: 0.0,
    }]);
    
    // Bắt đầu polling
    pollSourceStatus(data.source_id);
  }
}
```

---

#### **1.2 Backend - POST /upload Endpoint**
```
📥 Nhận file từ request
   ↓
🆔 Generate source_id (UUID)
   ↓
💾 Save file vào input_docs/
   ↓
📝 Ghi vào source_registry.json:
   {
     source_id: {
       filename: "...",
       source_stem: "...",
       status: "processing",
       progress: 0.0,
       created_at: "2025-01-09T..."
     }
   }
   ↓
🚀 Trigger background thread (non-blocking)
   ↓
✅ TRẢ RESPONSE NGAY:
   {
     source_id: "...",
     filename: "...",
     status: "processing"
   }
```

**Code:** `BE/main.py:416-456`

```python
@app.post('/upload-file')
def upload_file():
    file = request.files.get('file')
    source_id = str(uuid.uuid4())
    filename = file.filename
    
    # Save file
    save_path = os.path.join(INPUT_DIR, filename)
    file.save(save_path)
    
    # Register source
    registry = _load_source_registry()
    registry[source_id] = {
        "filename": filename,
        "source_stem": source_stem,
        "status": "processing",
        "progress": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_source_registry(registry)
    
    # Trigger background (non-blocking)
    _trigger_background_ingest(source_id, save_path, filename)
    
    # Return immediately
    return jsonify({
        'source_id': source_id,
        'filename': filename,
        'status': 'processing'
    })
```

**⏱️ Thời gian:** < 500ms (chỉ save file + ghi registry)

---

### **PHASE 2: BACKGROUND PROCESSING** (Async, không block)

#### **2.1 Background Worker Thread**
```
🔄 Thread bắt đầu chạy độc lập
   ↓
📋 _background_process_source(source_id, file_path, filename)
```

**Code:** `BE/main.py:292-354`

---

#### **2.2 Step-by-Step Processing**

##### **STEP 1: Extract Text** (Progress: 0.0 → 0.1)
```
📄 extract_text(file_path)
   - PDF: PyMuPDF (fitz)
   - DOCX: python-docx
   - TXT: read file
   - Image: OCR (Tesseract)
   ↓
✅ Update registry: progress = 0.1
```

**Code:** `BE/ingest_utils.py:20-69`

---

##### **STEP 2: Semantic Chunking** (Progress: 0.1 → 0.3)
```
📝 split_text(text)
   - Dùng SemanticChunker (LangChain)
   - Model: sentence-transformers/all-MiniLM-L6-v2
   - Breakpoint threshold: 94th percentile
   - Min chunk size: 300 chars
   ↓
✅ Update registry: progress = 0.3
```

**Code:** `BE/ingest_utils.py:74-90`

---

##### **STEP 3: Process Chunks & Create Video** (Progress: 0.3 → 0.4)
```
🎬 process_and_store_chunks(chunks, video_name, timestamp)
   - Chia chunk dài thành sub-chunks (nếu > MAX_QR_CHARS)
   - Thêm metadata prefix vào mỗi chunk
   - Tạo QR code frames
   - Lưu video vào videos/
   ↓
✅ Update registry: progress = 0.4
```

**Code:** `BE/chunk_processor.py:76-249`

**Output:**
- `video_path`: đường dẫn video file
- `metadata_entries`: list các entry với parent_id, sub_order, etc.

---

##### **STEP 4: Embedding + FAISS Index** (Progress: 0.4 → 0.6)
```
🔍 append_to_index() cho từng entry:
   - Encode chunks → embeddings (all-MiniLM-L6-v2)
   - Add vào FAISS index (index/index.faiss)
   - Ghi metadata vào index/index.json
   ↓
✅ Update registry: progress = 0.6
```

**Code:** `BE/faiss_utils.py:78-110`

**Files được tạo/cập nhật:**
- `index/index.faiss` - FAISS vector index
- `index/index.json` - Metadata (text, video, timestamp, parent_id, etc.)

---

##### **STEP 5: Build Memory Tree** (Progress: 0.6 → 0.8)
```
🌳 build_memory_tree_for_sources([source_stem])
   
   Bước 5.1: Load chunks từ index
   ↓
   Bước 5.2: Tạo Document Node
      - Summarize toàn bộ document
      - Classify intent_type
      - Create embedding
   ↓
   Bước 5.3: Tạo Section Nodes (incremental)
      - Group chunks thành sections
      - Summarize từng section
      - Classify intent_type cho mỗi section
      - Append vào tree dần dần
   ↓
   Bước 5.4: Rebuild Memory Index
      - Embed tất cả nodes
      - Build FAISS index cho memory nodes
      - Lưu memory_index.faiss + memory_index.json
   ↓
✅ Update registry: progress = 0.8
```

**Code:** `BE/memory_tree.py:309-448`

**Files được tạo/cập nhật:**
- `memory/memory_trees.json` - Tree structure với nodes
- `memory/memory_index.faiss` - FAISS index cho memory nodes
- `memory/memory_index.json` - Metadata cho memory nodes

---

##### **STEP 6: Complete** (Progress: 0.8 → 1.0)
```
✅ Update registry:
   - status = "ready"
   - progress = 1.0
   ↓
🎉 Source sẵn sàng cho query!
```

**Code:** `BE/main.py:345-347`

---

#### **2.3 Error Handling**
```
❌ Nếu có lỗi ở bất kỳ step nào:
   ↓
📝 Update registry:
   - status = "error"
   - progress = 0.0
   - error = error_message
   ↓
🛑 Stop processing
```

**Code:** `BE/main.py:349-354`

---

### **PHASE 3: FRONTEND POLLING & UI UPDATE**

#### **3.1 Polling Status**
```
🔄 pollSourceStatus(source_id) được gọi ngay sau upload
   ↓
⏰ Set interval: Poll mỗi 1.5 giây
   ↓
📡 GET /sources/{source_id}/status
   ↓
📊 Update UI:
   - status: "processing" | "ready" | "error"
   - progress: 0.0 → 1.0
   - error: (nếu có)
   ↓
🛑 Stop polling nếu:
   - status === "ready" → Refresh sources từ backend
   - status === "error" → Hiển thị error message
```

**Code:** `FE/src/components/Layout/SidebarLeft.jsx:15-56`

```javascript
const pollSourceStatus = (sourceId) => {
  const poll = async () => {
    const res = await fetch(`${API_BASE}/sources/${sourceId}/status`);
    const data = await res.json();
    
    setSources((prev) =>
      prev.map((s) =>
        s.source_id === sourceId
          ? { ...s, status: data.status, progress: data.progress }
          : s
      )
    );
    
    if (data.status === "ready" || data.status === "error") {
      stopPolling(sourceId);
      if (data.status === "ready") {
        setTimeout(() => fetchSourcesFromBackend(), 500);
      }
    }
  };
  
  poll(); // Poll ngay
  pollingIntervalsRef.current[sourceId] = setInterval(poll, 1500);
};
```

---

#### **3.2 UI Display States**

##### **Status: "processing"**
```
📄 filename
⏳ "Đang phân tích tài liệu…"
📊 Progress bar: [████░░░░░░] 60%
📝 "60%"
```

**Code:** `FE/src/components/Layout/SidebarLeft.jsx:240-260`

##### **Status: "ready"**
```
📄 filename
✅ "Sẵn sàng"
📊 "150 chunks"
☑️ Checkbox enabled
```

##### **Status: "error"**
```
📄 filename
❌ "Lỗi xử lý: [error message]"
🔴 Border đỏ
☑️ Checkbox disabled
```

---

### **PHASE 4: QUERY BEHAVIOR**

#### **4.1 Query với Source đang Processing**
```
🔍 User query với source đang processing
   ↓
📡 POST /query
   ↓
🔍 Backend check source status
   ↓
✅ Cho phép query (với chunks đã có)
   ↓
📝 Response có thêm:
   {
     answer: "...",
     processing_message: "Một số tài liệu đang được xử lý..."
   }
```

**Code:** `BE/main.py:665-706`

---

#### **4.2 Query với Source Ready**
```
🔍 User query với source ready
   ↓
📡 POST /query
   ↓
🌳 Query qua Memory Tree (ưu tiên)
   - Search memory_index.faiss
   - Lấy top memory nodes
   - Load evidence chunks
   - Generate answer với intent awareness
   ↓
✅ Trả answer đầy đủ
```

**Code:** `BE/memory_tree.py:657-853`

---

## 📊 DATA FLOW DIAGRAM

```
┌─────────────┐
│   FRONTEND  │
└──────┬──────┘
       │
       │ POST /upload (file)
       ▼
┌─────────────────────────────────────┐
│         BACKEND /upload             │
│  ┌──────────────────────────────┐  │
│  │ 1. Save file                  │  │
│  │ 2. Generate source_id         │  │
│  │ 3. Write source_registry.json │  │
│  │ 4. Start background thread    │  │
│  └──────────────────────────────┘  │
│           │                         │
│           │ Return immediately      │
└───────────┼─────────────────────────┘
            │
            ▼
┌─────────────────────────────────────┐
│      FRONTEND (Optimistic UI)       │
│  ┌──────────────────────────────┐  │
│  │ Add source to list           │  │
│  │ status = "processing"        │  │
│  │ Start polling                │  │
│  └──────────────────────────────┘  │
└─────────────────────────────────────┘

            │
            │ Background Thread (Async)
            ▼
┌─────────────────────────────────────┐
│   _background_process_source()      │
│  ┌──────────────────────────────┐  │
│  │ Step 1: Extract (0.1)        │  │
│  │ Step 2: Chunking (0.3)       │  │
│  │ Step 3: Video (0.4)          │  │
│  │ Step 4: FAISS (0.6)           │  │
│  │ Step 5: Memory Tree (0.8)    │  │
│  │ Step 6: Ready (1.0)          │  │
│  └──────────────────────────────┘  │
│           │                         │
│           │ Update registry          │
└───────────┼─────────────────────────┘
            │
            ▼
┌─────────────────────────────────────┐
│   Frontend Polling (1.5s interval)  │
│  ┌──────────────────────────────┐  │
│  │ GET /sources/{id}/status    │  │
│  │ Update UI progress          │  │
│  │ Stop when ready/error       │  │
│  └──────────────────────────────┘  │
└─────────────────────────────────────┘
```

---

## 📁 FILES CREATED/UPDATED

### **Backend Files:**
1. `data/source_registry.json` - Tracking source status
2. `input_docs/{filename}` - Original uploaded file
3. `videos/{video_name}.mp4` - QR code video
4. `index/index.faiss` - FAISS chunk index
5. `index/index.json` - Chunk metadata
6. `memory/memory_trees.json` - Memory tree structure
7. `memory/memory_index.faiss` - FAISS memory index
8. `memory/memory_index.json` - Memory node metadata

### **Frontend State:**
- `sources` array với:
  - `source_id`
  - `filename`
  - `status`: "processing" | "ready" | "error"
  - `progress`: 0.0 → 1.0
  - `video_stem` (khi ready)
  - `num_chunks` (khi ready)

---

## ⚡ PERFORMANCE METRICS

- **Upload Response Time:** < 500ms
- **UI Update:** Immediate (optimistic)
- **Polling Interval:** 1.5 seconds
- **Background Processing:** Async, không block server
- **Memory Tree Build:** Incremental (document node → sections)

---

## 🔒 ERROR HANDLING

1. **File không đọc được** → status = "error", error = "Cannot read file content"
2. **Không tạo được chunks** → status = "error", error = "No chunks generated"
3. **Memory Tree build fail** → status = "error", error message
4. **Polling fail** → Stop polling, giữ nguyên UI state

---

## ✅ CHECKLIST THÀNH CÔNG

- [x] Upload trả response < 500ms
- [x] File xuất hiện ngay trên UI
- [x] Background chạy không block server
- [x] Progress bar cập nhật real-time
- [x] Query không thấy dữ liệu "ma"
- [x] Memory Tree chỉ build khi ingest xong
- [x] Error handling đầy đủ
- [x] Multiple uploads song song
- [x] Refresh page → load lại sources đã ready

---

## 🎯 KEY FEATURES

1. **Optimistic UI** - File xuất hiện ngay
2. **Non-blocking** - Server không bị block
3. **Progress Tracking** - User thấy tiến trình
4. **Error Recovery** - Xử lý lỗi rõ ràng
5. **Race Condition Safe** - File locking cho registry
6. **Incremental Build** - Memory Tree build dần

