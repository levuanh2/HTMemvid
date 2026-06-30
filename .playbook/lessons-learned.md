# Lessons Learned

## Late Chunking (bge-m3): embed toàn văn → mean-pool theo span (mặc định ON)

- **Bối cảnh / root cause:** naive chunking (cắt chunk rồi embed từng chunk độc lập, pooling
  CLS của bge-m3) khiến mỗi vector "mù" ngữ cảnh xung quanh → mất thông tin ở tài liệu dài,
  nhiều tham chiếu ngược ("như đã trình bày ở trên", đại từ "thành phố này"). Late chunking
  embed token TOÀN VĂN trước (`AutoModel.last_hidden_state`) rồi mean-pool theo ranh giới chunk
  → mỗi vector "thấm" ngữ cảnh toàn cục.
- **Thiết kế (đã làm):** module `app/domains/ingest/late_chunk.py::LateChunkEncoder` (lazy singleton,
  cache theo model-name, `warmup()`, sliding-window token cho doc > context, mean-pool + L2-norm).
  `chunk_markdown_spans()` trả `(doc_text, pieces)` với `doc_text[start:end]==text` (hệ toạ độ char).
  Vector tính ở **chunk_node** rồi carry `late_embeddings` xuống `embed_index_node` →
  `append_to_index(embeddings=...)` (store ghi precomputed, `__meta__.pooling="mean_late"`).
- **2 bẫy then chốt (đã xử lý):**
  1. **Pooling phải NHẤT QUÁN query↔chunk.** bge-m3 mặc định CLS, late chunking mean → cosine chỉ
     có nghĩa khi cả hai cùng mean. ⇒ `llm_factory.get_embeddings/get_embedding_model` đổi sang
     `LateChunkEncoder`/`LateChunkEmbeddings` (mean-pool) cho MỌI single-text embedding (query,
     memory-tree, mindmap). Late chunking **bypass gateway Embed** (model/pooling gateway khác → sai).
  2. **Span vỡ ở embed_index_node.** Tại đó text đã enrich + sub-split (QR) → KHÔNG khớp char-span.
     Phải tính vector ở chunk_node (còn `doc_text`+spans); `chunk_processor` gắn `chunk_index` lên mọi
     entry để sub-chunk dùng chung vector chunk cha. (Smoke thật: best-match đúng đoạn dùng đại từ;
     cosine(in-context, standalone)≈0.80<1 ⇒ đã thấm ngữ cảnh.)
- **Prevention / regression:**
  1. Đổi scheme (LATE_CHUNKING bật/tắt) hay đổi model → **PHẢI rebuild FAISS index** (pooling/dim
     đổi). Giữ cờ `LATE_CHUNKING=0` (đường CLS cũ) cho tình huống khẩn — mặc định ON.
  2. EMBEDDING_MODEL_NAME PHẢI là long-context encoder (bge-m3/jina-v3/nomic). KHÔNG all-MiniLM
     (max 512 → vỡ window 8192). Đã bỏ fallback all-MiniLM ở đường late; default encoder = bge-m3.
  3. Tests: `test_late_chunk*` (pure fns + fake model, KHÔNG tải bge-m3), `test_chunking` (span),
     `test_chunk_processor_index` (chunk_index), `test_store_precomputed`, `test_late_chunk_ingest`
     (e2e graph với fake encoder), `test_embedding_late_chunk` (wiring). Smoke THẬT (bge-m3) chỉ chạy
     thủ công (cần model) — đúng bài học "real-engine smoke test bắt lỗi mà unit-với-fake bỏ sót".

## Per-feature temperature: factual→0, chat→0.3 (chống ảo giác đúng tầng)

- **Bối cảnh:** temperature thấp giảm bịa đặt ở tác vụ factual, NHƯNG không phải "viên đạn bạc"
  (vẫn cần prompt ràng buộc "thiếu thì nói thiếu" + RAG/CRAG/NLI — đã có sẵn). Trước đây cả
  chat/summary/sinh-đáp-án dùng CHUNG một `LLM_TEMPERATURE=0.3`.
- **Thiết kế (đã làm):** `_resolve_temperature(feature, options)` trong `llm_factory.py`, ưu tiên
  `options['temperature']` (override per-call, vd mindmap 0.15) > factual (`_FACTUAL_FEATURES`:
  answer/summary/mindmap/grade/classify/extract → `LLM_TEMPERATURE_FACTUAL=0`) > chat (`LLM_TEMPERATURE=0.3`).
  Sinh đáp án RAG dùng `feature="answer"` (cùng model chat, temp 0): `summarize_results`,
  LC QA chain (`answer_with_document_context*` qua query_graph), VÀ memory-tree answer (`tree.py`).
  LLMReranker scoring → `feature="grade"`. (Quét đủ path nhờ codex: GenerateAnswer + memory-tree.)
- **Bẫy gateway (proto3) — codex review bắt được:** `common_pb2.LlmOptions.temperature` là `double`
  proto3 → KHÔNG phân biệt "0.0 đặt rõ" vs "mặc định"; `server._options_to_dict` lại dùng truthy
  `if options.temperature:` → rớt 0.0. ⇒ KHÔNG inject temp client-side. Để **server tự resolve theo
  `feature`** (đồng nhất cả ollama/gemini/groq). Phải truyền `feature, options` vào CẢ
  `_gemini_chat_llm`/`_groq_chat_llm` (trước chỉ ollama nhận) — `local_providers.ProviderPool.ask`.
- **Prevention:** thêm feature factual mới → bỏ vào `_FACTUAL_FEATURES`. Tác vụ scoring/classify/extract
  PHẢI factual (tất định). Còn chừa: query_rewrite + structured_extraction/fact_check (domain summary)
  vẫn `feature="chat"` — chấp nhận (sinh ngôn ngữ), siết sau nếu cần policy khép kín.

## Định danh file phải có MỘT nguồn sự thật (đừng suy stem rải rác mỗi nơi mỗi kiểu)

- **Root cause:** stem của source được tính lại ở nhiều tầng (upload/ingest/retrieval/memory-tree/
  list-indexed) với quy tắc lệch nhau (giữ vs sanitize khoảng trắng, NFC vs NFKD, có/không bỏ timestamp).
  Tên file có space → query-theo-file trượt khớp, trả rỗng.
- **Prevention:**
  1. MỘT hàm canonical duy nhất (`shared/source_id.py`), MIRROR đúng cách tạo artifact đích (ở đây là
     video_path); mọi nơi so khớp PHẢI gọi nó. Khi thêm tầng mới đụng tên file → dùng lại, đừng tự chế.
  2. Ghi định danh canonical (`source_stem`/`source_id`) THẲNG vào metadata để hạ nguồn khớp chính xác,
     thay vì tái dựng từ tên đã biến đổi (sanitize/timestamp).
  3. NFKD KHÔNG bỏ dấu kết hợp — đừng tưởng `normalize("NFKD")` biến "Hướng" → "Huong". Muốn ổn định
     thì ép NFC + sanitize, đừng so khớp chuỗi thô.
- **Bài học test:** bug này KHÔNG bị bắt vì `conftest` mock `_trigger_background_ingest` + `QUERY_GRAPH`,
  và `hybrid.retrieve` return [] khi `SKIP_MODEL_LOAD=1`. Lớp bắt được là UNIT trực tiếp `_filter_by_sources`
  với metadata giả (video_path đã sanitize) — không cần model. Luôn có test ở tầng logic thuần khi tầng
  tích hợp bị mock (đúng tinh thần "conftest mock che mất lỗi" bên dưới).

## Mindmap: timeout TEMP, normalize nguồn lệch, mode bị bỏ qua

- **Timeout "TEMP TESTING":** `worker.py` từng để `LLM_TIMEOUT_BALANCED=30`, `JOB_TIMEOUT_BALANCED=60`
  (comment "was 90/180") → balanced hay timeout → rơi deterministic nghèo. Đã khôi phục 90/180 và cho
  override qua env `MINDMAP_LLM_TIMEOUT_*`/`MINDMAP_JOB_TIMEOUT_*`. **Bài học:** đừng để giá trị debug
  "TEMP" lọt vào nhánh chính; nếu cần thử nghiệm → dùng env, đừng sửa hằng số.
- **Normalize nguồn lệch chuẩn:** worker dùng `normalize_video_name` riêng (NFKD, khớp `m['video']`) thay
  vì `canonical_source_stem`. Đã hợp nhất: helper module-level `collect_chunks_for_sources` ưu tiên
  `m['source_stem']` (ingest ghi) → fallback `video`, canonical hoá. Khớp file giống retrieval (space/dấu).
- **Mode bị bỏ qua (bug ẩn):** `run_mindmap_job` nhét `generation_mode` vào field `strategy` và KHÔNG set
  `generation_mode` → `generate_node` luôn đọc mode = "balanced" (fast/quality bị mất). Đã thêm
  `generation_mode` + `strategy_requested` vào `MindmapState` và set đúng; endpoint propagate strategy.
  Test `test_mindmap_graph::test_mode_and_strategy_propagated_to_worker` chốt.
- **JSON repair phải string-aware:** bỏ dấu phẩy thừa bằng regex mù làm hỏng comma trong chuỗi
  (codex bắt). `_repair_json_text` quét ký tự, chỉ bỏ `,` trước `}`/`]` khi NGOÀI chuỗi.
- **Gap test mindmap:** conftest mock `MINDMAP_GRAPH` → graph thật không được dựng. Đã thêm
  `test_mindmap_graph.py` dựng `build_mindmap_graph` THẬT (callable stub) — bắt lỗi pydantic/langgraph.
- **Giới hạn đã biết — Huỷ mindmap (FE):** nút Huỷ chỉ dừng polling FE; job BE vẫn chạy xong và
  `append_mindmap` đã lưu map trong lúc sinh → map có thể hiện ở lần fetch sau. Huỷ thật cần
  cooperative-abort (cờ cancel + worker/TimeoutTracker kiểm) — chưa làm (codex review). Chấp nhận.

## Đừng nâng langgraph/langchain lên 1.x trên máy dev này

- **Root cause:** Nâng env lên langgraph 1.x kéo `ormsgpack` — binary bị Windows Application Control chặn → app vỡ hoàn toàn ở import-time. Code lại vốn viết cho langchain 0.3.x / langgraph 0.2.x (API `langchain.retrievers.EnsembleRetriever`, `SqliteSaver(conn)`), nên việc nâng lên 1.x còn kéo theo cả migrate API (`langchain.retrievers` → `langchain_classic.retrievers`).
- **Prevention:**
  1. Pin chặt langgraph/langchain trong `requirements.txt` (đã làm). Đặc biệt pin `langgraph-checkpoint==2.0.23` để không nhảy sang bản ormsgpack.
  2. Sau mọi `pip install`/đổi dependency, chạy `python -c "import app.graphs.query_graph"` để bắt lỗi import-time ngay.
  3. Khi tra phiên bản tương thích, dùng PyPI JSON (`requires_dist`) để xác định cutover dependency thay vì đoán.
- **Test env:** dùng global `python` để chạy pytest; cả global lẫn `.venv` đều resolve về `.venv\Lib\site-packages` trên máy này.

## conftest mock che mất lỗi build graph thật

- **Root cause:** `tests/conftest.py` gán `be_main.QUERY_GRAPH = _MockQueryGraph()` → `StateGraph(QueryState)` thật KHÔNG bao giờ được dựng trong test. Suite xanh 100% nhưng app thật vỡ ở startup (pydantic 2.12 + NotRequired).
- **Prevention:** Có test dựng graph THẬT bằng `build_query_graph(...)` với callable stub (xem `tests/_qg_build.py` + `test_crag_graph.py`/`test_hitl_graph.py`). Mọi thay đổi schema `QueryState` hay dependency phải chạy nhóm test này.

## Rerank (Two-Stage Retrieval) cần candidate pool RỘNG ở Stage 1 mới có tác dụng

- **Root cause:** Rerank chỉ sắp xếp lại tài liệu mà Retriever đưa cho nó, KHÔNG tìm tài liệu mới. Nếu Stage 1 (RetrieveFAISS) chỉ lấy đúng `HYBRID_TOP_K` (=4) thì cross-encoder không có gì để lọc → vô dụng. Phải để Stage 1 lấy rộng (`RERANK_CANDIDATE_K`, mặc định 20) rồi Stage 2 lọc xuống `RERANK_TOP_N`.
- **Thiết kế (đã làm):** module `app/domains/retrieval/rerank.py` (backend cắm-rút: cross_encoder/cohere/llm/none), lazy-load + cache model, guard `SKIP_MODEL_LOAD`, MỌI lỗi → fallback `IdentityReranker` (giữ nguyên thứ tự). Node `RerankDocuments` chèn giữa `RetrieveFAISS` → `ContextBuilder`, **chỉ wire khi `RERANK_ENABLED=1`** (tắt → topology graph y hệt cũ). Có timeout riêng `RERANK_TIMEOUT_SEC` (quá hạn → giữ nguyên thứ tự).
- **Prevention:**
  1. Default OFF. Bật rerank PHẢI kèm `RERANK_CANDIDATE_K > RERANK_TOP_N` mới có lợi.
  2. `cross_encoder` dùng `sentence-transformers` (đã có dep) — KHÔNG thêm dependency mới (tránh bẫy pin ở known-issues). `cohere` là optional, phá tính offline.
  3. Sau khi đổi env rerank trong test: `cfg.reload()` + `rerank.reset_cache()` (model cache theo backend|model|batch).
  4. Verify: `python -c "import app.graphs.query_graph"` + build graph THẬT với `RERANK_ENABLED=1` (xem `tests/test_rerank_graph.py`) — bắt lỗi pydantic/langgraph khi thêm node, đúng bài học conftest-mock bên dưới.

## Tầng NLI (contradiction-check) — mirror rerank, KHÔNG bump dependency

- **Bối cảnh:** embedding (bi-encoder) có điểm mù — cosine cao nhưng nghĩa ngược (phủ định/đổi thực thể/thời gian/con số). Thêm node `VerifyContext` quét cặp chunk top-K bằng mDeBERTa NLI, loại chunk hạng thấp khi mâu thuẫn với chunk hạng cao.
- **Thiết kế (đã làm):** module `app/domains/retrieval/nli.py` mirror `rerank.py`: engine cắm-rút (`MDebertaNli`/`NullNli`), lazy-load + cache theo model-name, guard `SKIP_MODEL_LOAD`, **MỌI lỗi/timeout → passthrough (không loại chunk nào)**. Node `VerifyContext` chèn `RetrieveFAISS → [Rerank] → [VerifyContext] → ContextBuilder`, **chỉ wire khi `NLI_ENABLED=1`** (tắt → topology y hệt cũ). Dùng `transformers`/`torch` đã có — chỉ thêm leaf-dep `sentencepiece` (tokenizer DebertaV2, KHÔNG chạm langgraph).
- **Prevention:**
  1. Default OFF. Thêm `nli.reset_cache()` + `cfg.reload()` sau khi đổi env NLI trong test.
  2. Sau khi thêm dep (`sentencepiece`): verify `python -c "import app.graphs.query_graph"` vẫn OK (đừng để pip kéo theo bản transformers/torch mới làm vỡ pin).
  3. Phải có test build graph THẬT với `NLI_ENABLED=1` (xem `tests/test_nli_graph.py` qua `_qg_build.py`) — bắt lỗi pydantic/NotRequired khi thêm field `context_conflicts`/`rerank_scores` vào `QueryState` (đúng bài học conftest-mock).
  4. Khi nhiều node cùng sửa `retrieved_chunks` (Rerank đổi thành str + lưu `rerank_scores`; VerifyContext loại chunk), node sau PHẢI realign mọi list song song (`rerank_scores`, `retrieved_stems`) theo index giữ lại — lệch độ dài thì downstream (CRAG grade) phải tự bỏ qua an toàn.

## Timeout bọc lời gọi engine PHẢI loại trừ thời gian load/JIT (warm trước, ngoài timeout)

- **Root cause:** `RerankDocuments`/`VerifyContext` bọc engine trong `result(timeout=...)` 10s,
  nhưng engine lazy-load model NGAY trong block đó. Load weights mDeBERTa ~12.7s > 10s → query
  đầu âm thầm fallback identity/[] (rerank vô tác dụng, NLI không khử mâu thuẫn). Phát hiện CHỈ
  qua smoke-test engine THẬT — unit test monkeypatch engine fn nên không bao giờ load model thật.
- **Prevention:**
  1. Mọi node bọc model-call trong timeout PHẢI warm model (load + 1 forward mồi) NGOÀI vùng
     timeout trước. Đã thêm `rerank.warmup()`/`nli.warmup()` (timeout riêng 120s, mọi lỗi → no-op).
  2. Timeout của node chỉ nên bao **inference thực**, không bao chi phí một-lần (load/JIT/trace).
  3. Phải có smoke-test chạy ENGINE THẬT (không monkeypatch) với cờ bật + timeout mặc định —
     đây là lớp duy nhất bắt được loại lỗi "timeout nuốt lần load đầu" (mirror bài học conftest-mock).
  4. Khi đặt giá trị timeout mặc định: ĐO inference thực trên phần cứng đích trước
     (mDeBERTa CPU ~7s/cặp ⇒ 10s là phi thực tế cho NLI; xem known-issues). Đừng đoán.
- **Test env:** `base_env` (`tests/_qg_build.py`) set `SKIP_MODEL_LOAD=1` để `warmup()` không
  kéo model thật trong unit test; test cần engine thật tự bật lại `"0"` + monkeypatch `get_*`.
- **3 bẫy khi viết warmup (codex review bắt được — đã sửa):**
  1. **`with ThreadPoolExecutor` vô hiệu hoá timeout:** `__exit__` gọi `shutdown(wait=True)` →
     vẫn chặn tới khi load xong, dù `result(timeout=...)` đã ném. Phải tạo executor thủ công +
     `finally: ex.shutdown(wait=False)` mới TRẢ NGAY khi quá hạn (load tiếp ở nền). (Lưu ý: node
     `RerankDocuments`/`VerifyContext` cũng dùng `with ...` y hệt → timeout của node cũng KHÔNG
     bỏ được call treo; đây là pattern toàn codebase, residual chưa sửa.)
  2. **Double-load race:** warmup (wait=False) + node có thể cùng gọi `_ensure_model` → thêm
     `threading.Lock` + double-checked locking, gán `self._model` là bước CUỐI.
  3. **Forward mồi mỗi query:** `_load` chạy 1 forward để warm JIT — nếu không gắn cờ sẽ chạy
     LẠI mỗi query (NLI ~11s/query thừa). Gắn `engine._warmed=True` sau lần đầu → các lần sau no-op.
     Cold-path bọc thêm `_warmup_lock` (module) + double-check `_warmed` → warm đúng 1 lần cho mọi
     case thực tế (tuần tự + đồng thời thường). **Residual benign (chấp nhận):** nếu load > timeout
     (warmup nhả lock khi `_warmed` chưa set) + có query đồng thời → forward mồi có thể chạy 2 lần
     (double-LOAD vẫn bị instance `_lock` chặn). Trên CPU này load ~13s ≪ 90–120s nên gần như bất
     khả thi; cố đóng kín sẽ thêm máy móc concurrency không đáng. (codex review 2 vòng)

## langgraph 0.2.x: interrupt() KHÔNG đặt key `__interrupt__` trong kết quả invoke

- **Root cause:** Convention `out["__interrupt__"]` là của langgraph 1.x. Ở 0.2.x, `graph.invoke` khi gặp `interrupt()` trả về state đã commit (không có key đó) và graph tạm dừng. Phát hiện đúng: `graph.get_state(config).next` khác rỗng + đọc `state.tasks[].interrupts[0].value`.
- **Prevention:** Dùng helper `_detect_query_interrupt(graph, thread_id)` trong `main.py` (qua get_state) thay vì kiểm tra key `__interrupt__`. Resume bằng `graph.invoke(Command(resume=decision), config={thread_id})`.
- **Lưu ý review_gate:** logic áp dụng quyết định (edit/reject) phải nằm SAU khi lấy decision (từ `interrupt()` trả về khi resume), KHÔNG tách thành nhánh `if review_decision` riêng — vì khi resume node re-run và decision đến từ giá trị trả về của `interrupt()`.

## Provenance trong query payload cho "lề bằng chứng" (FE) — additive, KHÔNG suy lại stem

- **Bối cảnh:** redesign UI (Phòng đọc) cần hiện nguồn/chunk đã grounding câu trả lời ở
  cột phải. Dữ liệu ĐÃ có trong state graph (`retrieved_sources`/`retrieved_stems`/
  `retrieved_chunks`) nhưng `_finalize_query_job` chỉ copy `answer`/`error` vào payload → FE
  không thấy.
- **Thiết kế (đã làm):** thêm `_attach_evidence(payload, out)` (gọi trong `_finalize_query_job`
  CHỈ khi `has_ans`), set `payload["sources"]` (list stem) + `payload["chunks"]`
  (`[{stem, chunk_id, snippet}]`, cắt 12 chunk × 600 ký tự). Stem/chunk_id ưu tiên PARSE từ
  prefix `"[Nguồn: <stem>, đoạn <id>]"` mà node RetrieveFAISS đã gắn, fallback `retrieved_stems[i]`.
- **Prevention / regression:**
  1. ADDITIVE thuần — bọc `try/except`, không bao giờ làm hỏng đường answer/error. Không đổi
     `status_code`, không đổi history-persist.
  2. KHÔNG suy lại định danh source ở đây (đúng bài học "một nguồn sự thật cho source_stem"):
     chỉ tái dùng stem có sẵn trong state / prefix; FE so khớp bằng `normStem` (mirror `stemBaseLoose`).
  3. Prefix citation là hợp đồng ngầm giữa `query_graph` (RetrieveFAISS) và FE
     (`utils/evidence.js::processCitations`, regex `[Nguồn: …, đoạn N]`). Đổi format ở một phía
     PHẢI đổi phía kia.
  4. Verify: `python -m pytest BE/tests/test_query.py` (global python) — payload có `sources`/`chunks`
     khi có answer, vắng khi lỗi.
