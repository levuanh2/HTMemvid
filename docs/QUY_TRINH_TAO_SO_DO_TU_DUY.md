# QUY TRÌNH HỆ THỐNG TẠO SƠ ĐỒ TƯ DUY

## I. TỔNG QUAN: SƠ ĐỒ TƯ DUY TRONG HỆ THỐNG DÙNG ĐỂ LÀM GÌ?

Hệ thống tạo ra hai loại sơ đồ tư duy với mục đích khác nhau:

**Memory Tree (Cây Trí nhớ)**: Được tạo tự động sau khi người dùng tải tài liệu lên. Đây là cấu trúc cây phản ánh logic của tài liệu, giúp hệ thống hiểu được cấu trúc và mối quan hệ giữa các phần để trả lời câu hỏi có ngữ cảnh sâu hơn.

**Mind Map (Sơ đồ Tư duy)**: Được tạo theo yêu cầu của người dùng khi họ muốn xem tổng quan kiến thức. Đây là sơ đồ trực quan giúp người học dễ ghi nhớ và nắm bắt cấu trúc kiến thức. Pipeline hiện tại là **skeleton-first**: có đúng MỘT đường xử lý (không còn nhiều mode/strategy chọn theo kích thước dữ liệu) — xem chi tiết ở `docs/MINDMAP_WORKFLOW.md`.

Cả hai loại đều được xây dựng từ cùng một nguồn dữ liệu ban đầu nhưng qua các quy trình xử lý khác nhau để phục vụ mục đích riêng.

## I.5. CẬP NHẬT THIẾT KẾ LƯU CHUNK TEXT

Phần này mô tả mô hình lưu trữ/truy xuất chunk text hiện hành:

- `chunks.sqlite` là nơi lưu raw text của chunk trong runtime ingest.
- `index.json` giữ metadata mảnh và pointer như `video`, `frame_index`, `source_stem`, `parent_id`, `sub_order`, embedding prefix.
- `index.json` có thể còn `text` ở một số entry cũ hoặc trường hợp tương thích, nhưng đó chỉ là fallback.
- Memory Tree và Mind Map đều đọc text qua `chunk_text_store.get_text()`.

```mermaid
flowchart LR
    A[Semantic chunks] --> B[chunks.sqlite<br/>raw text]
    A --> C[index.json<br/>pointer + metadata]
    A --> D[video QR frames]
    B --> E[chunk_text_store.get_text]
    C --> E
    D --> E
    E --> F[Memory Tree / Mind Map]
```

Thứ tự fallback:

1. `chunks.sqlite`
2. inline `text` trong `index.json`
3. decode on-demand từ video QR frames

---

## II. NGUỒN DỮ LIỆU ĐẦU VÀO

Khi người dùng tải lên một tài liệu, hệ thống trích xuất văn bản thô từ PDF, DOCX, TXT hoặc ảnh để đưa vào pipeline xử lý.

---

## III. CÁC BƯỚC XỬ LÝ CHÍNH

### BƯỚC 1: CHUẨN BỊ DỮ LIỆU

- Trích xuất nội dung từ file gốc
- Chuẩn hóa văn bản

### BƯỚC 2: CHIA NHỎ THEO NGỮ NGHĨA

- Tạo semantic chunks
- Giữ ranh giới ý nghĩa thay vì cắt theo kích thước cố định

### BƯỚC 2.5: LƯU CHUNK TEXT VÀ METADATA

- Raw chunk text được ghi vào `chunks.sqlite`
- Metadata của chunk được ghi vào `index.json`
- Video QR frames là nguồn recovery cuối cùng

### BƯỚC 3: TẠO MEMORY TREE

Memory Tree dùng chunk metadata từ `index.json`, nhưng khi cần nội dung thật của chunk để tóm tắt document/section, hệ thống đi qua `chunk_text_store.get_text()`.

Điều này có nghĩa:

- node chỉ cần giữ `chunk_refs`
- text chi tiết được lấy lại theo nhu cầu
- cùng một abstraction được dùng cho join text, build human context và recovery

### BƯỚC 4: HOÀN THIỆN MEMORY TREE

- Build document node
- Build section nodes
- Embed summary của node
- Lưu `memory_trees.json`, `memory_index.faiss`, `memory_index.json`

### BƯỚC 5: TẠO MIND MAP

Mind Map dùng `app/domains/mindmap/input_collector.py::collect_mindmap_input` để gom input: lọc
metadata chunk từ `index.json` theo source, resolve text qua `chunk_text_store.get_text()`, merge
sub-chunk vào chunk cha, và giữ lại `heading_path` (từ metadata) + `tree_sections` (từ Memory Tree)
làm nguyên liệu dựng khung. Từ đó hệ thống tính `content_hash` (sha256 của phiên bản pipeline +
nguồn + toàn bộ text chunk) làm khoá cache trong `memory/mindmaps.sqlite` — trùng hash và không ép
tạo lại (`force`) thì trả ngay kết quả cũ, không chạy lại pipeline.

Khi cần sinh mới, một LangGraph 5 node xử lý theo đúng MỘT đường (không rẽ nhánh theo mode/kích
thước dữ liệu):

1. **Skeleton** (0 LLM, đo thật <1s): dựng cây từ `heading_path` → fallback section của Memory Tree
   → fallback cụm TF-IDF/KMeans → fallback single-root. Đây là khung xương tất định, không bao giờ
   là rác kể cả khi các bước sau thất bại.
2. **Enrich** (LLM song song theo nhánh): mỗi nhánh section top-level gọi LLM 1 lần để sinh tiêu đề
   gọn hơn, tóm ý, và 2-5 ý con kèm `chunk_refs` (bị lọc lại theo id hợp lệ của nhánh — chặn LLM bịa
   tham chiếu). Nhánh nào lỗi/timeout thì GIỮ NGUYÊN khung xương của nhánh đó (không mất dữ liệu),
   đánh dấu `degraded`.
3. **Relations** (1 LLM call): tìm quan hệ ngữ nghĩa chéo giữa các nhánh (khác quan hệ cha-con sẵn
   có trong cây), validate lại id/trùng cạnh/tự-trỏ, cap 20 quan hệ.
4. **AssemblePersist**: ghép thành record schema v2 (`nodes` + `relations` + `generator` mang cờ
   `degraded`/`missing`) rồi lưu vào `mindmaps.sqlite`. Record LUÔN được tạo, dù các bước LLM có
   thất bại toàn bộ.

Chi tiết đầy đủ từng node, cơ chế cache, cách huỷ job, và số đo thời gian thật xem
`docs/MINDMAP_WORKFLOW.md`.

```mermaid
flowchart TD
    A[index.json metadata + heading_path] --> B[chunk_text_store.get_text]
    T[Memory Tree sections] -.fallback.-> SK
    C[chunks.sqlite] --> B
    D[inline text fallback] --> B
    E[decode QR frame fallback] --> B
    B --> F[collect_mindmap_input: logical chunks + content_hash]
    F --> CH{cache hit theo content_hash?}
    CH -->|có, !force| R[Trả record cũ ngay]
    CH -->|không| SK[Skeleton 0 LLM]
    SK --> EN[Enrich LLM song song theo nhánh]
    EN --> RE[Relations 1 LLM call]
    RE --> AP[AssemblePersist -> mindmaps.sqlite]
```

---

## IV. DỮ LIỆU LƯU TRỮ CUỐI CÙNG

### Memory Tree

- `memory/memory_trees.json`
- `memory/memory_index.faiss`
- `memory/memory_index.json`

### Mind Map

- `memory/mindmaps.sqlite` (bảng `mindmaps`, index theo `content_hash` — khoá cache thật)
- `memory/mindmaps.json` (định dạng cũ, chỉ còn được migrate 1 lần vào sqlite lúc khởi động cho dữ
  liệu tạo trước khi có pipeline skeleton-first; không còn được ghi mới)

### Chunk Storage Layer

- `index/chunks.sqlite` giữ raw text
- `index/index.json` giữ metadata/pointer

---

## V. KẾT LUẬN

Memory Tree và Mind Map cùng chia sẻ một cơ chế đọc chunk text thống nhất:

- ưu tiên `chunks.sqlite`
- fallback `index.json` inline text
- recovery từ video QR frames khi cần

Thiết kế này giúp `index.json` gọn hơn, giảm coupling giữa metadata và raw text, đồng thời giữ được khả năng phục hồi dữ liệu khi cần.

Riêng Mind Map có thêm một tầng cache thật của riêng nó (`memory/mindmaps.sqlite`, khoá theo
`content_hash`) mà Memory Tree không có — sinh mindmap trùng nội dung nguồn sẽ trả lại kết quả cũ
ngay lập tức thay vì chạy lại LLM.
