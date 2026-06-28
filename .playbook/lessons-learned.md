# Lessons Learned

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

## langgraph 0.2.x: interrupt() KHÔNG đặt key `__interrupt__` trong kết quả invoke

- **Root cause:** Convention `out["__interrupt__"]` là của langgraph 1.x. Ở 0.2.x, `graph.invoke` khi gặp `interrupt()` trả về state đã commit (không có key đó) và graph tạm dừng. Phát hiện đúng: `graph.get_state(config).next` khác rỗng + đọc `state.tasks[].interrupts[0].value`.
- **Prevention:** Dùng helper `_detect_query_interrupt(graph, thread_id)` trong `main.py` (qua get_state) thay vì kiểm tra key `__interrupt__`. Resume bằng `graph.invoke(Command(resume=decision), config={thread_id})`.
- **Lưu ý review_gate:** logic áp dụng quyết định (edit/reject) phải nằm SAU khi lấy decision (từ `interrupt()` trả về khi resume), KHÔNG tách thành nhánh `if review_decision` riêng — vì khi resume node re-run và decision đến từ giá trị trả về của `interrupt()`.
