# Spec: QR video làm kho text canonical + index.json gọn (sidecar sqlite)

Ngày: 2026-06-30 · Nhánh: `refactor/microservices-restructure`

## Context

Sau khi thêm late chunking, `index.json` lưu CẢ `text` lẫn vector/metadata cho mỗi chunk
(~13MB cho 245 chunk) trong khi QR video (`videos/*.mp4`) cũng chứa chính text đó dạng QR —
**trùng lặp**, và video hiện là "write-only" (gần như không ai đọc). Người dùng muốn QR video
có vai trò thật (đúng bản sắc "MemVid") đồng thời `index.json` gọn lại.

Ràng buộc cứng phát hiện qua audit (codex): **BM25** (`hybrid._ensure_loaded`) build corpus từ
TOÀN BỘ text mỗi khi process start / `index.json` đổi mtime; nhiều consumer khác cũng đọc text
hàng loạt (rebuild index, memory-tree summary, mindmap, `/sources`, summary). Nên text PHẢI nạp
được hàng loạt nhanh — decode video on-demand từng frame không phục vụ nổi các path này.

## Mục tiêu

1. `index.json` **không còn `text`** — chỉ pointer `(video, frame_index)` + metadata + (vector ở FAISS).
2. QR video = kho **canonical/portable** + nguồn **recovery** (đã verify khi ghi).
3. Text runtime phục vụ BM25/bulk/top-k từ **sidecar `chunks.sqlite`** (dẫn xuất, tái dựng từ video).
4. Vận hành thường **không decode video** (text ghi sqlite ngay lúc ingest). Decode video chỉ ở recovery.
5. Tương thích ngược: index cũ (text inline) vẫn chạy.

## Non-goals

- Không giảm RAM của BM25 (giới hạn cố hữu — BM25 cần toàn corpus trong RAM).
- Không giảm tổng disk (text nằm ở video + sqlite). Lợi là: index.json sạch/gọn, video có vai trò thật, hết trùng text trong index.
- Không đụng `embedding` list rút gọn trong index.json (mindmap KMeans dùng) — để lần sau.
- Không đổi cơ chế vector/late chunking.

## Kiến trúc: 3 store

| Store | Vai trò | Nội dung/khóa |
|---|---|---|
| `videos/*.mp4` | Canonical/portable + recovery | QR của text từng chunk; thứ tự frame = thứ tự `metadata_entries` sau lọc |
| `index/index.json` | Pointer + metadata (GỌN) | `chunk_id → {video, frame_index, source_stem, source_id, heading_path, category, date, language, parent_id, sub_order, total_parts, is_subchunk}` — KHÔNG `text` |
| `index/chunks.sqlite` | Text runtime (dẫn xuất) | bảng `chunks(chunk_id INTEGER PRIMARY KEY, text TEXT)`; gitignore; tái dựng từ video |
| `index/index.faiss` | Vector | (không đổi) |

## Module mới: `BE/app/domains/vectorstore/chunk_text_store.py`

Tầng truy cập text DUY NHẤT, mọi consumer gọi qua đây:
- `put_many(items: list[tuple[int, str]])` — ghi text vào sqlite (lúc ingest).
- `get_text(chunk_id) -> str | None` — thứ tự fallback: (1) sqlite; (2) `index.json` inline `text` (back-compat); (3) decode `(video, frame_index)` qua LRU cache; (4) None.
- `get_texts(ids) -> dict[int,str]` — batch (top-k).
- `iter_all() -> Iterable[tuple[int,str]]` — cho BM25/bulk; nguồn sqlite (fallback inline text nếu sqlite trống → back-compat index cũ).
- `mtime() -> float` — để BM25 guard cache.
- `rebuild_from_videos()` — recovery: decode video theo thứ tự, map theo `frame_index`, nạp lại sqlite.
- Đường dẫn sqlite bám theo `store.INDEX_DIR` (giống cách patch path trong test).

## Luồng ingest (sửa)

1. `chunk_processor.process_and_store_chunks`: sau khi lọc frame hỏng, **gán `entry["frame_index"] = i`** theo thứ tự cuối cùng (khớp thứ tự ghi video). (video non-fatal giữ nguyên.)
2. `ingest_graph.embed_index_node`: như hiện tại, truyền `custom_metadata` (giờ kèm `frame_index`, `video`).
3. `store.append_to_index` / `append_chunks_to_lc_index`:
   - Ghi vào `index.json`: **bỏ field `text`** (giữ pointer/metadata). Nếu `video_path` rỗng (video lỗi) → **giữ `text` inline** cho chunk đó (an toàn, không mất data).
   - Ghi text vào `chunks.sqlite` qua `chunk_text_store.put_many` (luôn, kể cả khi có video).
   - Vector: precomputed (late chunking) như hiện tại.

## Đọc text (đổi ~các site codex liệt kê)

Thay `meta[id]["text"]` / `v.get("text")` bằng `chunk_text_store`:
- `hybrid._ensure_loaded` (BM25 corpus) → `iter_all()`; guard theo `chunk_text_store.mtime()`.
- `hybrid` result materialization (top-k) → `get_texts()`.
- `store.search_index` (top-k) → `get_texts()`.
- `memory/tree.py` (`_join_chunk_text`, evidence) → `get_text`/`get_texts`.
- `services/mindmap/worker.py` (collect_chunks_for_sources, cluster/label) → `get_texts`.
- `main.py` `/sources` (1184), summary (1551-1566) → `get_texts`.
- `store.rebuild_chunk_index` / `rebuild_lc_index_from_meta` (đọc text để re-embed) → `get_text`.

## Sửa 3 bug tiên quyết (recovery đáng tin)

1. `video_utils.decode_video_qr`: đang dùng `set` → **mất thứ tự**. Đổi: trả list theo thứ tự frame; parse `frame_index`/metadata để map chính xác.
2. `video_utils.save_qr_frames_to_video`: ép late-chunk video chỉ dùng `mp4v`/`.mp4` (bỏ nhánh `.avi`) để khớp `rebuild_index_from_video.glob("*.mp4")`; hoặc mở rộng glob sang `.avi`. → Chọn: **chỉ .mp4** (đơn giản, khớp rebuild).
3. `frame_index` chưa lưu → gán ở `process_and_store_chunks` (mục Luồng ingest #1) và ghi vào metadata.

## Tương thích ngược & migration

- Index cũ có `text` inline: `chunk_text_store` đọc inline khi sqlite chưa có → chạy bình thường, không bắt buộc migrate.
- Lệnh tùy chọn `rebuild_sqlite_from_videos` (CLI trong `app/scripts/`) để dựng `chunks.sqlite` cho data cũ. Không bắt buộc cho data mới (ingest tự ghi sqlite).

## Error handling

- Video lỗi → text vẫn ở sqlite + giữ inline trong index.json cho chunk đó (không mất data).
- sqlite mất/hỏng → `rebuild_from_videos()` (recovery); nếu cũng không có video → chunk "unreadable", fail-soft (bỏ qua, log).
- decode-on-demand chỉ là tầng cuối; bọc try/except, lỗi → None.

## Testing

- Unit: `chunk_text_store` 3 tầng fallback + LRU; `put_many`/`get_texts`/`iter_all`; `frame_index` gán đúng sau khi lọc frame; `decode_video_qr` giữ thứ tự.
- Integration (graph, fake encoder): ingest 1 doc → `index.json` KHÔNG có `text`, có `frame_index`/`video`; `chunks.sqlite` có text; query + BM25 trả đúng qua tầng truy cập. Video lỗi → vẫn index + query được (text từ sqlite + inline fallback).
- Smoke thật (bge-m3): recovery — xóa `chunks.sqlite`, `rebuild_from_videos()`, query khớp.
- Regression: `python -c "import app.graphs.ingest_graph; import app.graphs.query_graph"`; toàn bộ suite (global python).

## Rủi ro

- Nhiều site đọc text (codex liệt kê ~15) → đổi sót sẽ vỡ. Giảm rủi ro bằng tầng truy cập DUY NHẤT + test ở mỗi consumer chính.
- Thứ tự frame chỉ tin cậy nếu `frame_index` gán sau lọc — bám đúng điểm này.
- `chunks.sqlite` ghi đồng thời (ingest nhiều file) → dùng 1 connection/khoá ghi đơn giản, WAL.
