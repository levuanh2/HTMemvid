# Mindmap UX v3 — Sinh nền + viewer mind-elixir

- **Ngày**: 2026-07-04
- **Trạng thái**: Đã duyệt (user approve cả 5 section trong phiên brainstorm)
- **Phạm vi**: FE luồng sinh mindmap + viewer; BE thêm 1 endpoint update
- **Kế thừa**: `2026-07-03-mindmap-redesign-design.md` (pipeline skeleton-first v2 — GIỮ NGUYÊN, không đụng BE pipeline/generate/cancel)

## 1. Bối cảnh & vấn đề (sau round 1)

Round 1 (skeleton-first) xong: pipeline v2, sqlite store, cache thật, cancel thật,
FE tách module + evidence drawer + overlay. Ba vấn đề UX còn lại, xác nhận bằng đọc code:

1. **"Phải reload mới thấy map"**: FE poll có hard-timeout `jobTimeout(180s)+10s`
   (`SidebarRight.jsx::startPolling`, `maxElapsedMs`). Pipeline thật chạy vài phút
   (đo 2026-07-04: ~100s cho doc 4 chunk, doc lớn lâu hơn) → FE alert lỗi và dừng
   poll trong khi BE vẫn chạy xong + lưu record. User chỉ thấy map sau khi F5.
2. **Overlay fullscreen mở NGAY khi có skeleton** (partial) → chiếm màn hình suốt
   vài phút sinh, user không chat tiếp được.
3. **Node "chưa đẹp"**: ReactFlow+ELK cho dáng box-graph vuông vức, không ra chất
   mindmap hữu cơ (nhánh cong, màu theo nhánh, fold/expand).

## 2. Quyết định nền (Q&A với user)

| Câu hỏi | Quyết định |
|---|---|
| UX lúc sinh | **Chạy nền + tự mở khi xong** — không mở overlay lúc sinh; chip tiến độ ở sidebar; KHÔNG hard-timeout FE |
| Renderer | **Thay ReactFlow+ELK bằng `mind-elixir`** (mind-elixir-core, MIT, zero-dep) — không giữ fallback ReactFlow |
| Feature giữ | Evidence drawer + relations nét đứt có toggle + export PNG + **chỉnh sửa tay có lưu** (cả 4) |
| Edit save | **Nút Lưu tường minh** — dirty indicator, `PUT /mindmaps/<id>` ghi đè record, đóng khi dirty → confirm |

## 3. Sinh nền (FE)

- Bấm "Tạo sơ đồ" → KHÔNG mở overlay. Chip tiến độ ở sidebar (tab Sơ đồ):
  spinner + label stage từ `GET /mindmap-status/<id>` (`current_node`/`progress`/
  message): "Khung xương…" → "Làm giàu nhánh i/n…" → "Tìm quan hệ…". Chip có nút
  Huỷ (dùng cancel thật hiện có).
- **Bỏ FE hard-timeout**: poll tới khi BE trả terminal status
  (`done`/`error`/`timeout`/`cancelled`). Interval giãn 2s → 5s → 10s (sau 2 phút).
- **Stall guard** (không bỏ cuộc, chỉ cảnh báo): `updated_at` của job đứng yên
  \> 5 phút → chip chuyển trạng thái cảnh báo "Có vẻ kẹt — Huỷ?" nhưng vẫn poll.
- **Xong** → toast "Sơ đồ sẵn sàng" + tự mở overlay + `fetchMindMaps()` refresh
  list. Degraded vẫn mở map + banner như cũ. Lỗi → toast lỗi.
- **Sống sót reload**: `job_id` đang chạy ghi `localStorage`
  (key `mindmap_active_job`); mount lại → resume poll. Job đã xong trong lúc vắng
  mặt → toast + list đã có record, KHÔNG tự mở overlay (tránh giật khi vừa vào
  trang). Terminal status nào cũng xoá key.
- Skeleton preview overlay bỏ; dữ liệu `partial` chỉ còn nuôi label chip.
- Toast: component nhẹ trong React layer (thay `alert()` cho đường mindmap;
  KHÔNG refactor alert toàn app).

## 4. Viewer mind-elixir

### 4.1 Adapter 2 chiều (pure, test được)

`FE/src/utils/mindElixirAdapter.js`:

- `recordToMindElixir(record) -> {nodeData, arrows, theme?}`
  - v2 nodes (flat, `parent`) → `nodeData` tree lồng nhau (id giữ nguyên,
    `title` → `topic`); v1 legacy đi qua `normalizeMindmapRecord` TRƯỚC.
  - `relations` → `arrows` (nét đứt + label — style mặc định của mind-elixir
    arrow, màu son qua cssVar).
- `mindElixirToRecord(data, baseRecord) -> record v2`
  - `getData()` → nodes flat v2 + relations.
  - **Sidecar map** `id → {note, chunk_refs, kind}` giữ ở React layer — KHÔNG
    tin mind-elixir bảo toàn field lạ qua operations. Merge lại khi save.
  - Node user tạo mới → id mới (mind-elixir tự sinh), `chunk_refs: []`,
    `kind: "idea"`; node bị user xoá → mất khỏi sidecar khi merge (không rò).
- Round-trip phải bảo toàn: cây + title + relations + (qua sidecar)
  note/chunk_refs/kind của node còn sống.

### 4.2 Integration

- Component mới `FE/src/components/mindmap/MindElixirView.jsx`: ref container +
  `new MindElixir({el, direction: SIDE, editable: true, draggable: true,
  contextMenu: true, ...})` + `init(data)`. Instance sống qua `useRef`,
  re-init khi đổi record id.
- **Theme Phòng đọc**: palette nhánh = bảng archival inks hiện có trong
  `index.css`; cssVar nền giấy/mực từ design token — KHÔNG hardcode hex mới.
- **Evidence drawer**: giữ nguyên `EvidenceDrawer.jsx`, đổi nguồn event —
  `mind.bus.addListener('selectNode', node => ...)` → mở drawer theo id (tra
  sidecar lấy note/chunk_refs). Node không có chunk_refs → "Chưa có bằng chứng".
- **Relations toggle**: bật/tắt layer arrows bằng CSS ẩn layer (class chính
  xác của arrow layer chốt lúc implement — kiểm DOM thật).
- **Edit + Lưu**: mọi `operation` event → set dirty (chấm "chưa lưu" trên
  toolbar). Nút **Lưu**: `mindElixirToRecord` → `PUT /mindmaps/<id>` → toast
  "Đã lưu", clear dirty. Đóng overlay khi dirty → `window.confirm`.
- **Export PNG giữ**: chạy trên container mind-elixir; thử `html-to-image`
  hiện có trước, không đạt → chuyển `modern-screenshot` (đường chính thức của
  mind-elixir). Tên file giữ format `mindmap-<title>-<yyyymmdd>.png`, nền đặc.
- Giữ: fullscreen overlay + Esc đóng + degraded banner + nút "Tạo lại" (force).
- **Bỏ** (xoá sau khi view mới xanh): `useElkLayout.js`, `RelationEdge.jsx`,
  `MindmapNodeCard.jsx`, `MindmapView.jsx` (ReactFlow), minimap, focus/overview
  mode (mind-elixir có fold/expand + zoom/drag thay). Dep `reactflow`/`elkjs`
  chỉ gỡ khỏi package.json nếu không nơi nào khác dùng (grep trước).

## 5. BE — một endpoint

`PUT /mindmaps/<id>`:

- Body = record đã sửa (shape v2). Server: 404 nếu id không tồn tại trong
  `mindmaps.sqlite`; validate nodes qua `sanitize_nodes` + relations qua
  `validate_relations` (tái dùng `services/mindmap/pipeline/schema.py`);
  **giữ nguyên** `id`/`content_hash`/`created_at`/`sources` từ record gốc
  (body không đè được); set `updated_at` (ISO Z) + `generator.edited = true`;
  ghi qua `store.save_record` (INSERT OR REPLACE sẵn có).
- Record đã edit vẫn giữ `content_hash` → cache-hit generate (không force) trả
  bản user đã sửa — chủ ý (bản curated quý hơn bản máy sinh lại).
- Không đổi gì generate/status/cancel/delete.

## 6. Testing

1. **Vitest (pure)**: adapter round-trip (v2→ME→v2; sidecar bảo toàn
   note/chunk_refs/kind; node mới → refs rỗng + kind idea; node xoá không rò
   sidecar; relations↔arrows; v1 legacy qua normalize); stall-guard (fake
   timers); poll-until-terminal không có hard-timeout (fake timers).
2. **Pytest**: `PUT /mindmaps/<id>` — 404 id lạ, sanitize node rác, field bảo
   toàn (id/hash/created_at/sources), `generator.edited` set, body relations
   invalid bị lọc.
3. **Manual smoke**: (a) sinh doc lớn (>190s) → chip chạy, KHÔNG alert timeout,
   tự mở khi xong; (b) reload giữa chừng → resume poll, xong → toast, không tự
   mở; (c) edit (đổi tên, thêm node, kéo, vẽ arrow) + Lưu + F5 → bản sửa còn
   nguyên; (d) export PNG mở được; (e) map v1 legacy mở không vỡ.

## 7. Phân công codex CLI

| Ai | Việc | Lý do |
|---|---|---|
| **Claude (chính)** | Adapter + MindElixirView + theme; background flow (chip/toast/auto-open/no-timeout); edit/dirty/save UX; rewire drawer | Core UX, nhiều quyết định, cần TDD |
| **codex (phụ)** | BE `PUT /mindmaps/<id>` + pytest; localStorage resume-poll helper + vitest; export PNG trên container mới; xoá file ReactFlow chết sau khi view mới xanh | Cơ khí, spec rõ |

Dispatch: `codex exec -C <dir> -s workspace-write --skip-git-repo-check "..."`.
Giao task codex SAU khi adapter interface + endpoint contract chốt trong plan.

## 8. Ngoài phạm vi

- Refactor `alert()` toàn app sang toast (chỉ làm đường mindmap).
- Realtime skeleton preview trong overlay (đã bỏ chủ ý — sinh nền).
- SSE/streaming progress (poll đủ).
- Undo history sâu cho edit (mind-elixir có undo built-in tới đâu dùng tới đó).
