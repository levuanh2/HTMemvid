# Thiết kế lại Sinh Sơ đồ Tư duy (Mindmap) — Skeleton-first

- **Ngày**: 2026-07-03
- **Trạng thái**: Đã duyệt (user approve từng section trong phiên brainstorm)
- **Phạm vi**: BE pipeline sinh mindmap + lưu trữ + API + FE viewer

## 1. Bối cảnh & vấn đề

Luồng hiện tại: FE `POST /generate-mindmap {sources, q}` → thread → `MINDMAP_GRAPH`
(LangGraph 3 node nhưng toàn bộ logic nằm trong 1 node) → `run_mindmap_generation`
(`services/mindmap/worker.py`, 2113 dòng: 3 mode × 7 strategies × fallback chain ×
LLM budget × visual diagram LLM) → `append_mindmap` → `memory/mindmaps.json`.

Các điểm lệch đã xác nhận bằng đọc code:

1. **Cache "ma"**: `_content_hash()` + `CACHE_VERSION` tồn tại, progress in "Đang lưu
   vào cache..." nhưng không có lookup/save nào — mỗi lần sinh là full-price LLM.
   Docs MINDMAP_WORKFLOW §5 mô tả cache không tồn tại.
2. **FE không gửi `mode`**: SidebarRight chỉ gửi `{sources, q}` — máy móc
   fast/balanced/quality không kích hoạt được từ UI; `q` bị BE bỏ qua.
3. **Hai artifact trùng lặp**: `nodes` (cây) và `diagram` (semantic graph) sinh riêng,
   lưu riêng, FE `normalizeHierarchyFromData` gộp lại làm một.
4. **Embedding đọc từ index.json slim**: worker lấy `m["embedding"]` để merge/cluster
   nhưng index slim chỉ còn embedding prefix → cluster trên vector cụt, không ai báo.
5. **Schema drift không version**: mindmaps.json lẫn record cũ (thiếu diagram/mode) và mới.
6. **Lưu = rewrite cả file**; không xoá mindmap khi xoá source; job state nhân 3 nơi
   (dict in-memory + jobs.sqlite + LangGraph checkpointer).
7. **Ranh giới service bị xuyên**: mindmap-service (gRPC) tự đọc index.json +
   chunks.sqlite + videos, trái thiết kế "monolith sở hữu toàn bộ state trên đĩa".
8. **Huỷ không thật**: nút Huỷ chỉ dừng polling; job vẫn chạy và vẫn append.

## 2. Quyết định nền (từ Q&A với user)

| Câu hỏi | Quyết định |
|---|---|
| Ưu tiên sửa gì | Cả 4: chất lượng nội dung, tốc độ/độ tin cậy, kiến trúc/lưu trữ, UI/UX |
| Ngân sách thời gian | **Vài phút cũng được** — chất lượng > tốc độ; tin cậy = progress rõ + cache + không rơi rác |
| Mindmap "đúng" là gì | **Khung xương theo cấu trúc tài liệu + quan hệ ngữ nghĩa vẽ chéo giữa nhánh** (một model thống nhất tree + cross-edges) |
| Bao nhiêu mode | **1 đường duy nhất** — không mode/strategy picker; adaptive nội bộ; xoá toàn bộ máy móc mode |
| Phương án pipeline | **A — Skeleton-first** (B: LLM tự dựng toàn bộ, C: progressive streaming — C để phase sau) |

Cơ sở đã xác minh: ingest hiện tại **persist `heading_path`** vào metadata chunk
(`ingest_graph.py` embed_index_node, khi headings khớp 1:1). Dữ liệu cũ (trước
late-chunking) chưa có field này → cần fallback hoặc re-ingest.

## 3. Data model & lưu trữ

### 3.1 Record mindmap schema v2 (MỘT artifact)

```jsonc
{
  "id": "uuid",
  "schema_version": 2,
  "title": "Đề xuất dự án X",
  "sources": ["de_xuat_du_an_docx"],          // canonical stems (shared/source_id)
  "content_hash": "sha256(...)",               // cache key
  "created_at": "2026-07-03T...Z",
  "nodes": [
    { "id": "n1", "parent": null, "kind": "root",    "title": "...", "note": "",  "chunk_refs": [], "order": 0 },
    { "id": "n2", "parent": "n1", "kind": "section", "title": "2. Phương pháp", "note": "1-2 câu tóm ý", "chunk_refs": ["12","13"], "order": 2 },
    { "id": "n5", "parent": "n2", "kind": "idea",    "title": "So khớp QR",     "note": "...", "chunk_refs": ["13"], "order": 0 }
  ],
  "relations": [
    { "source": "n2", "target": "n7", "type": "leads_to", "label": "dẫn tới" }
  ],
  "generator": { "pipeline": "skeleton_v1", "model": "qwen2.5:14b", "elapsed_sec": 142, "degraded": false, "missing": [] }
}
```

- `kind`: `root | section | idea | detail`. `relations.type`:
  `relates_to | leads_to | causes | supports | contrasts | contains`.
- **Mỗi node mang `chunk_refs`** — provenance bắt buộc: click node ra trích đoạn gốc
  (khớp hợp đồng evidence của Phòng đọc); node LLM sinh mà không bám chunk nào là tín hiệu bịa.
- `degraded: true` + `missing: ["relations", ...]` khi stage LLM fail — không bao giờ trả rác im lặng.

### 3.2 Lưu trữ: sqlite

- File mới `memory/mindmaps.sqlite`, bảng
  `mindmaps(id TEXT PK, content_hash TEXT, sources_json TEXT, created_at TEXT, record_json TEXT)`
  (+ index theo `content_hash`).
- Cache thật = lookup theo `content_hash`; nút "Tạo lại" gửi `force=true` bỏ qua cache.
- Xoá source → xoá mindmap có source đó (nối vào `/delete-source` và `DELETE /sources/<id>` — hiện bỏ sót).
- Migrate `mindmaps.json` → sqlite MỘT lần lúc khởi động (record cũ gắn `schema_version: 1`,
  giữ nguyên nội dung); file json giữ làm backup, không ghi thêm.
- Hết load-all + rewrite cả file mỗi lần append.

### 3.3 content_hash

`sha256(pipeline_version + sorted(canonical_stems) + text các chunk thuộc sources)`.
Đổi pipeline_version khi đổi prompt/logic để tự vô hiệu cache cũ.

## 4. Pipeline sinh: 4 stage trên LangGraph thật

### 4.1 Graph (langgraph 0.2.x — GIỮ PIN, không đụng)

```
StateGraph(MindmapState)

CollectInput ─▶ Skeleton ─▶ Enrich ─▶ Relations ─▶ AssemblePersist
     │              │           │           │              │
     └──────── mọi node: check cancel flag; lỗi hệ thống → ErrorHandler;
               lỗi LLM ở Enrich/Relations KHÔNG route error mà set degraded rồi đi tiếp
```

- **CollectInput** (monolith): gom `chunks` (text qua `chunk_text_store`), `heading_path`
  từ index meta, section nodes từ memory tree. Worker/service KHÔNG tự đọc đĩa nữa.
- **Skeleton** (0 LLM, <1s): dựng cây mục lục từ `heading_path` (h1→h2→h3, giữ thứ tự
  tài liệu). Fallback theo bậc: (1) memory-tree sections, (2) clustering TF-IDF trên text
  (KHÔNG dùng embedding prefix từ index.json). Ghi skeleton vào job record ngay
  (`partial`) — FE render khung xương lập tức.
- **Enrich** (LLM, song song theo nhánh top-level, qua llm-gateway): mỗi nhánh 1 call nhỏ
  → chỉnh `title`, viết `note` 1-2 câu, sinh 2-5 node `idea` con, gán `chunk_refs`.
  JSON schema per call; parse bằng `_repair_json_text` (string-aware, tái dùng) + retry 1 lần.
- **Relations** (1 LLM call): input = title+note các nhánh; output cross-edges có nhãn.
  Validate: id phải tồn tại, bỏ self-loop, bỏ edge trùng cạnh cây, cap ~20 relations.
- **AssemblePersist**: pydantic schema v2, sanitize, cap tổng ~120 node, kiểm
  `chunk_refs` tồn tại trong input, ghi sqlite + cache.

### 4.2 Semantics quan trọng

1. **Không bao giờ trả rác**: skeleton là kết quả tối thiểu có nghĩa. LLM chết →
   record vẫn ra, `degraded: true`, FE hiện badge thay vì im lặng.
2. **Cancel thật**: cờ cancel trong jobs store (`POST /mindmap-cancel/<id>`), check giữa
   các node và giữa các call trong Enrich — huỷ là dừng, KHÔNG persist.
3. **Timeout**: chỉ còn per-LLM-call (đo thật trên phần cứng, theo bài học playbook)
   + deadline tổng; warmup/model-load nằm NGOÀI vùng timeout (bài học rerank/NLI).
4. **Checkpointer có việc**: crash giữa Enrich → resume không mất skeleton.
5. **gRPC per-stage**: mindmap-service thành stateless executor — RPC `Skeleton` /
   `EnrichBranch` / `Relations` nhận input qua wire (in-proc mặc định khi không set
   `MINDMAP_SERVICE_ADDR`, qua factory như hiện tại).
6. **Xoá bỏ**: 3 mode, 7 strategies, fallback chain, `LlmCallBudget`,
   visual diagram LLM, `TimeoutTracker` phức tạp. Worker ~2113 dòng → ước ~500-600.
7. `MindmapState` thêm field: `skeleton`, `enriched_nodes`, `relations`, `degraded`,
   `cancel_requested` — PHẢI có test dựng graph thật (bài học conftest-mock + pydantic pin <2.11).

## 5. API surface (đổi tối thiểu, FE cũ không vỡ)

| Endpoint | Thay đổi |
|---|---|
| `POST /generate-mindmap` | Body `{sources, force?}`. `mode`/`strategy`/`q` gửi lên thì bỏ qua (back-compat). Cache hit + không force → trả `{status:"done", result}` ngay, không tạo job |
| `GET /mindmap-status/<id>` | Giữ shape; thêm `partial` (skeleton preview) khi running + `degraded` khi done |
| `POST /mindmap-cancel/<id>` | MỚI — set cờ cancel trong jobs store |
| `GET /mindmaps` | Giữ; đọc sqlite; trả cả record v1 (FE normalize sẵn) |
| `DELETE /mindmaps/<id>` | Giữ; xoá trong sqlite |
| `/delete-source`, `DELETE /sources/<id>` | Thêm bước xoá mindmap của source |

Job state gom về MỘT chỗ: jobs.sqlite (bỏ dict in-memory `mindmap_jobs`).

## 6. UI/UX — "Bản đồ tri thức" (Phòng đọc)

Chủ thể: người học tra cứu tài liệu nghiên cứu tiếng Việt. Một việc duy nhất của màn
hình: **nhìn thấy xương sống tài liệu và lần theo bằng chứng**.

- **Bố cục**: bỏ modal chật → overlay toàn màn hình (layer trên MainLayout, không cần
  route mới). Nền giấy Phòng đọc, map chiếm trọn, toolbar mỏng phía trên.
- **Ngôn ngữ thị giác**: cạnh cây = nét mực liền (bảng archival inks sẵn có);
  **relations = nét đứt màu son (seal accent) có nhãn chữ nhỏ** — chữ ký thị giác của
  map, toggle bật/tắt. Phân cấp node bằng cỡ chữ/đậm nhạt, không đổi hình dạng.
- **Signature interaction**: click node → **ngăn kéo bằng chứng** trượt từ phải:
  `note` + trích đoạn từ `chunk_refs`; bấm trích đoạn → nhảy sang ChatArea hỏi tiếp
  về đoạn đó. Mindmap = mục lục sống của tài liệu, không phải tranh tĩnh.
- **Lúc sinh**: skeleton hiện ngay (~1s); node "thở" nhẹ khi đang enrich (tôn trọng
  `prefers-reduced-motion`); degraded → dải thông báo "Bản đồ chưa có tầng quan hệ — Tạo lại".
- **Giữ**: ReactFlow + ELK, focus/overview mode, minimap. **Thêm**: export PNG.
- **Refactor**: `MindMapModal.jsx` (2622 dòng) tách thành `FE/src/components/mindmap/`
  (viewer, node, evidence-drawer, toolbar, layout hooks).
- Quality floor: responsive mobile, keyboard focus, reduced-motion.

## 7. Testing

1. **Unit thuần** (không model): skeleton builder từ fixture heading (có/không heading,
   tiếng Việt có dấu, sub-split); relations validator (id lạ, self-loop, trùng cạnh cây);
   `content_hash` ổn định; cap node; migration json→sqlite.
2. **Graph thật**: `build_mindmap_graph` 5 node với runner stub — bắt lỗi
   pydantic/langgraph khi đổi `MindmapState` (bài học conftest-mock).
3. **Route**: cache hit trả thẳng; `force` bypass; cancel giữa chừng không persist;
   delete-source kéo theo mindmap.
4. **Smoke thủ công** LLM thật (script scratchpad): 1 doc có heading + 1 PDF không heading.
5. **FE**: normalize record v1/v2 snapshot test.

Test env: chạy pytest bằng global `python` (bài học môi trường).

## 8. Phân công codex CLI

| Ai | Việc | Lý do |
|---|---|---|
| **Claude** | Pipeline 4 stage + prompts; `MindmapState` + graph 5 node; cache/cancel semantics; FE viewer + evidence drawer | Core, nhiều bẫy (playbook), cần TDD |
| **codex** | `mindmaps.sqlite` store CRUD + migration từ json; proto per-stage RPC + regen `shared/proto/gen`; nối delete-source→xoá mindmap; xoá dead code (mode/strategy/budget/visual-LLM) SAU khi pipeline mới xanh; nút export PNG | Cơ khí, spec rõ, ít quyết định |

Dispatch: `codex exec -C <dir> -s workspace-write --skip-git-repo-check "..."`.
Giao task cho codex SAU khi schema v2 + interface store được chốt trong plan
(tránh codex tự chế schema).

## 9. Ngoài phạm vi (phase sau)

- Progressive streaming từng nhánh lên FE qua SSE (phương án C) — pipeline đã emit
  progress theo nhánh, chỉ cần thêm transport sau.
- Sinh sẵn mindmap lúc ingest (precompute).
- Chỉnh sửa mindmap thủ công (kéo thả, đổi tên node).
