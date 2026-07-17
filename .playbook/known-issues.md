# Known Issues

## (ĐÃ SỬA 2026-07-17) Huỷ tóm tắt kẹt "Đang huỷ… (36%)" mãi — cancel job không còn executor + FE poller không biết "interrupted"

- **Triệu chứng:** Đang tạo tóm tắt, bấm Huỷ → chip kẹt "Đang huỷ… (36%)" vĩnh viễn, %
  đứng yên, không bao giờ thoát trạng thái huỷ. (Huỷ khi executor còn sống hoạt động đúng —
  đã chứng minh bằng repro graph thật.)
- **Root cause (2 tầng, đo bằng repro trực tiếp):**
  1. BE: `jobs_store.request_cancel` CHỈ set cờ `cancel_requested=1` và trông chờ executor
     đang sống ack giữa các node. Job KHÔNG còn executor — `pending` trong queue, hoặc
     `interrupted` (BE restart → `mark_interrupted_jobs` đánh dấu job mồ côi, GIỮ progress 36)
     — thì không ai ack cờ → status không bao giờ terminal.
  2. FE: `jobPoller.js` chỉ coi done/error/timeout/cancelled là terminal — "interrupted"
     KHÔNG có trong tập (queryPolling.js CÓ, jobPoller quên) → poll vô hạn, label bị khoá
     "Đang huỷ…" (cancelRequestedRef) + progress đóng băng đúng như user thấy.
- **Fix:** (1) `request_cancel` chuyển THẲNG `pending`/`interrupted` → `cancelled` trong cùng
  UPDATE (running/processing giữ cooperative; terminal giữ nguyên → idempotent). Sửa MỘT chỗ
  ở store → summary LẪN mindmap cancel hưởng chung. (2) `/summary-cancel` 404 job lạ (cùng
  contract `/summary-status`), trả `status` sau cancel. (3) `summary_graph.assemble_node`
  thêm 2 cancel checkpoint: trước coverage judge (LLM dài) + trước persist — cancel đến sau
  entry-guard vẫn KHÔNG persist/done (done-with-result vẫn atomic). (4) FE `jobPoller` coi
  "interrupted" là terminal (onError, message riêng qua `messages.interrupted`).
- **Regression:** BE `test_jobs_cancel.py` (running cooperative / pending+interrupted →
  cancelled ngay / terminal idempotent), `test_summary_graph.py::test_cancel_mid_summarize_
  reaches_terminal_cancelled` + `test_cancel_during_coverage_judge_does_not_persist_or_done`,
  `test_summary_routes.py` (cancel 404 job lạ/khác type, interrupted → "cancelled", done →
  safe no-op). FE `summaryJob.test.js` (cancelled → onCancelled dừng hẳn; interrupted →
  onError, không poll vô hạn).
- **Prevention:** Cancel theo cờ cooperative PHẢI có đường terminal cho job không còn
  executor — endpoint cancel không được chỉ "ghi cờ rồi hy vọng". FE poller: tập status
  terminal phải khớp ĐỦ tập status BE có thể ghi (interrupted sinh ra ở startup-reconcile,
  không chỉ trong flow chạy bình thường); thêm status mới phía BE → rà mọi poller.
- **Tái điều tra 2026-07-17 (user báo "vẫn kẹt" SAU commit fix):** code fix ĐÚNG — nguyên nhân
  còn lại là DEPLOYMENT STALE: stack thật user mở (compose project `memvid_auth_smoke`,
  FE :3000 / BE :8080, `QUEUE_ENABLED=true` + rq-worker) build từ image 2026-07-14, TRƯỚC
  commit `0bd4624` 3 ngày. Verify trực tiếp: `docker exec <backend|rq-worker> grep
  "pending','interrupted'" jobs_store.py` → FIX_ABSENT; FE bundle không có message
  "gián đoạn trên server". Sau `docker compose -p memvid_auth_smoke --profile worker up -d
  --build backend rq-worker frontend`: smoke Playwright trên UI thật PASS — cancel lúc
  running (45%, mục 3/8) → `/summary-cancel` 200 `{ok,status:"running"}` → 8s sau
  `/summary-status` trả `cancelled`, chip thoát "Đang huỷ…", notice "Đã huỷ tạo tóm tắt.",
  `summary_active_job` localStorage cleared, không summary nào bị lưu, tạo lại ngay OK.
  RQ path đã rà: queued-rồi-worker-nhặt được entry-guard chặn (không persist), cancel_cb
  check sau MỖI section LLM call → trễ tối đa 1 call. Bẫy phụ khi rebuild: root `.env`
  KHÔNG có `COMPOSE_PROJECT_NAME` → `docker compose` trần build/chạy project `memvid_new`
  KHÁC stack user đang mở và đụng port 8080 — phải `-p memvid_auth_smoke` (hoặc set
  COMPOSE_PROJECT_NAME vào .env như .env.example).
- **Prevention (ops):** "fix rồi mà user vẫn thấy bug" → bước 1 LUÔN so runtime user mở
  với code: `docker ps` xem project/created-time, `docker exec grep <chuỗi đặc trưng fix>`
  trong container + FE bundle. Đừng đọc lại code trước khi chứng minh code đó ĐANG chạy.

## (ĐÃ SỬA 2026-07-06) Cache hit trả "Không có phản hồi." — race job done-trước-result + 4 lỗ contract

- **Triệu chứng:** Câu hỏi bị cache HIT (nhanh <1s) → FE hiện "Không có phản hồi." dù Redis
  có answer đầy đủ; câu MISS (chậm 30s+) trả lời bình thường. User thấy: "noi dung la gi"
  OK nhưng "nội dung là gì"/"nọi dung là gì" rỗng. Reproduce 3/3 bằng smoke script.
- **Root cause (đo trực tiếp, không đoán):** `finalize_node` set `status="done"` vào jobs_store
  NGAY TRONG graph; `result` được `_finalize_query_job` (main.py) gắn SAU khi `graph.invoke`
  trả về — giữa 2 bước còn `_detect_query_interrupt` đọc checkpoint sqlite (chậm, state to).
  Job nhanh → FE poll trúng cửa sổ `status=done, result=None` → answer rỗng. Job chậm không
  bao giờ trúng → asymmetry đánh lừa chẩn đoán về phía diacritics/cache-logic.
- **Fix:** finalize_node KHÔNG set status nữa (chỉ progress); "done" đi CÙNG result trong một
  update duy nhất ở `_finalize_query_job`. + Bịt 4 lỗ contract (codex audit xác nhận):
  1. cache_lookup_node: hit phải có answer non-empty mới `done=True`; lookup exception →
     đi tiếp pipeline (trước đây → ErrorHandler, chặn đường trả lời).
  2. Mọi hit path trong llm_cache (`_answer_ok`): entry answer rỗng = không tồn tại.
  3. Mọi write path (semantic_store, _set_cached_query L1, finalize): answer rỗng/whitespace
     không được ghi (`cache_write_skipped_empty_answer`).
  4. `gen_fallback` flag: message chẩn đoán "Không nhận được phản hồi từ model..." KHÔNG
     được cache (trước đây cache như answer thật → poisoning mọi câu tương đương).
- **Regression:** `test_llm_cache.py` — empty_cached_answer_treated_as_miss_all_paths,
  store_skips_empty_answer, vn_variant_flow_same_document, graph_cache_hit_empty_answer_falls_through,
  graph_cache_lookup_exception_falls_back_to_llm, graph_finalize_skips_store_when_answer_empty
  (31 test). Smoke: `python BE/scripts/smoke_semantic_cache.py` — 6/6 PASS.
- **Prevention:** (1) Trạng thái terminal của job PHẢI được ghi atomically cùng payload kết quả
  — không bao giờ set "done" ở một tầng rồi gắn result ở tầng khác. (2) Bug "lúc có lúc không"
  tương quan với TỐC ĐỘ response = nghĩ ngay đến race polling, đừng chỉ soi logic nghiệp vụ.
  (3) Cache lookup exception không bao giờ được route sang error-terminal — cache là tối ưu.

## (2026-07-06) bge-m3: câu Việt CÓ dấu vs KHÔNG dấu embed rất khác nhau (cosine 0.558) — đừng gác diacritics bằng cosine

- **Triệu chứng:** Nâng cấp semantic cache, thiết kế đầu: alias không-dấu hit phải verify
  cosine ≥ threshold (chống đồng tự "bán"/"bàn"). Smoke Docker thật: "noi dung chinh cua
  tai lieu la gi" KHÔNG hit entry "Nội dung chính của tài liệu là gì?" — đo trực tiếp
  trong container: cosine 2 form = **0.558** (threshold 0.85).
- **Nguyên nhân:** bge-m3 mean-pool tokenize 2 form khác hẳn nhau → cặp CÙNG nghĩa
  có/không dấu sim thấp; ngược lại cặp homograph KHÁC nghĩa (lệch 1 ký tự) sim rất cao
  → cosine verify gác NGƯỢC chiều đe doạ: chặn true-positive, cho qua false-positive.
- **Cách xử lý (đã làm):** bỏ cosine verify ở alias path; gác bằng **LLM judge** (so intent
  2 câu dạng chữ — judge thấy dấu, phân biệt được nghĩa). Judge tắt → alias hit thẳng
  (toàn câu normalized trùng modulo dấu = tín hiệu mạnh, đánh đổi ghi rõ trong DR-2).
  Regression: `test_nodia_variant_hits_via_alias`, `test_nodia_reverse_direction_hits`,
  `test_nodia_alias_homograph_judge_denies`. Smoke live: hit `kind=exact_nodia` 5.1s vs cold 39.7s.
- **Prevention:** guard dựa trên embedding phải CALIBRATE bằng số đo thật trên đúng encoder
  + đúng loại text trước khi tin — trực giác "cùng nghĩa thì sim cao" sai với cross-form
  (có dấu/không dấu, viết tắt, ngôn ngữ trộn). Unit test vector giả không thay được số đo thật.
- **Quan sát phụ (chưa sửa, ghi nhận):** finalize re-store answer vào cache MỖI lần hit
  (3 dòng cache_write cho 3 hit trong smoke) — idempotent, chỉ tốn 1 SETEX + refresh TTL,
  hành vi có từ v1. Muốn tối ưu: skip set_cached khi payload lấy từ cache.

## (ĐÃ SỬA 2026-07-06) 'LateChunkEmbeddings' object is not callable — LC FAISS path chết mỗi query

- **Triệu chứng:** Mỗi query log 2 dòng: langchain warning "`embedding_function` is expected
  to be an Embeddings object, support for passing in a function will soon be removed" +
  `HybridRetriever.retrieve_faiss_only: LC path failed: 'LateChunkEmbeddings' object is not
  callable`. Retrieval VẪN ra kết quả (rơi về legacy FAISS im lặng) nên dễ bỏ qua.
- **Nguyên nhân:** `llm_factory.py::LateChunkEmbeddings` là plain class, KHÔNG kế thừa
  `langchain_core.embeddings.Embeddings`. LangChain FAISS check
  `isinstance(embedding_function, Embeddings)` — fail → coi nó là callable (đường deprecated),
  gọi `obj(text)` → TypeError not callable → LC path fail mọi `similarity_search_with_score`.
  Cả 2 dòng log cùng MỘT gốc. Duck-typing (có đủ embed_query/embed_documents) KHÔNG đủ —
  langchain phân nhánh bằng isinstance.
- **Cách xử lý (đã làm):** 1 dòng — `class LateChunkEmbeddings(_LCEmbeddings)` (import
  `Embeddings` module-level). 2 method abstract đã có sẵn.
- **Regression:** `test_embedding_late_chunk.py::test_late_chunk_embeddings_is_langchain_embeddings`
  (assert isinstance). Đã chạy kèm `test_store_precomputed.py` + `test_llm_cache.py` — xanh.
- **Prevention:** Viết adapter cho interface langchain → PHẢI subclass base class thật
  (`Embeddings`, `BaseRetriever`…), đừng duck-type; langchain rẽ nhánh isinstance ở nhiều chỗ.
  Test wiring assert `isinstance(..., Embeddings)` chứ không chỉ `hasattr`.
- **Lưu ý liên quan (ĐÃ XỬ LÝ cùng ngày):** hiện tượng "hỏi lại y hệt vẫn soạn mới" trong CÙNG
  phiên chat không phải bug này — trước đây `cache_lookup_node` bypass MỌI câu khi có
  `conversation_history`. Đã đổi: chỉ bypass câu FOLLOW-UP; câu STANDALONE
  (`llm_cache.is_standalone_question` — heuristic conservative: câu <4 từ, anaphora
  nó/này/đó/that/it..., mở đầu còn/thế/vậy/what about... → follow-up) vẫn cache.
  Điều kiện an toàn: `generate_answer_node` BỎ history khỏi prompt khi `cache_key` được set
  → answer context-free → store không poisoning (lookup/store nhất quán). Metric mới
  `standalone_with_history`. Regression: `test_llm_cache.py::test_is_standalone_question_heuristic`
  + `test_standalone_question_with_history_uses_cache`. Heuristic nghiêng về bypass —
  sai hướng đó chỉ mất cache, sai hướng ngược lại mới sinh answer thiếu ngữ cảnh.

## (ĐÃ SỬA 2026-07-05) Mindmap viewer + PNG export vỡ hoàn toàn — thiếu import MindElixir.css

- **Triệu chứng:** Mở sơ đồ tư duy: toàn bộ text node dồn thành MỘT dòng góc trên-trái
  ("Tổng quan tài liệuPhát hiện xâm phạm…"), root lơ lửng, 2 đường bezier bay lạc, canvas
  trống khổng lồ. PNG export y hệt (snapdom chụp trung thực DOM đang vỡ).
- **Nguyên nhân (3 lớp):**
  1. `mind-elixir/style` (dist/MindElixir.css) KHÔNG được import ở đâu cả — mind-elixir v5
     layout HOÀN TOÀN bằng CSS (`me-nodes` flex, `me-tpc` block...). Thiếu nó, custom elements
     rơi về `display:inline` → sụp toàn bộ. Bundle build cũng không có (verified grep dist).
  2. THEME custom chỉ set 4/22 cssVar; MindElixir.css dùng `var(--map-padding)`,
     `--main-gap-x/y`, `--node-gap-x/y`, `--root-radius`… KHÔNG có fallback → declaration
     invalid, spacing sụp dù đã import CSS.
  3. Export chụp `mind.nodes` (element `me-nodes`) TÁCH khỏi `.map-canvas` — rule then chốt
     là descendant selector `.map-canvas me-nodes{display:flex}` không match trong clone
     snapdom → PNG vỡ kể cả khi viewer đúng. Không có `scale` → ảnh mờ.
- **Cách xử lý (đã làm):** import `"mind-elixir/style"` trong `MindElixirView.jsx`; THEME
  PhongDoc set đủ 22 var (guard bằng `theme.test.js` — thiếu var nào test đỏ); export chụp
  `mind.map` (`.map-canvas`) + `scale: 2`.
- **Prevention:** dùng thư viện render bằng CSS-file riêng → kiểm tra CSS có vào bundle
  (`grep <rule đặc trưng> dist/assets/*.css`). Chụp DOM bằng snapdom/html2canvas → target
  phải CHỨA đủ tổ tiên mà CSS selector cần. Theme override một thư viện → set đủ TOÀN BỘ
  bộ var nó tiêu thụ, đừng set một phần.

## (ĐÃ SỬA 2026-07-05) Mindmap docx nông: heading_path rỗng → skeleton filler "Tổng quan tài liệu"

- **Triệu chứng:** Tạo sơ đồ cho docx → cây chỉ có root → 1 section "Tổng quan tài liệu"
  → vài idea; không sâu hơn, relations luôn rỗng (skip khi <2 section).
- **Nguyên nhân (chuỗi 4 khâu):**
  1. mammoth chỉ sinh `#`/`##`/`###` cho Word Heading styles thật — docx sinh viên dùng
     bold/đánh số tay → markdown 0 heading → mọi chunk `heading_path=""`.
  2. Chỉ `_from_headings` tạo được chiều sâu; tree_sections/clusters đều FLAT. Fallback
     tree_sections với ≤18 chunk trả đúng 1 section size-based tên "Tổng quan tài liệu".
  3. Kể cả khi có heading: `embed_index_node` cũ yêu cầu `len(headings)==len(entries)` —
     QR sub-split 1 chunk là lệch → rớt TOÀN BỘ heading_path của doc.
  4. `content_hash` không hash heading metadata → re-ingest phục hồi heading (text không đổi)
     vẫn trúng cache cũ, trả mãi map nông.
- **Cách xử lý (đã làm, PIPELINE_VERSION → skeleton_v2):**
  - `clean.py::promote_headings`: promote heuristic (dòng bold đứng một mình ≤90 ký tự không
    kết thúc ".", `Chương/Phần/Bài/Mục`, `1.`→##, `1.1`→###, La Mã→#) — CHỈ khi doc chưa có
    heading nào; item list sát nhau không bị promote (yêu cầu blank 2 phía).
  - `ingest_graph.py`: map heading qua `entry["chunk_index"]` (đã có sẵn cho late chunking)
    thay vì alignment 1:1 — sub-split không rớt heading nữa.
  - `skeleton.py::_from_tree_sections` yêu cầu ≥2 section (1 section = filler, bỏ).
  - MỚI `outline.py::build_outline`: skeleton "single" → 1 LLM call sinh mục lục 2 tầng
    (chunk_keys validate theo id thật); thành công → method "llm_outline", lỗi → root-only
    + degraded_missing "skeleton".
  - `content_hash(..., chunk_headings)` hash cả heading (prefix `\x02`); `generator.skeleton_method`
    được persist để chẩn đoán record đã lưu.
  - SKIP_MODEL_LOAD giờ khai `degraded=True` ở enrich/relations (trước im lặng trả skeleton
    như bản hoàn chỉnh).
- **Regression:** `test_promote_headings.py`, `test_mindmap_outline.py`, `test_mindmap_skeleton.py::
  test_single_tree_section_is_rejected_as_filler`, `test_late_chunk_ingest.py::test_heading_path_
  survives_subsplit`, `test_mindmap_schema_v2.py` (hash headings + skeleton_method).
- **Lưu ý:** dữ liệu đã index TRƯỚC fix vẫn heading_path rỗng — muốn map sâu phải re-upload
  (re-ingest) tài liệu; hash mới sẽ tự bypass cache cũ.
- **Regression cùng ngày (đã vá, skeleton_v3):** bản đầu của `promote_headings` chỉ match
  `**bold**` — mammoth THẬT sinh `__bold__` VÀ escape punctuation (`1\.` chứ không phải `1.`)
  → doc Q&A re-upload vẫn trượt promote. Vá: `_BOLD_LINE_RE` nhận cả `__`/`**` (backreference
  `(\*\*|__)...\1`), thêm `unescape_mammoth` (bỏ `\` trước bộ punctuation AN TOÀN `. ( ) ! ? , : ; … " '`
  — KHÔNG đụng `# * - [ ]` tránh tạo markdown giả) chạy TRƯỚC promote trong `clean_markdown`.
  Bài học: viết heuristic parse markdown phải kiểm bằng OUTPUT THẬT của converter (đọc chunk
  từ sqlite), đừng viết theo markdown "chuẩn" trong đầu. Test: `test_promote_headings.py`
  (case mammoth dialect), FE mirror `evidence.js::unescapeMd` cho data cũ.
- **Regression vòng 2 cùng ngày (đo qua smoke Docker thật, đã vá):**
  1. Cap heading 90 ký tự chặn câu hỏi Q&A tiếng Việt bold (đo thật: 203 ký tự) → tách cap:
     bold đứng một mình (tín hiệu mạnh) = 250, dòng đánh số trần = 90.
  2. `MINDMAP_LLM_TIMEOUT_SEC` mặc định 120s không đủ cho enrich prompt nested-detail trên
     qwen3.5:9b CPU (3/4 nhánh degraded) → compose set 240s (cả backend + mindmap-service;
     lưu ý pipeline chạy trong mindmap-service khi `MINDMAP_SERVICE_ADDR` bật — set env đúng container).
  3. qwen thi thoảng trả JSON hỏng delimiter (~1/4 nhánh) → `enrich._ask_json` retry đúng 1 lần
     trước khi degraded. Regression: `test_enrich_retries_once_on_malformed_json`.

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

## Global python site-packages trôi khỏi requirements.txt pin (langchain/langgraph/pydantic)

- **Triệu chứng:** `pytest tests/` báo lỗi collection ở các file dùng `ensemble_retriever`
  (`test_crag_graph.py`, `test_hitl_graph.py`, `test_nli_graph.py`, `test_rerank_graph.py`,
  `test_supervisor_graph.py`, và trực tiếp `python -c "import app.graphs.query_graph"`):
  `ModuleNotFoundError: No module named 'langchain_core.pydantic_v1'`.
- **Nguyên nhân:** Global Python (dùng chung cho nhiều project trên máy dev — thấy cả
  `day08-langgraph-agent-lab` trong `pip list`) đã bị một lần `pip install` KHÔNG pin cài đè
  lên site-packages: `langchain==0.2.17` + `langchain-core==1.4.8` (lệch pha nặng — 0.2.x code
  gọi API chỉ có ở core cũ `pydantic_v1` shim, core 1.4.8 đã bỏ) + `langgraph==1.0.1` +
  `pydantic==2.13.4`, đều NGOÀI pin của `requirements.txt`
  (`langchain>=0.3.27,<0.4`, `langgraph>=0.2.57,<0.3`, ngụ ý pydantic<2.11 qua known-issue khác).
  Đã verify bằng `git stash` — lỗi tồn tại TRƯỚC bất kỳ thay đổi nào trong task hiện tại → môi
  trường trôi độc lập với code.
- **Quan sát phụ:** dù `langgraph` đã lên 1.0.1 (khác pin 0.2.x) và `pydantic` lên 2.13.4 (khác
  pin <2.11), `StateGraph(MindmapState)` với nhiều field `NotRequired[...]` VẪN build và chạy
  được (xem `tests/test_mindmap_graph.py`) — có thể lỗi `PydanticForbiddenQualifier` cũ (xem
  known-issue "pydantic 2.11+ làm vỡ StateGraph") đã được vá ở nhánh mới hơn của
  langchain_core/langgraph. KHÔNG coi đây là "đã an toàn để nâng pin" — chỉ là quan sát, chưa
  test đủ rộng (rerank/NLI/ensemble vẫn vỡ vì lý do khác — thiếu `pydantic_v1` shim ở core mới).
- **Cách xử lý:** CHƯA sửa (ngoài phạm vi task mindmap) — sửa bằng cách nào cũng đụng vào global
  site-packages dùng chung, rủi ro phá project khác trên máy. Test suite chạy OK khi loại 5 file
  trên: `pytest tests/ --ignore=tests/test_crag_graph.py --ignore=tests/test_hitl_graph.py
  --ignore=tests/test_nli_graph.py --ignore=tests/test_rerank_graph.py
  --ignore=tests/test_supervisor_graph.py`.
- **Prevention:** Trước khi bắt đầu 1 session dài, `pip show langchain langchain-core langgraph
  pydantic` đối chiếu `requirements.txt`; nếu lệch, cân nhắc venv riêng cho repo này thay vì
  global python (đánh đổi với lesson "dùng global python" cũ — lesson đó giả định global site-
  packages KHỚP pin; giờ không còn đúng). Nếu phải sửa global site-packages: `pip install -r
  BE/requirements.txt` rồi chạy lại toàn bộ suite của MỌI project dùng chung global python đó,
  không chỉ repo này.

## chunks.sqlite bị mất hoặc hỏng dữ liệu

- **Triệu chứng:** Không thể thực hiện tìm kiếm lexical (BM25 trả kết quả kém) hoặc tìm kiếm/tóm tắt thất bại khi đọc text của chunk, mặc dù các vector search qua FAISS vẫn trả về các ID tương ứng.
- **Nguyên nhân:** File cơ sở dữ liệu runtime `chunks.sqlite` (lưu trữ text của các chunk) bị xóa nhầm, lỗi quyền ghi, hoặc bị hỏng. `index.json` nay chỉ chứa pointer `(video, frame_index)` và metadata, không còn lưu trữ text inline mặc định nữa.
- **Cách xử lý:** Chạy công cụ dòng lệnh khôi phục để tự động quét `index.json`, giải mã lại các frame video QR tương ứng để tái cấu trúc lại database SQLite:
  ```bash
  cd BE
  python -m app.scripts.rebuild_sqlite_from_videos
  ```

## (ĐÃ SỬA 2026-07-04) Xoá nguồn khi index lớn → re-embed toàn bộ bằng bge-m3, block toàn bộ API vài phút

> **Resolved 2026-07-04:** Delete flow giờ ưu tiên remove-by-id trên index hiện có:
> `remove_chunks_from_lc_index` map `chunk_id -> docstore_id` rồi gọi `FAISS.delete(ids=...)`,
> `remove_chunks_from_raw_index` gọi `IndexIDMap.remove_ids(...)`. `rebuild_chunk_index(...)`
> chỉ còn là fallback khi delete-by-id lỗi, để ưu tiên toàn vẹn index/meta hơn hiệu năng.
> Giữ mục này làm lịch sử; phần dưới mô tả trạng thái TRƯỚC khi sửa.

- **Triệu chứng:** Bấm xoá nguồn khi index còn nhiều chunk → mọi endpoint (kể cả `/health`, `/list-indexed`) timeout vài phút; log in `[vector_store] rebuilt LC FAISS vectors=N (model=BAAI/bge-m3)` sau mỗi lần xoá. Quan sát thật ngày 2026-07-04 trên Docker: xoá lần lượt các nguồn khi index còn `245 → 240 → 237` vectors, mỗi lần đều block; xoá khi chỉ còn `2` chunks thì mất `0.35s`.
- **Nguyên nhân:** Flow xoá (`BE/app/domains/vectorstore/store.py::delete_chunks_by_source` / `delete_source_from_index`) gọi `rebuild_chunk_index(meta)`; nhánh LangChain gọi tiếp `rebuild_lc_index_from_meta` (`store.py:385`) = `FAISS.from_documents` trên TOÀN BỘ docs còn lại → re-embed tất cả bằng `BAAI/bge-m3` trên CPU. Cộng thêm gunicorn mặc định chỉ có `1` sync worker (`BE/Dockerfile:65`, `WEB_CONCURRENCY` mặc định `1`) nên 1 request nặng chặn cả app. Đây là nợ thiết kế cũ: trước còn rẻ với MiniLM 384, nay đắt vì late-chunking `bge-m3`.
- **Cách xử lý tạm:** Đặt `WEB_CONCURRENCY=2+` trong compose để app còn thở khi rebuild; xoá nguồn lúc rảnh.
- **Prevention:** Fix thật là bỏ re-embed khi xoá, chuyển sang delete-by-id trên index hiện có và chỉ rebuild ở nhánh fallback an toàn; xem plan `docs/superpowers/plans/2026-07-04-delete-source-no-reembed.md`.

## (ĐÃ SỬA 2026-07-04) `/generate-mindmap` cache-hit không có `job_id` → FE ném lỗi "Server không trả job_id"

> **Resolved 2026-07-04 (Task 16, commit aec6017):** FE `SidebarRight.jsx::runMindmapGeneration`
> giờ nhánh theo `startData.status === "done" && startData.result` TRƯỚC khi kiểm `job_id`
> (SidebarRight.jsx ~dòng 222-226) — cache-hit dùng thẳng `result`, bỏ polling. Phía BE trả
> `{"status":"done","result",...}` không có job_id là THIẾT KẾ của cache thật, không phải bug.
> Giữ mục này làm lịch sử; phần dưới mô tả trạng thái TRƯỚC khi sửa.

- **Triệu chứng:** Bấm "Tạo sơ đồ" (KHÔNG force) cho nguồn đã có mindmap cache theo `content_hash`
  → thay vì hiện lại map cũ ngay, FE alert lỗi "Không tạo được sơ đồ: Server không trả job_id."
- **Nguyên nhân:** `POST /generate-mindmap` khi cache hit (`force=False` + `mindmap_store.get_by_hash`
  trúng) trả THẲNG `{"status":"done","result":cached,"cached":true}` (200, KHÔNG có `job_id`) —
  xem `BE/app/main.py` quanh dòng 1742-1745. FE (`SidebarRight.jsx::runMindmapGeneration`, trước đây
  `handleGenerateMindMap`) luôn giả định response có `job_id` rồi mới poll: `if (!startData.job_id)
  throw new Error("Server không trả job_id.")` — không có nhánh xử lý response cache-hit.
- **Phát hiện:** đọc code khi làm Task 14 (tách MindMapModal.jsx), KHÔNG phải qua test/smoke thật —
  chưa xác nhận tần suất trúng cache trên dữ liệu thật (phụ thuộc `content_hash` có trùng không).
- **Cách xử lý:** CHƯA sửa — ngoài phạm vi Task 14 (tách file + render v2 relations). Hướng sửa gợi ý:
  FE nhánh theo `startData.status === "done"` (dùng `startData.result` thẳng, bỏ qua polling) TRƯỚC khi
  kiểm `job_id`, y hệt cách `onDone` xử lý kết quả job thường.

## (ĐÃ SỬA 2026-07-04) FE mindmap poll có hard-timeout 180s+10s → job thật chạy vài phút bị FE bỏ cuộc giữa chừng

- **Triệu chứng:** Tạo sơ đồ cho tài liệu lớn/nhiều nhánh (enrich+relations thật ~100s–vài phút, xem
  lessons-learned "skeleton-first") → FE tự báo lỗi "Quá thời gian chờ tạo Sơ đồ (frontend timeout)."
  dù job BE vẫn đang chạy và sẽ xong bình thường. User phải F5 rồi mở lại từ danh sách mới thấy map.
- **Nguyên nhân:** `SidebarRight.jsx::startPolling` (cũ) tự đặt `maxElapsedMs = jobTimeoutMs (180s) +
  maxExtraMs (10s)` và chủ động bắn `onError` khi vượt — một giá trị đoán, không theo thời gian chạy
  thật của pipeline (đo thật: enrich 3 nhánh ≈86s, nhưng tài liệu lớn/nhiều nhánh hơn dễ vượt 190s).
  Ngoài ra khi đang chờ, FE mở overlay fullscreen sớm với skeleton `partial` preview — trải nghiệm rối
  (overlay bật tắt nhiều lần) và không có cách nào phục hồi theo dõi job nếu user lỡ F5 (không có gì
  lưu `job_id` để resume).
- **Cách xử lý (đã làm, Task 1-4 nhánh mindmap-ux-v3):** thay `startPolling`/`stopPolling` (poller cũ,
  hard-timeout) bằng `utils/mindmapJob.js::createMindmapPoller` — KHÔNG hard-timeout (chỉ có stall-flag
  hiển thị UI sau `STALL_MS=5 phút` không đổi tiến độ, không tự huỷ). Bỏ overlay fullscreen sớm với
  skeleton preview; thay bằng progress chip nhỏ trong sidebar (`mindmapJobUi` state: running/label/
  progress/stalled). Thêm resume-after-reload: `utils/activeMindmapJob.js` lưu `{jobId, sources,
  startedAt}` vào localStorage khi job bắt đầu, `SidebarRight` mount-effect đọc lại và tự start poller
  mới (cờ `resumed=true` → done chỉ toast, không tự mở overlay, tránh giật user vào fullscreen cho job
  họ có thể không nhớ đã bấm). `clearActiveMindmapJob()` gọi ở mọi nhánh terminal (done/error/cancelled).
- **Prevention:** KHÔNG đặt hard-timeout FE cho job chạy nền dựa trên số đo TRUNG BÌNH — nếu cần phát
  hiện "kẹt", dùng stall-detection (không đổi tiến độ trong N phút, chỉ cảnh báo UI, không tự huỷ) thay
  vì tự ý coi là lỗi. Mọi job chạy nền dài (mindmap và tương lai các job tương tự) nên lưu định danh job
  vào localStorage ngay khi có `job_id` để F5 giữa chừng vẫn resume được, không bắt user "tưởng lỗi".
  `createMindmapPoller` là instance-per-run KHÔNG tự guard double-start — caller (`SidebarRight`) phải
  `pollerRef.current?.stop()` trước khi gán poller mới vào ref, nếu không sẽ rò rỉ vòng lặp polling cũ
  khi user bấm tạo/tạo lại liên tiếp.
- **Verify:** `cd FE && npm run build && npx vitest run` xanh (23 test, unit `mindmapJob.test.js`/
  `activeMindmapJob.test.js` cover poller + localStorage helper thuần, không cần BE thật). Manual smoke
  cần BE chạy thật (F5 giữa chừng lúc đang sinh → chip tự hiện lại) — dời qua đợt smoke thủ công riêng,
  chưa chạy trong phiên sửa này.

## Tạo lại xong ghi đè chỉnh sửa chưa lưu trong viewer

- **Triệu chứng:** Đang mở sơ đồ, sửa tay (đổi tên node, kéo, vẽ arrow — chưa bấm Lưu) rồi bấm "Tạo
  lại" (force=true, banner degraded) cho CÙNG map đang mở → khi job nền xong, bản chỉnh sửa tay biến
  mất, viewer hiện bản mới do LLM sinh lại thay vì hỏi trước.
- **Nguyên nhân:** `SidebarRight.jsx::handleMindmapDone` (được gọi khi poller báo `done`, kể cả với
  `isRegenerate: true`) build lại `record` từ kết quả job rồi gọi `setShowModalMap(record)` — thay
  thẳng object `data` mà `MindElixirView.jsx` đang render. `MindElixirView` re-init mind-elixir mỗi
  khi `data.id` đổi (`useEffect(..., [data?.id])`) — vì record mới có `id` mới (mindmap record UUID
  khác, xem mục 5 pipeline: force luôn tạo bản ghi mới) nên effect này chạy lại, gọi
  `recordToMindElixir(data)` mới và ghi đè toàn bộ instance, kể cả state `dirty`/nội dung chưa lưu
  của phiên sửa trước đó. `dirty` chỉ sống trong state của `MindElixirView`, không được đẩy lên
  `SidebarRight` nên `handleMindmapDone` không có cách nào biết viewer đang có thay đổi chưa lưu để
  chặn lại.
- **Cách xử lý (đã làm, mitigation không phải fix thật):** `MindElixirView.jsx` banner "Đang tạo lại
  sơ đồ…" hiện thêm dòng cảnh báo khi `dirty === true`: "— thay đổi chưa lưu sẽ bị thay thế khi bản
  mới sẵn sàng." (xem comment tại banner generating, ngay trước JSX `{dirty ? "..." : ""}`). Không
  chặn hành vi, chỉ báo trước để user tự bấm Lưu trước khi tạo lại nếu muốn giữ bản sửa.
- **Fix thật (chưa làm):** thread trạng thái `dirty` từ `MindElixirView` lên `SidebarRight` (ví dụ
  qua callback `onDirtyChange` giống `onSaved`/`onCancel` hiện có), rồi trong
  `SidebarRight.jsx::handleMindmapDone` (nhánh `isRegenerate`) kiểm cờ đó TRƯỚC khi
  `setShowModalMap(record)` — nếu đang dirty, hỏi xác nhận (hoặc giữ nguyên bản đang mở + chỉ toast
  "Có bản mới, xem?") thay vì tự động swap.
- **Tham chiếu code:** `FE/src/components/mindmap/MindElixirView.jsx` (banner generating, dòng có
  comment "Honest mitigation"), `FE/src/components/Layout/SidebarRight.jsx::handleMindmapDone`.

