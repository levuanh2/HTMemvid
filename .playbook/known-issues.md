# Known Issues

## Late chunking + EMBEDDING_MODEL_NAME chưa set → tách không gian embedding (MiniLM vs bge-m3)

- **Triệu chứng:** Bật late chunking nhưng query/memory/một số path lại embed bằng
  all-MiniLM (384) trong khi chunk index là bge-m3 (1024) → cosine vô nghĩa, retrieve trật.
  Trong Docker: log `model=sentence-transformers/all-MiniLM-L6-v2 dim=384`.
- **Nguyên nhân:** `get_embedding_model(model_name)` tôn trọng tên caller truyền; nhiều nơi
  truyền `store.MODEL_NAME` (đóng băng lúc import = default all-MiniLM khi env chưa set).
  Late chunking là scheme TOÀN CỤC nhưng lại nhận model ngắn-context → vỡ.
- **Cách xử lý (đã làm):** dưới late chunking, `get_embedding_model` BỎ QUA `model_name`
  caller, luôn resolve `get_late_chunk_encoder(os.getenv("EMBEDDING_MODEL_NAME") or None)`
  (env hoặc bge-m3) — đồng nhất với `get_embeddings`. ⇒ memory tree / mindmap /
  `_optional_prefix_embedding_list` / query đều dùng MỘT encoder. Regression:
  `test_embedding_late_chunk::test_get_embedding_model_ignores_caller_minilm_default`.
- **Prevention:** Docker vẫn nên set `EMBEDDING_MODEL_NAME=BAAI/bge-m3` (đã thêm vào compose)
  cho rõ ràng. Đổi model → rebuild index. (Phát hiện qua **codex audit** + Docker log thật.)

## Video QR ghi 0 frame trong container headless (opencv-python-headless)

- **Triệu chứng:** `Completed: 0/N frames written successfully` / `Failed to write frame`;
  file .mp4 tạo ra rỗng/hỏng. Local Windows cũng từng in 0/N dù video vẫn tạo.
- **Nguyên nhân:** `writer.isOpened()` chỉ chứng minh writer mở được, KHÔNG chứng minh
  codec↔container encode được; `cv2.VideoWriter.write()` trả None (không tin được làm
  tín hiệu thành công). Code cũ thử XVID/DIVX/MJPG nhưng ghi vào `.mp4` (sai cặp).
- **Cách xử lý (đã làm):** `video_utils.save_qr_frames_to_video` ghép codec↔đuôi (mp4v/avc1→.mp4,
  MJPG/XVID→.avi), ghi xong rồi `_video_is_valid()` (tồn tại + size + `VideoCapture.read()`
  đọc được ≥1 frame) mới chấp nhận; không thì thử codec/đuôi khác. Và video là LƯU TRỮ PHỤ →
  `chunk_processor` nuốt lỗi save (video_path="") để KHÔNG chặn indexing (text đã ở FAISS).
  Regression: `test_video_codec.py`, `test_chunk_processor_index::test_video_failure_is_non_fatal`.

## UnboundLocalError 'get_embeddings' ở append_chunks_to_lc_index (LangChain FAISS path)

- **Triệu chứng:** Khi `USE_LC_VECTOR_STORE=1`, append chunk in `[vector_store] LangChain
  vector store failed, fallback legacy FAISS: cannot access local variable 'get_embeddings'`
  → âm thầm rơi về raw FAISS (vẫn chạy nhưng sai backend dự kiến). Phát hiện qua WORKFLOW
  SMOKE THẬT, không phải unit (unit raw-path không chạm nhánh LC).
- **Nguyên nhân:** Trong `append_chunks_to_lc_index` có `from app.clients.llm_factory import
  get_embeddings` Ở GIỮA hàm (khối __meta__) → Python coi `get_embeddings` là biến CỤC BỘ cho
  CẢ hàm → `emb = get_embeddings()` ở đầu hàm ném UnboundLocalError.
- **Cách xử lý (đã làm):** bỏ import lồng trong hàm; dùng lại `emb` (đã gán từ get_embeddings
  module-level ở đầu hàm) để lấy `emb_dim`. Regression: `test_store_precomputed.py::
  test_lc_path_precomputed_no_get_embeddings_shadow` (ép USE_LC_VECTOR_STORE=1 + embeddings).
- **Prevention:** KHÔNG `from x import y` giữa hàm nếu `y` đã dùng như tên module-level trong
  cùng hàm — sẽ shadow toàn hàm. Có test chạm nhánh LC FAISS (không chỉ raw).

## AutoModel.from_pretrained nạp .bin bị chặn với torch 2.5.x (late chunking) → dùng safetensors

- **Triệu chứng:** `LateChunkEncoder` nạp bge-m3 qua `AutoModel.from_pretrained` ném
  `ValueError: Due to a serious vulnerability issue in torch.load ... require torch >= v2.6`
  (CVE-2025-32434). Late chunking không tạo được vector → ingest rơi về fallback naive.
- **Nguyên nhân:** transformers chặn `torch.load` file `pytorch_model.bin` khi torch < 2.6.
  Repo PIN `torch==2.5.1+cpu` (xem lý do CUDA/Docker) → không nâng. Cache bge-m3 có CẢ
  `model.safetensors` lẫn `pytorch_model.bin`; mặc định transformers thử .bin → bị chặn.
- **Cách xử lý (đã làm):** `AutoModel.from_pretrained(name, use_safetensors=True)` trong
  `late_chunk.py::_ensure_backend` → buộc nạp .safetensors (không dính torch.load guard).
- **Verify:** smoke thật `scratchpad/smoke_late_chunk.py` (hoặc bất kỳ ingest có model) phải
  nạp bge-m3 OK, trả vector (n,1024). Model mới thêm vào hệ PHẢI có .safetensors trên HF.

## ormsgpack DLL bị Windows Application Control chặn (langgraph 1.x không import được)

- **Triệu chứng:** `import langgraph.graph` → `ImportError: DLL load failed while importing ormsgpack: An Application Control policy has blocked this file.` Toàn bộ tầng graph (query/ingest/mindmap) không import được → app không chạy.
- **Nguyên nhân:** langgraph 1.x phụ thuộc cứng `langgraph-checkpoint>=3` → `ormsgpack`. Binary `ormsgpack.cp311-win_amd64.pyd` bị Windows Application Control (Smart App Control/WDAC) chặn trên máy dev này. (pydantic-core Rust load OK → policy chỉ chặn riêng binary ormsgpack.)
- **Cách xử lý (đã chốt):** Pin về stack 0.3.x/0.2.x dùng `msgpack` thuần:
  - `langgraph>=0.2.57,<0.3` (0.2.57+ có `interrupt()` động cho HITL; dùng 0.2.76)
  - `langgraph-checkpoint==2.0.21` — **bản msgpack cuối cùng**. Lưu ý: checkpoint ≤2.0.21 dùng `msgpack`; **≥2.0.22 chuyển sang `ormsgpack`** (đã verify qua PyPI `requires_dist`).
  - `langgraph-checkpoint-sqlite==2.0.10` cần `checkpoint>=2.0.21` → giao điểm duy nhất msgpack-thuần là **đúng 2.0.21**.
  - *(Quan sát:* trên máy này ormsgpack 1.12.1 có lúc lại load được — policy có thể chuyển audit→allow. Nhưng vẫn pin msgpack-thuần để miễn nhiễm nếu bị tái chặn.)
  - `langchain*` về 0.3.x (core>=0.3.66 để thỏa community 0.3.27).
- **Verify sau mọi thay đổi dependency:** `python -c "import app.graphs.query_graph"` phải thành công. `import ormsgpack` vẫn fail là bình thường (msgpack không chạm tới nó).

## Rerank/NLI lazy-load NẰM TRONG timeout → query đầu âm thầm fallback (no-op)

- **Triệu chứng:** Bật `RERANK_ENABLED=1`/`NLI_ENABLED=1`, query ĐẦU TIÊN sau khi
  khởi động process: rerank không đổi thứ tự (như chưa bật), NLI trả
  `context_conflicts=[]` dù có cặp chunk mâu thuẫn rõ ràng. Query #2+ lại đúng.
  Test suite KHÔNG bắt được (graph-test monkeypatch `rerank_texts`/`detect_conflicts`
  → không có model load thật — đúng bài học "conftest mock che lỗi").
- **Nguyên nhân:** `RerankDocuments`/`VerifyContext` bọc lời gọi engine trong
  `ThreadPoolExecutor(...).result(timeout=RERANK_TIMEOUT/NLI_TIMEOUT)` (mặc định 10s).
  Engine load model **lazy** (`_ensure_model`) nên LẦN ĐẦU việc tải model chạy NGAY
  TRONG block timeout. Trên CPU/cache nguội, **chỉ riêng load weights mDeBERTa đã ~12.7s > 10s**
  → `TimeoutError` → nuốt im lặng thành identity/[] ở query đầu. Singleton cache model
  nên query sau (cùng process) mới đúng.
- **Cách xử lý (đã làm):** thêm `warmup()` ở `rerank.py`/`nli.py` — nạp weights **và**
  chạy 1 forward mồi (warm JIT/trace), gọi trong node **TRƯỚC** block timeout. Có timeout
  riêng rộng (120s) để model lỗi không treo vô hạn; `SKIP_MODEL_LOAD`/identity/null/lỗi → no-op.
  Timeout của node giờ chỉ bao inference thực. Regression: `test_*_warmup_loads_model_outside_timeout`
  (mô phỏng load chậm deterministic). `base_env` test set `SKIP_MODEL_LOAD=1` để warmup
  không kéo model thật trong unit test.
- **Verify:** smoke build graph THẬT với cờ bật + timeout MẶC ĐỊNH → rerank đảo thứ tự đúng
  ở query đầu (chunk vô quan bị loại).

## NLI (mDeBERTa) trên CPU ~7s/cặp → `NLI_TIMEOUT_SEC=10` mặc định KHÔNG đủ

- **Triệu chứng:** Sau khi đã fix warmup ở trên, rerank chạy tốt trong 10s nhưng NLI vẫn
  `context_conflicts=[]` ở timeout mặc định. Đo trực tiếp trên CPU máy dev (đã warm):
  `predict 6 cặp ≈ 42.8s` (~7s/cặp). `NLI_MAX_PAIRS=10` (mặc định) → tới 20 forward ≈ ~140s.
- **Nguyên nhân:** Đây là **giới hạn hiệu năng phần cứng**, không phải bug. mDeBERTa-v3-base
  inference rất chậm trên CPU; `detect_conflicts` chấm cả 2 chiều mỗi cặp nên số forward = 2×pairs.
- **Cách xử lý (đã chốt):** đổi default cho CPU chạy được: `NLI_MAX_PAIRS=3` + `NLI_TIMEOUT_SEC=90`.
  Đo THỰC trên CPU máy dev: 3 cặp chunk DÀI (6 forward) ≈ **66s** (câu ngắn ~42s nên ban đầu ước
  lượng thấp) → để 90s có đệm. Có GPU/model nhanh hơn thì hạ cả hai xuống qua env. Passthrough an
  toàn khi quá hạn vẫn giữ nguyên (không vỡ).
- **Lưu ý:** rerank (`bge-reranker-v2-m3`) trên cùng CPU lại kịp trong 10s với pool ~4–10 ứng viên
  → mặc định rerank giữ nguyên; chỉ NLI cần cân nhắc.

## Query-theo-file trả rỗng với tên file có space/dấu/ký tự đặc biệt (stem phân mảnh)

- **Triệu chứng:** chọn file để hỏi → "Không tìm thấy dữ liệu phù hợp", dù file đã index. Đặc biệt
  với tên có KHOẢNG TRẮNG (rất phổ biến), dấu tiếng Việt, hoặc ký tự đặc biệt.
- **Nguyên nhân:** định danh "stem" được suy ra ở ~6 nơi với quy tắc KHÁC NHAU. Mấu chốt: upload
  lưu `source_stem` GIỮ khoảng trắng (`Path(filename.replace('.','_')).stem.lower()` → "my report_pdf"),
  còn chunk `index.json["video"]` = video_path đã SANITIZE (space→'_' → "my_report_pdf") + timestamp.
  Retrieval `hybrid._filter_by_sources` so khớp 2 phía qua `_norm_stem` (NFKD, GIỮ space) → "my report_pdf"
  (selected) ≠ "my_report_pdf" (chunk) → `allowed_idx=[]` → retrieve [] . (NFKD KHÔNG bỏ dấu kết hợp.)
- **Cách xử lý (đã chốt):** MỘT canonicalizer dùng chung `shared/source_id.py::canonical_source_stem`,
  MIRROR đúng cách ingest đặt tên video_path (bỏ '.mp4' container có timestamp → fold '.'→'_' qua
  sanitize → bỏ timestamp → NFC + lower). Áp vào: `hybrid._norm_stem`, `memory/tree._normalize_video_stem`,
  `upload_file`/`ingest_graph` (source_stem), `/list-indexed` (trả `video_stem` canonical + `filename`).
  Ghi thêm `source_stem`/`source_id` canonical vào chunk metadata (ingest_graph) để retrieval khớp CHÍNH
  XÁC (ưu tiên field này, fallback suy từ `video` cho data cũ → không cần re-ingest).
- **Verify:** `python -m pytest tests/test_source_id.py tests/test_retrieval_filter.py tests/test_source_stem_sync.py
  tests/test_upload_query_e2e.py` — test space/dấu/ký-tự-đặc-biệt khớp đúng.
- **Hardening kèm theo:** lưu file vật lý an toàn (`_safe_save_path`: chặn ký tự cấm Windows + path
  traversal); chống trùng tên (`_unique_display_filename` gắn " (n)"); `/delete-source` khớp canonical +
  BỎ glob `{stem}*` nguy hiểm (xóa nhầm), dọn registry + file input; `/upload-multiple` đi cùng luồng
  async với `/upload-file` (source_id + registry + background ingest → FE poll được).

## pydantic 2.11+ làm vỡ StateGraph(QueryState) (langgraph 0.2.x)

- **Triệu chứng:** `build_query_graph` ném `pydantic.errors.PydanticForbiddenQualifier: ... 'NotRequired[Union[str, NoneType]]' contains the 'typing.NotRequired' type qualifier`. (Test cũ KHÔNG bắt được vì `conftest.py` mock `QUERY_GRAPH` → không bao giờ gọi `StateGraph(QueryState)` thật.)
- **Nguyên nhân:** pydantic ≥2.11 kéo `typing_inspection`, raise `ForbiddenQualifier('not_required')` khi `langchain_core.utils.pydantic.create_model_v2` build model từ `QueryState` TypedDict (có nhiều field `NotRequired[Optional[...]]`). langgraph 0.2.x truyền nguyên annotation kèm `NotRequired`.
- **Cách xử lý:** pin `pydantic>=2.7.4,<2.11` (dùng 2.10.6, không có typing_inspection).
- **Verify:** build graph thật (không mock) với cả 3 cờ CRAG/Supervisor/HITL bật phải compile được.

## chunks.sqlite bị mất hoặc hỏng dữ liệu

- **Triệu chứng:** Không thể thực hiện tìm kiếm lexical (BM25 trả kết quả kém) hoặc tìm kiếm/tóm tắt thất bại khi đọc text của chunk, mặc dù các vector search qua FAISS vẫn trả về các ID tương ứng.
- **Nguyên nhân:** File cơ sở dữ liệu runtime `chunks.sqlite` (lưu trữ text của các chunk) bị xóa nhầm, lỗi quyền ghi, hoặc bị hỏng. `index.json` nay chỉ chứa pointer `(video, frame_index)` và metadata, không còn lưu trữ text inline mặc định nữa.
- **Cách xử lý:** Chạy công cụ dòng lệnh khôi phục để tự động quét `index.json`, giải mã lại các frame video QR tương ứng để tái cấu trúc lại database SQLite:
  ```bash
  cd BE
  python -m app.scripts.rebuild_sqlite_from_videos
  ```

