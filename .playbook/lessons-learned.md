# Lessons Learned

## Summary v3 Phase 5 (2026-07-16): coverage judge — JUDGE-ONLY, flag-gated, cache key gồm chế độ coverage

- **Root cause / bối cảnh:** bản tóm tắt không có chẩn đoán chất lượng có cấu trúc cho nội dung
  thiếu (missing) / không được nguồn hậu thuẫn (unsupported) / mơ hồ (vague). Cần một tầng CHẤM
  điểm mà KHÔNG viết lại, KHÔNG auto-repair.
- **Thiết kế (đã làm):** cờ `SUMMARY_COVERAGE` (mặc định OFF). Module thuần
  `services/summary/pipeline/coverage.py`: `build_coverage_payload` (trích artifact source-backed:
  overview + section title/summary/key_points/facts + study.key_concepts — KHÔNG gửi raw chunk,
  KHÔNG gửi pointer/id để model không bịa trang/nguồn), `build_coverage_prompt` (luật JSON keys +
  "do not rewrite"), `sanitize_coverage` (list ép str + cap, vague ép bool, chỉ giữ COVERAGE_KEYS,
  non-dict→None), `judge_coverage(record, *, ask_fn, enabled)` (inject ask_fn → test được bằng fake
  LLM; nuốt MỌI lỗi/JSON hỏng → None). Adapter `LocalSummaryPipeline.coverage` resolve cờ + bọc
  ask_ai (threadpool+timeout, temp 0). Graph `assemble_node` gọi `getattr(pipeline,"coverage",None)`
  SAU dedupe_record, TRƯỚC persist — pipeline không có method thì bỏ qua (back-compat stub cũ).
- **Prevention / bài học:**
  1. Coverage đổi output khi bật → **PHẢI vào content_hash** (thêm param `coverage`), KHÔNG chỉ bump
     PIPELINE_VERSION. Bump v5→v6 vô hiệu cache cũ chung; nhưng cùng version, coverage ON vs OFF là
     HAI output khác nhau → nếu không hash cờ coverage, bản cache lúc OFF bị trả về khi bật ON
     (stale no-coverage). Cả route (`_summary_input_and_hash`) LẪN `collect_node` phải mirror cờ.
  2. Judge phải là JUDGE-ONLY tuyệt đối: 2 lớp guard (judge tự nuốt lỗi + graph bọc try) để judge
     lỗi KHÔNG BAO GIỜ chặn job tóm tắt. Test chứng minh: fake LLM raise / JSON hỏng → record vẫn
     done, chỉ thiếu key `coverage`.
  3. Inject `ask_fn` (không gọi thẳng ask_ai trong hàm judge) → test pure với fake LLM, không cần
     model/monkeypatch. Adapter mới lo model/timeout/threadpool.
  4. Coverage ADDITIVE: OFF → record omit key `coverage` byte-for-byte như cũ. Không đụng
     overview/sections/study — test chứng minh text không đổi sau judge.
- **Regression:** `test_summary_coverage.py` (17: sanitize valid/coerce/cap/vague/non-dict/unknown-
  keys/item-len, payload source-backed + no pointer leak, prompt JSON keys + no-rewrite, judge
  disabled/valid/malformed/raises/no-mutate), `test_summary_schema.py` (v6, content_hash gồm coverage,
  build_record omit/include coverage), `test_summary_graph.py` (skip khi None, include khi judge trả
  diag + overview/sections/study không đổi + persist atomic, judge failure không fail job). 107 non-route
  summary pass. KHÔNG đụng FE.

## Summary v3 Phase 4 (2026-07-16): dedup THUẦN sau assembly — khớp CHÍNH XÁC chuẩn hoá, không gộp mờ

- **Root cause / bối cảnh:** tóm tắt nhiều mục có thể lặp cùng fact/key_point (mục con nhắc lại mục
  cha; study gom facts các section). Cần tỉa lặp mà KHÔNG viết lại, KHÔNG bịa, KHÔNG mất fact duy nhất.
- **Thiết kế (đã làm):** module thuần `services/summary/pipeline/dedup.py` — `normalize_text` (lower +
  strip + gộp khoảng trắng + bỏ dấu câu nhẹ, GIỮ diacritics), `dedupe_strings` (khớp CHÍNH XÁC theo
  normalize; trùng → giữ bản DÀI hơn, bằng → giữ bản đầu; giữ thứ tự), `dedupe_facts/sections/study/
  record`. Gọi 1 chỗ: `summary_graph.assemble_node` SAU `build_record`, TRƯỚC persist (không node mới,
  không đổi cancel/error/done-atomic). Dedup: section.key_points + facts.*; study.* lists; self_check
  theo câu hỏi chuẩn hoá; recommended_review theo key `(chunk_id, section_title, reason)`. KHÔNG đụng
  summary/overview/chunk_refs/pointers.
- **Prevention / bài học:**
  1. Dedup văn bản người dùng thấy → CHỈ khớp CHÍNH XÁC bản chuẩn hoá (case/space/dấu câu). KHÔNG
     containment/fuzzy: "Đệ quy" là substring của "Đệ quy tuyến tính" nhưng KHÁC nghĩa — gộp = mất
     fact/gộp ý không liên quan. Bảo thủ thắng thông minh ở đây.
  2. GIỮ diacritics khi chuẩn hoá (đừng NFKD bỏ dấu): "bàn" ≠ "bán". Bỏ dấu = gộp nhầm homograph.
  3. Tie-break "giữ bản dài hơn" CHỈ kích hoạt khi normalize BẰNG nhau (khác dấu câu/hoa-thường);
     biến thể thêm TỪ mới normalize KHÁC → giữ cả hai (đúng: không mất chi tiết). (Test ban đầu sai kỳ
     vọng — tưởng "Đệ quy" gộp vào "Đệ quy: hàm gọi..." nhưng chúng normalize khác nên đều giữ.)
  4. Đổi NỘI DUNG output (dù thuần, deterministic) → bump PIPELINE_VERSION v4→v5, cache cũ chưa-dedup
     miss + tái sinh. schema_version GIỮ 2 (shape không đổi).
- **Regression:** `test_summary_dedup.py` (13: normalize, exact-dup, keep-longest, giữ thứ tự,
  conservative-không-gộp-na-ná, giữ-fact-thêm-từ, facts/sections/study/self_check/review dedup, pointer
  không mất, record null-safe), `test_summary_graph.py::test_dedup_removes_repeated_keypoints_and_facts`,
  `test_summary_schema.py` version v5. 85 non-route summary pass. KHÔNG đụng FE.

## Summary v3 Phase 3 (2026-07-16): study là trục `mode` (mục đích), KHÔNG phải length_mode (độ dài)

- **Root cause / bối cảnh:** "study/ôn tập" từng dễ bị mô hình hoá như một giá trị length_mode thứ 4.
  Sai: độ dài (short/medium/detailed) và mục đích (standard/study) là HAI trục độc lập — gộp thì mất
  tổ hợp short+study, medium+study...
- **Thiết kế (đã làm):** thêm trục `mode = standard|study` TRỰC GIAO length_mode. Thread qua route
  `/generate-summary` (thiếu→standard; sai→400 rõ ràng, KHÁC length_mode âm thầm rơi về medium) →
  content_hash (mode trong hash, standard/study khác cache) → job state → graph → record `mode` +
  block `study`. Block `study` DETERMINISTIC (0 LLM) `services/summary/pipeline/study.py::build_study`:
  gom facts (Phase 1) + pointers (Phase 2) các section → key_concepts/definitions/formulas/examples/
  common_mistakes + self_check (suy từ open_questions/important_terms, fallback key_points) +
  recommended_review (CHỈ từ pointer thật). FE: picker standard/study + render block study null-safe.
- **Prevention / bài học:**
  1. Phân biệt "độ dài" vs "mục đích/định dạng" = trục riêng. Đừng nhồi option mới vào enum sẵn có
     chỉ vì rẻ — mất tính tổ hợp + ngữ nghĩa lẫn lộn.
  2. mode SAI → 400 (người dùng gửi rác cần biết); length_mode sai → rơi default (độ dài chỉ là gợi ý).
     Hai policy validate khác nhau CÓ CHỦ ĐÍCH — ghi rõ.
  3. study block THUẦN từ facts/pointers đã có → 0 LLM, 0 bịa, test được. self_check suy từ facts
     (không hỏi model sinh câu ngoài tài liệu). recommended_review chỉ từ pointer thật (không bịa trang).
  4. mode ĐỘC LẬP cờ SUMMARY_FACTS: facts vắng (flag OFF) → study degrade an toàn (key_concepts/
     self_check fallback key_points, block facts rỗng, review vẫn có từ pointers). Không ép facts.
  5. Đổi shape output (thêm mode/study) → bump PIPELINE_VERSION v3→v4. schema_version GIỮ 2 (additive);
     record cũ thiếu mode → FE default standard, thiếu study → không render.
- **Regression:** `test_summary_study.py` (7: aggregate+dedupe, self_check open_q→terms→fallback,
  không bịa quá facts, review chỉ từ pointer thật, degrade rỗng), `test_summary_schema.py` (mode trong
  hash, build_record default/invalid/study/none, version v4), `test_summary_graph.py` (standard→no study,
  study→block từ facts+pointers). FE `summaryJob.test.js` (SUMMARY_MODES, normalize mode/study),
  `api.test.js` (generateSummary gửi mode). BE 72 non-route pass; FE 129 pass + build.
- **Lưu ý env:** `test_summary_routes.py` giờ 11 fail — TẤT CẢ là `401 UNAUTHORIZED` (test client không
  auth; AUTH_PROTECT bật). Pre-existing (baseline cũng 401), gồm cả test invalid-mode-400 (401 chặn
  trước khi tới handler). Không sửa auth env (ngoài phạm vi). Mock route đã cập nhật đúng chữ ký mới
  (`_summary_input_and_hash(sources, length_mode, mode)`, `_start_summary_job(..., length_mode, mode)`).

## Summary v3 Phase 2 (2026-07-16): source pointer suy DETERMINISTIC từ metadata chunk, KHÔNG từ LLM

- **Root cause / bối cảnh:** summary section chỉ có `chunk_refs` (id) — không đủ metadata để FE
  "quay lại đúng trang/mục" review. page/source/heading ĐÃ có ở ingest nhưng
  `collect_mindmap_input` DROP (chỉ giữ key/text/heading_path/chunk_keys).
- **Thiết kế (đã làm):** (1) `input_collector` propagate ADDITIVE `source_stem/source_id/page/
  chunk_index` per chunk (chỉ khi có; mindmap bỏ qua field lạ — guard test). (2) module thuần
  `services/summary/pipeline/pointers.py::build_pointers(mm_input, chunk_ids)` map id→pointer
  {chunk_id, source_id, source_stem, page, section_title, heading_path, chunk_index} — theo thứ tự
  chunk_ids, dedupe, BỎ id lạ (không bịa). (3) `attach_pointers` gắn `section["pointers"]` từ
  chunk_refs, gọi trong `summary_graph.assemble_node` TRƯỚC `sanitize_sections`. (4) schema
  `sanitize_pointers` chuẩn hoá (giữ POINTER_KEYS, cần chunk_id, dedupe, field lạ bỏ).
- **Prevention / bài học:**
  1. Metadata điều hướng nguồn (page/section) phải suy DETERMINISTIC từ chunk metadata, TUYỆT ĐỐI
     không nhờ LLM sinh (LLM bịa số trang/tên mục). Pointer 0 LLM, thuần, test được.
  2. `heading_path` trong mm_input là CHUỖI (' > ' join) — mindmap skeleton còn `.split(' > ')`.
     KHÔNG đổi sang list ở mm_input (vỡ mindmap); pointer tách list Ở TẦNG POINTER, giữ chuỗi ở mm.
  3. Field đi qua collector DÙNG CHUNG (mindmap+summary) → ADDITIVE, guard test "mindmap fields
     intact + build được" (bài học input_collector chung).
  4. Output shape đổi (thêm `pointers`) → vẫn **bump PIPELINE_VERSION** (v2→v3) dù pointers là suy
     diễn deterministic: cache cũ thiếu pointers phải miss + tái sinh. schema_version GIỮ 2 (additive).
  5. Pointer ĐỘC LẬP cờ SUMMARY_FACTS — suy từ chunk_refs, chạy cả summary chuẩn (facts OFF).
- **Regression:** `test_summary_pointers.py` (9: map metadata, section_title=mục cuối heading, sub-key→
  parent meta, missing→None, unknown id bỏ, dedupe giữ thứ tự, no-heading→None, attach per-section);
  `test_summary_schema.py` (sanitize_pointers keep/dedupe/drop-no-id, missing safe, non-list, sanitize_sections
  giữ pointers + chunk_refs không đổi, omit khi rỗng, version=v3); `test_mindmap_input_collector.py`
  (metadata additive + mindmap fields intact). 58 non-route summary + 14 mindmap pass.

## Summary v3 Phase 1 (2026-07-16): facts ledger là IR chuẩn, flag-gated, cache vô hiệu qua PIPELINE_VERSION

- **Root cause / bối cảnh:** tóm tắt v2 sinh `summary` thẳng từ chunk, KHÔNG có tầng trung gian có
  cấu trúc → khó suy ra study-mode/pointer/coverage sau này, và summary dễ trôi khỏi nội dung nguồn.
- **Thiết kế (đã làm):** thêm `facts` ledger per-section (7 key: key_points/definitions/formulas/
  examples/important_terms/common_mistakes/open_questions) sau cờ `SUMMARY_FACTS` (mặc định OFF).
  MỘT LLM call/section trả cả facts LẪN summary; prompt bắt "trích facts trước, viết summary CHỈ từ
  facts". `_summarize_one(..., with_facts=False, two_pass=False)` — `two_pass=True` raise
  NotImplementedError (seam tương lai, KHÔNG cài). `summarize_sections(with_facts=None)` resolve từ
  `get_settings().summary_facts`. Nguồn sự thật FACTS_KEYS + `sanitize_facts` ở `schema.py`, import
  vào summarize (DRY, một hành vi coerce/cap). `sanitize_sections` giữ facts khi có, BỎ key nếu rỗng.
- **Prevention / bài học:**
  1. Thêm field output ảnh hưởng cache → **bump `PIPELINE_VERSION`** (`summary_sections_v1`→`v2`);
     content_hash tự vô hiệu cache cũ, không migrate.
  2. Cờ tính năng OFF phải bảo toàn shape cũ BYTE-FOR-BYTE (trừ version): section không facts → KHÔNG
     có key `facts` (không sinh key rỗng). Test `test_with_facts_false_omits_facts_and_keeps_old_shape`.
  3. facts là free-text → chỉ coerce str + strip + drop-empty + cap (MAX_FACT_ITEMS), KHÔNG lọc theo
     allowed_set như `chunk_keys` (id). `chunk_keys` vẫn lọc id thật (chống bịa, bài học enrich).
  4. Lỗi LLM/degraded → KHÔNG bịa facts (section giữ skeleton, không key facts). Trung thực degraded.
- **Regression:** `test_summary_schema.py` (version bump, sanitize_facts coerce/drop/cap, sanitize_sections
  giữ/bỏ facts, build_record pass-through), `test_summary_summarize.py` (parse 7 key, coerce, hallucinated
  chunk_keys lọc, retry-once, degraded không bịa, two_pass raise, prompt chứa 7 key + length rule).
  44 passed (schema/summarize/sections/synthesize/graph-THẬT/store).
- **Lưu ý env:** `test_summary_routes.py` có 8 fail TỪ TRƯỚC (xác nhận bằng stash: fail y hệt trên
  origin/main sạch) — do redis/ollama warmup trong route test, KHÔNG liên quan facts.

## Container mind-elixir (2026-07-16): `absolute inset-0` bị chính thư viện ghi đè → canvas sụp chiều cao

- **Bối cảnh:** phần tử truyền vào `new MindElixir({ el })` trong `MindElixirView.jsx` mang class
  Tailwind `absolute inset-0`. mind-elixir constructor set `el.style.position = "relative"` INLINE
  (verified dist: `C.style.position = "relative"` với `C = this.el`). Inline THẮNG class → `absolute`
  vô hiệu, `inset-0` không còn tác dụng (inset chỉ áp cho positioned-absolute/fixed). `el` rơi về
  normal-flow không chiều cao rõ ràng.
- **Root cause (đo trực tiếp qua Playwright A/B trên bundle thật):** `.map-container{height:100%}`
  (CSS của mind-elixir) resolve theo chiều cao của `el`; `el` sụp về chiều cao NỘI DUNG → cả canvas
  sụp. Đo: `el`/`.map-container`/`me-nodes` = **363px** trong slot **800px** (khớp triệu chứng báo
  cáo ~307/747). Hệ quả: vùng dưới là dead space (kéo/click không ăn), `scaleFit()` tính theo container
  nhỏ nên "Vừa khung" căn sai khung nhìn.
- **Cách xử lý:** `el` sang normal-flow lấp đầy cha đã-định-cỡ: wrapper giữ `relative flex-1 min-h-0
  overflow-hidden`, ref target đổi `absolute inset-0` → `h-full w-full min-h-0`; thêm backstop CSS
  `.me-container{ width:100%; height:100%; min-height:0 }` (không phụ thuộc Tailwind class sống sót
  qua purge). Sau fix đo lại: `el`/`.map-container` = **800px** = slot, `elementFromPoint` ở đáy =
  `.map-container` (live canvas, hết dead space). Không đụng pan/zoom (unchanged PR #8).
- **Prevention:**
  1. Phần tử giao cho thư viện render (mind-elixir, và mọi lib tự set inline style) → ĐỪNG dựa vào
     positioning class (`absolute inset-0`) mà lib có thể ghi đè bằng inline `position`. Cho nó
     normal-flow `w/h-full` dưới một cha đã định-cỡ, hoặc set width/height 100% trực tiếp.
  2. Verify layout bằng SỐ ĐO thật (Playwright đo `getBoundingClientRect` A/B before/after trên
     CHÍNH bundle đã build + lib thật), đừng tin "build xanh" — cùng họ bài học mind-elixir/style.
  3. `elementFromPoint(x, đáy)` là cách rẻ + trung thực để chứng minh "hết dead space" mà không phải
     tổng hợp sự kiện pointer (pan của mind-elixir bám pointer-capture, khó tái tạo bằng synthetic
     event ngoài app thật → dùng làm tín hiệu phụ, đừng gate).

## Viewport sơ đồ (2026-07-15): pan/zoom ĐÃ CÓ SẴN — thiếu là chrome, không phải tính năng

- **Bối cảnh:** user báo "sơ đồ lớn khó di chuyển". Đọc `node_modules/mind-elixir/dist/MindElixir.js`
  TRƯỚC khi viết code: thư viện đã tự lo TOÀN BỘ pan/zoom — wheel = pan, shift+wheel = pan ngang,
  ctrl/cmd+wheel = zoom bám con trỏ, kéo-trái nền = pan (nhờ `mouseSelectionButton: 2` viewer đã set),
  touch/pointer pan, Space+kéo, và cả keymap `Ctrl/Cmd + =/-/0`. Không thiếu một gesture nào.
  Thiếu là **khả năng khám phá**: không có readout, không fit, không reset, nền không có con trỏ grab
  (thư viện chỉ đặt grab khi `.space-pressed`), dòng gợi ý "kéo nền" lại bị `display:none` dưới 640px —
  tức là ẩn đúng chỗ cảm ứng cần nhất. Viết pan/zoom custom sẽ là đập đi làm lại thứ đã chạy tốt,
  và còn phải đánh nhau với chính transform thư viện ghi lên `.map-canvas`.
- **Bug thật tìm ra khi đọc dist (không phải từ report):** `zoomBy` cũ kẹp `Math.min(2, Math.max(0.4, …))`
  trong khi mặc định thư viện là `scaleMin = 0.2` / `scaleMax = 1.4`, VÀ guard trong `scale()` là
  **REJECT chứ không phải clamp**: `if (e < this.scaleMin && e < this.scaleVal || e > this.scaleMax && e > this.scaleVal) return`.
  ⇒ Nút "Phóng to" **chết im lặng ở 1.4** (bấm tiếp không làm gì, không lỗi, không log). Sửa: đọc
  `mind.scaleMin`/`mind.scaleMax` từ CHÍNH instance rồi kẹp trước khi gọi — scale() luôn nhận.
- **Prevention:**
  1. Trước khi xây tính năng viewport/tương tác trên một thư viện render: **grep dist của nó** tìm
     `wheel`/`scale`/`move`/keymap handler. Ở đây `scaleFit()`, bus event `scale`, `scaleMin/Max` đều
     có sẵn và không hề dùng — build custom là thêm nợ, không thêm giá trị.
  2. Hằng số giới hạn của thư viện phải ĐỌC TỪ INSTANCE, đừng hardcode ở FE. Hardcode lệch với thật =
     control chết im lặng (loại lỗi không có exception, không test nào bắt, chỉ user thấy).
  3. Đọc kỹ guard là *reject* hay *clamp*. Reject + giá trị ngoài range = no-op câm; clamp thì tự sửa.
  4. `scaleFit()` gọi `Ce(this, !0)` — tham số `true` ÉP nhánh căn-theo-nodes **bất kể** option
     `alignment`. Nên KHÔNG cần truyền `alignment: "nodes"` (doc thư viện gợi ý "better with") →
     tránh được rủi ro đổi `transformOrigin` của `toCenter()` mặc định. Đọc code thắng đọc doc.
  5. `toCenter()` GIỮ NGUYÊN `scaleVal` (`pn` vẽ lại transform với `scale(${this.scaleVal})`) → "đặt lại
     khung nhìn" phải gọi CẢ `scale(1)` LẪN `toCenter()`; chỉ một cái là nửa vời.
- **Regression:** `FE/src/utils/mindmapViewport.test.js` (17 test, THUẦN — không import React/mind-elixir,
  chạy được ở env `node` mặc định vì repo chưa có jsdom). Cover: clamp min/max, `nextScale` kẹp trần/sàn,
  `formatZoom` làm tròn, keymap `+ = - _ 0 f/F`, null khi đang gõ, null khi có ctrl/meta/alt
  (nhường keymap `Ctrl/Cmd +/-/0` sẵn có của thư viện — kiểm tra bảng keymap trong dist trước khi
  bind phím mới, đừng đoán). Suite FE: 104 passed (19 file).
- **Guard non-finite (không phải phòng xa vu vơ):** `scaleFit()` tính
  `this.nodes.offsetHeight / this.container.offsetHeight` — container ẩn/chưa layout cho `0/0 = NaN` →
  `scaleVal = NaN` → `transform: scale(NaN)` vỡ map + readout in "NaN%". `clampScale`/`formatZoom` chặn
  tại tầng thuần.
- **CSS:** đặc tả cố ý — `.me-container{cursor:grab}` = (0,1,0) THUA `.map-container me-parent{cursor:pointer}`
  = (0,1,1) của thư viện nên node giữ pointer, chỉ nền grab; thêm `me-parent:active` (0,2,1) để bấm node
  không nháy grabbing. Verify rule vào bundle bằng `grep` `dist/assets/*.css` (đúng bài học
  "đừng tin build xanh" của mind-elixir/style) — lưu ý grep class arbitrary Tailwind phải tính ký tự
  escape (`min-w-\[46px\]`), grep thô sẽ tưởng nhầm là bị rớt.

## Summary v2 (2026-07-06): thay pipeline 6-technique bằng section-first mirror mindmap

- **Bối cảnh:** tóm tắt cũ (`summarize_advanced.py` FROST/CoD/DANCER/extract/fact-check)
  sync (block request doc dài), không cache, không citation, lưu JSON, không dùng heading
  từ ingest. Thay HẲN (đã xóa file + endpoint `/summarize-documents`, `/summarize-file`,
  `POST /summaries`) bằng pipeline section-first — spec `docs/SUMMARY_V2_SPEC.md`.
- **Thiết kế:** copy nguyên pattern mindmap đã trưởng thành thay vì phát minh mới:
  `services/summary/pipeline/` (schema/sections/summarize/synthesize) import trực tiếp
  `skeleton.py`/`outline.py` của mindmap (plain Python, monolith); `summary_graph.py`
  5 node clone mindmap_graph (guard/cancel/done-atomic-với-result); store sqlite +
  `content_hash` (CÓ length_mode trong hash — đổi độ dài = record khác);
  FE generalize `createMindmapPoller` → `jobPoller.js` + `makeActiveJobStore` —
  wrapper mindmap giữ nguyên API, test cũ pass không sửa.
- **Bài học:** feature mới cùng shape (job nền dài + LLM + cache + poll) → generalize
  hạ tầng CŨ thành module chung với wrapper backward-compatible, đừng copy-paste body.
  Toàn bộ ràng buộc playbook (done atomic, degraded honest, no FE hard-timeout,
  cache-hit không job_id, chunk_refs lọc id thật) được thừa hưởng miễn phí từ template.
- **Regression:** `test_summary_{schema,sections,summarize,synthesize,graph,store,routes}.py`
  (38 test, graph THẬT + route contract cache-hit-no-job_id + old endpoint 404);
  FE `summaryJob.test.js`/`activeJob.test.js`. Suite: BE 289 passed, FE 43 passed.
- **Lưu ý vận hành:** đổi prompt/logic summary → bump `PIPELINE_VERSION`
  (`services/summary/pipeline/schema.py`). `summaries.json` cũ migrate 1 lần khi startup
  → `.migrated`; record legacy render qua `summary_md` fallback trong SummaryModal.

## Cache 3 tầng (Redis): bucket-key encode mọi điều kiện match = chống poisoning theo cấu trúc

- **Bối cảnh (2026-07-06):** thêm semantic response cache + retrieval cache (Redis, fail-open)
  cho pipeline query — spec đầy đủ `docs/SEMANTIC_CACHE_SPEC.md`, module
  `app/domains/cache/llm_cache.py` + `app/clients/redis_client.py`. Điểm cắm: L2 trong
  `main._get_cached_query`/`_set_cached_query` (graph không đổi cho Tier 2, thừa hưởng guard
  history/processing/empty-answer sẵn có) + wrap `_do_hybrid_retrieve` cho Tier 3.
- **Bài học thiết kế:**
  1. Semantic cache KHÔNG so cosine tự do — bucket key sha256(namespace|env|PROMPT_VERSION|
     embedding model|LATE_CHUNKING|index_version|sources|language|category|use_memory_tree);
     chỉ so cosine TRONG bucket. Khác điều kiện = khác bucket = không thể false-hit chéo.
     Đây là dạng tổng quát của bài học "cache key phải hash MỌI input ảnh hưởng output"
     (mindmap content_hash).
  2. index_version = `os.stat(index.json).st_mtime_ns-size` — KHÔNG import store (kéo
     faiss/langchain), KHÔNG đọc nội dung file (nặng). `_save_meta` atomic-replace đảm bảo
     mtime đổi mỗi ingest/delete → mọi cache liên quan tài liệu tự vô hiệu, không cần event bus.
  3. Fail-open phải có "cửa sổ unavailable" (60s) — không thì Redis chết = mỗi request ăn
     0.5s timeout. `mark_unavailable()` khi op lỗi giữa chừng, không chỉ lúc connect.
  4. Risk-classifier deny-regex (personal/realtime) chạy TRƯỚC khi ghi cache. Regex bảo thủ
     có false-positive chấp nhận được (deny nhầm = chỉ mất 1 cơ hội cache) — test semantic
     phải dùng câu hỏi trung tính, đừng dùng câu chứa "password"/"giá"/"hôm nay".
  5. Threshold có sàn cứng 0.80 (clamp + warning; override phải bật cờ riêng) — hạ threshold
     để tăng hit-rate là công thức cache poisoning.
- **Regression:** `tests/test_llm_cache.py` (14 case: exact/semantic hit, bucket miss,
  index_version miss, expired+SREM, fail-open window, floor clamp, risk deny, retrieval
  round-trip, real-graph history bypass). Đổi system prompt qa_chain → bump
  `llm_cache.PROMPT_VERSION`; đổi format `_make_query_cache_key` → sửa
  `llm_cache._parse_cache_key` (lệch = miss im lặng, hướng fail-safe).

## Thư viện render bằng CSS riêng (mind-elixir): import style là PHẦN CỦA API, không phải trang trí

- **Root cause (2026-07-05):** mind-elixir v5 layout hoàn toàn bằng `dist/MindElixir.css`
  (custom elements `me-nodes`/`me-tpc` mặc định inline). Viewer mới (thay ReactFlow) chỉ
  import JS, không import CSS → sơ đồ vỡ hoàn toàn ở CẢ viewer lẫn PNG export, mà build/test
  vẫn xanh (không lớp nào kiểm "CSS có vào bundle"). Kèm 2 bẫy cùng họ: theme override chỉ
  set 4/22 cssVar (var thiếu = declaration invalid vì CSS không có fallback), và snapdom chụp
  `mind.nodes` tách khỏi `.map-canvas` làm descendant selector không match trong clone.
- **Prevention:**
  1. Thêm thư viện render mới → đọc package.json exports tìm `"./style"`; smoke DOM thật
     (mở viewer nhìn bằng mắt/screenshot) chứ đừng tin build xanh.
  2. Override theme → set đủ TOÀN BỘ bộ var thư viện tiêu thụ; guard bằng test liệt kê
     (`FE/src/components/mindmap/theme.test.js`) để version sau thêm var là test đỏ.
  3. snapdom/html2canvas: target chụp phải CHỨA mọi tổ tiên mà CSS selector cần
     (`.map-canvas me-nodes{...}` → chụp `.map-canvas`, không chụp `me-nodes`). Thêm `scale: 2`.
- **Chẩn đoán nhanh loại lỗi này:** mọi text dồn 1 dòng + element không có kích thước
  = layout CSS không được nạp, đừng đi tìm bug data/adapter.

## Text hiển thị cho user phải đi qua MỘT đường render, không phải `<p>{raw}</p>`

- **Root cause (2026-07-05):** chunk text lưu sqlite là markdown mammoth thô (`__bold__`,
  `\(escape\)`). ChatArea render qua react-markdown nên đẹp; 2 surface bằng chứng
  (EvidenceDrawer, lề bằng chứng SidebarRight) lại `<p>{text}</p>` raw → user thấy
  `__Triển khai...\(IDS/IPS\)__`. Cùng một loại data, hai số phận — vì mỗi chỗ tự quyết
  cách hiển thị.
- **Prevention:**
  1. Data có thể chứa markup → dùng chung MỘT component render (`ui/Markdown.jsx::MdSnippet`
     cho trích đoạn; ChatArea giữ map riêng vì có citation-chip). Chỗ mới hiển thị chunk/note
     → dùng lại MdSnippet, đừng tự `<p>`.
  2. Escape-cleanup 2 phía phải MIRROR nhau và ghi chú chéo: BE `clean.py::unescape_mammoth`
     (data mới, tận gốc) ↔ FE `evidence.js::unescapeMd` (data cũ đã lưu). Đổi set ký tự một
     bên phải đổi bên kia.
  3. Set unescape phải BẢO THỦ (chỉ punctuation `. ( ) ! ? , : ; … " '`) — unescape `\# \* \- \[`
     là tự tạo heading/list/link markdown giả từ text vốn được escape có chủ đích.

## Cấu trúc mindmap đến từ INGEST, không phải từ pipeline mindmap

- **Root cause (2026-07-05):** map nông không phải lỗi skeleton/enrich — tài liệu docx không
  mang heading nào tới pipeline (mammoth cần Word Heading styles; sinh viên dùng bold/số tay).
  Sửa prompt/pipeline bao nhiêu cũng không thêm được chiều sâu mà nguồn không mang theo.
  Fix đúng tầng: promote heading heuristic ở `clean.py` (ingest) + map heading qua
  `chunk_index` sống sót sub-split + LLM outline fallback CHỈ khi deterministic bó tay.
- **Prevention:**
  1. Chẩn đoán "output nghèo" → truy NGƯỢC pipeline tới tận nguồn dữ liệu (chunk metadata
     thật trong index.json) trước khi sửa prompt/LLM.
  2. Metadata dẫn xuất đi kèm chunk (heading, span, page) phải map qua ID/index bám theo
     entry (như `chunk_index`), KHÔNG qua alignment `len(a)==len(b)` — mọi bước sub-split/
    lọc sẽ phá alignment và rớt metadata im lặng.
  3. Cache key phải hash MỌI input ảnh hưởng output (cả metadata), không chỉ text — không thì
     fix ingest xong cache vẫn trả kết quả cũ và tưởng fix hỏng. `generator.skeleton_method`
     được persist để chẩn đoán nhanh record đã lưu sinh từ đường nào.
  4. Degraded phải TRUNG THỰC: mọi nhánh no-op (SKIP_MODEL_LOAD, LLM lỗi, outline fail) phải
     khai `degraded/missing`, đừng trả kết quả thiếu như bản hoàn chỉnh.

## Xoá source trên FAISS: LangChain dùng docstore id, legacy raw-FAISS dùng `chunk_id`

- **Root cause:** Hai backend lưu id khác nhau. LangChain FAISS giữ vector theo `docstore_id`
  nội bộ (uuid trong `docstore._dict`), nên `FAISS.delete(ids=...)` KHÔNG nhận trực tiếp
  `chunk_id`. Legacy raw-FAISS (`IndexIDMap`) thì id trong index chính là `chunk_id`.
- **Prevention:**
  1. Nhánh LangChain phải map `chunk_id -> docstore_id` từ metadata trước khi delete; không
     được giả định `chunk_id` là key xoá dùng chung cho cả hai backend.
  2. Mọi lỗi delete-by-id phải rơi về `rebuild_chunk_index(...)` để ưu tiên toàn vẹn
     index/meta. Sai một bước xoá có thể để meta và vector lệch nhau; rebuild là đường lui an toàn.
  3. Regression cần có ở cả hai backend: LC chứng minh map đúng sang `docstore_id`, raw-FAISS
     chứng minh vẫn xoá theo `chunk_id` như cũ.

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

> **(Lịch sử — pipeline mô tả dưới đây đã bị THAY THẾ hoàn toàn ngày 2026-07-04 bởi skeleton-first;
> xem mục "Mindmap: skeleton-first thay pipeline 3-mode/7-strategy" phía trên. `worker.py`
> mode/strategy/`generation_mode`/`multilevel_fast` không còn tồn tại trong code. Giữ mục này làm
> lịch sử vì các bài học chung (đừng để giá trị debug lọt vào nhánh chính, JSON repair phải
> string-aware, conftest mock che gap test) vẫn còn giá trị.)**

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
  **[ĐÃ GIẢI QUYẾT 2026-07-04]** `POST /mindmap-cancel/<job_id>` set cờ thật trong `jobs.sqlite`
  (`request_cancel`); mọi node của graph mới (`_guard()` trong `app/graphs/mindmap_graph.py`) và cả
  vòng lặp theo batch trong `enrich_branches`/trước lời gọi LLM trong `extract_relations` đều kiểm
  tra cờ này — huỷ giữa chừng dừng đúng lúc và KHÔNG persist record.
- **FE `record` sau generate rebuild field-by-field → rớt field v2 (schema_version/relations/generator):**
  `SidebarRight.jsx::handleGenerateMindMap` (nay là `runMindmapGeneration`) dựng lại `record` từ `data`
  (kết quả job) bằng cách liệt kê từng field (`id/title/nodes/diagram/sources/createdAt/strategy/...`)
  thay vì giữ nguyên `data` — mọi field KHÔNG có trong danh sách bị rớt âm thầm. V2 record có thêm
  `schema_version`/`relations`/`generator` (BE `services/mindmap/pipeline/schema.py::build_record`)
  mà danh sách cũ không liệt kê → sơ đồ vừa tạo xong (chưa reload) không hiện quan hệ/banner degraded,
  dù `normalizeMindmapRecord` (Task 13) đã hỗ trợ đủ. Chỉ lộ ra khi F5 lại (list `/mindmaps` trả full
  record đã lưu, không đi qua đường rebuild field-by-field này).
  **Sửa (Task 14):** `record = { ...data, id: ..., title: ..., ... }` — spread `data` TRƯỚC, các field
  tường minh sau chỉ để backfill default (key sau đè key trước trong object literal, không mất field
  nào của `data`). **Prevention:** khi FE "đóng gói lại" một response BE thành state cục bộ, ưu tiên
  `{ ...response, ...overrides }` thay vì liệt kê thủ công từng field — liệt kê thủ công là nợ kỹ thuật
  âm thầm mỗi khi BE thêm field mới (không lỗi build/test nào bắt được, chỉ lộ qua so sánh dữ liệu thực).

## Mindmap: skeleton-first thay pipeline 3-mode/7-strategy (2026-07-04)

- **Bối cảnh / root cause:** pipeline mindmap cũ ~2113 dòng: 3 mode (fast/balanced/quality) × 7
  strategy (`single_call_schema`/`mindmap_v2`/`cmgn_light`/`cmgn`/`multilevel_fast`/`iterative`/
  deterministic) × fallback chain × LLM call budget × một LLM-call riêng để build "visual diagram"
  (2 artifact trùng nhau: `nodes` + `diagram` cho cùng nội dung). "Cache mechanism §5" trong tài
  liệu cũ mô tả cache theo content hash + strategy + model NHƯNG code không hề có bước lookup —
  progress chỉ IN ra "Đang lưu cache" rồi ghi thẳng, không đọc lại (cache ma, chưa từng tồn tại
  trong code dù tài liệu khẳng định có). Ngoài ra: FE không có đường gửi `mode` xuống nên server
  luôn chạy nhánh mặc định; `worker.py` tự đọc `index.json`/`chunks.sqlite` trực tiếp xuyên ranh
  giới service (vi phạm tách monolith/service); nút Huỷ ở FE chỉ dừng polling, job BE vẫn chạy xong
  và lưu map (huỷ giả — xem known-issues cũ).
- **Thiết kế đã làm:** thay bằng skeleton-first — cấu trúc mục lục tài liệu (`heading_path`, fallback
  Memory Tree section, fallback TF-IDF cluster) làm KHUNG XƯƠNG tất định (0 LLM, deterministic,
  không bao giờ là rác) trước; LLM chỉ được gọi ở 2 chỗ hẹp: Enrich (mỗi nhánh 1 call, song song,
  `chunk_refs` do LLM trả về bị lọc lại theo tập id hợp lệ để chặn bịa) và Relations (1 call tìm
  quan hệ chéo, validate lại id/trùng cạnh/tự-trỏ, cap 20). Một LangGraph 5 node
  (`app/graphs/mindmap_graph.py`: CollectInput → Skeleton → Enrich → Relations → AssemblePersist)
  thay toàn bộ cây quyết định mode/strategy cũ — không còn rẽ nhánh theo kích thước dữ liệu. Cache
  THẬT: `content_hash` (sha256 của `PIPELINE_VERSION` + sources + chunk text) lookup trong
  `memory/mindmaps.sqlite` (`app/domains/mindmap/store.py::get_by_hash`) TRƯỚC khi tạo job — cache
  hit trả thẳng record, không tốn LLM call nào. LLM lỗi ở nhánh/relations nào → giữ nguyên khung
  xương phần đó, đánh dấu `generator.degraded=true` + `generator.missing=[...]` thay vì fallback
  im lặng hoặc bịa dữ liệu. Cancel THẬT: cờ trong `jobs.sqlite`, mọi node (`_guard()`) và cả vòng
  lặp batch trong enrich/relations đều kiểm tra trước khi tiếp tục — huỷ giữa chừng không persist.
- **Prevention / regression:**
  1. Đổi prompt hoặc logic bất kỳ node nào (skeleton/enrich/relations/schema) → PHẢI bump
     `PIPELINE_VERSION` trong `services/mindmap/pipeline/schema.py` để tự vô hiệu cache cũ (nếu
     không, kết quả sinh từ logic cũ tiếp tục được trả về do trùng `content_hash`).
  2. Mọi thay đổi `MindmapState` phải có test dựng graph THẬT (`build_mindmap_graph(...)` với
     pipeline/callback stub) — đúng bài học "conftest mock che mất lỗi build graph thật" ở mục
     dưới; không được chỉ test qua mock.
  3. Đặt timeout mặc định (`MINDMAP_LLM_TIMEOUT_SEC`) dựa trên số đo THẬT trên phần cứng đích, không
     đoán — xem số đo 2026-07-04 (Ollama CPU local): enrich 3 nhánh ≈ 86s, relations ≈ 14s, tổng
     ≈ 100s cho 4 chunk có heading; 57 chunk không heading (fallback cluster) ≈ 58s.
  4. Service (nếu bật `MINDMAP_SERVICE_ADDR`) KHÔNG được tự đọc `index.json`/`chunks.sqlite` — input
     phải được monolith gom sẵn (`input_collector.py`) rồi truyền qua wire (gRPC per-stage:
     Skeleton/EnrichBranches-stream/Relations), giữ đúng ranh giới service đã học ở lần trước.

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

## QR Canonical Text Store: video=canonical, index slim, sqlite derived

- **Bối cảnh / root cause:** Sau khi thêm late chunking, `index.json` lưu cả `text` lẫn vector/metadata cho mỗi chunk (~13MB cho 245 chunk) trong khi QR video (`videos/*.mp4`) cũng chứa chính text đó dạng QR — trùng lặp dữ liệu và video đóng vai trò "write-only". Đồng thời, BM25 nạp corpus hàng loạt từ `index.json` khi khởi động/thay đổi, các site khác cũng đọc text rất nhiều lần, nên không thể giải mã video on-demand hàng loạt được vì quá chậm.
- **Thiết kế (đã làm):** 
  1. Tách biệt 3 store: `videos/*.mp4` làm canonical archive + recovery, `index/index.json` gọn nhẹ chỉ chứa pointer `(video, frame_index)` + metadata, và `index/chunks.sqlite` lưu trữ text runtime (dẫn xuất, tái dựng được từ video).
  2. Module truy cập duy nhất `chunk_text_store.py` (Sqlite + fallback inline index.json + decode-on-demand từng frame với LRU cache).
  3. Quá trình ingest: lưu video QR ở `save_qr_frames_to_video`, luôn lưu text vào SQLite qua `chunk_text_store.put_many`, và loại bỏ field `text` trong `index.json` nếu có video QR hợp lệ (giữ lại inline `text` làm fallback an toàn khi ghi video lỗi).
  4. Chuyển đổi toàn bộ các nơi đọc text (BM25, memory tree, mindmap worker, main app endpoints) sang `chunk_text_store`.
- **Prevention / lessons:**
  - Thứ tự frame giải mã trong video QR cực kỳ quan trọng; đổi `decode_video_qr` sang trả về list tuple `(frame_index, chunk_text)` theo đúng thứ tự frame thay vì dùng set mất thứ tự.
  - Gán `frame_index` ở `chunk_processor` phải thực hiện SAU khi đã lọc các frame hỏng để khớp chính xác 1-1 với video ghi ra.
  - Video chỉ ghi `.mp4` để đồng bộ với cơ chế recovery quét file `.mp4`.
- **Regression / testing:**
  - Unit test `test_chunk_text_store.py` (kiểm thử 3 tầng fallback, reset cache, iter_all, put_many).
  - Integration test `test_store_precomputed.py` và `test_late_chunk_ingest.py` (verify index.json không còn text khi có video, sqlite có text, query/BM25 hoạt động tốt).

## Job chạy nền dài (mindmap): KHÔNG đặt hard-timeout FE dựa trên thời lượng trung bình

- **Root cause:** `SidebarRight.jsx::startPolling` (bản round-1) tự đặt `maxElapsedMs =
  jobTimeout(180s) + 10s` rồi chủ động bắn `onError` khi vượt — một con số ĐOÁN theo thời lượng
  TRUNG BÌNH của pipeline lúc đo (enrich 3 nhánh ≈86s), không phải giới hạn thật của hệ thống. Tài
  liệu lớn hơn/nhiều nhánh hơn thì thời gian sinh tăng tuyến tính và vượt mốc đoán đó dễ dàng, dù BE
  vẫn đang chạy đúng và sẽ xong. Kết quả: FE tự báo lỗi "quá thời gian chờ" giữa chừng, job BE vẫn
  hoàn tất và lưu record, user phải F5 rồi mở lại từ danh sách mới thấy — tưởng nhầm là lỗi thật.
  Chi tiết triệu chứng/fix xem `.playbook/known-issues.md` (mục đã resolved 2026-07-04).
- **Prevention:**
  1. KHÔNG gắn hard-timeout ở tầng client cho bất kỳ job nào có thời lượng chạy PHỤ THUỘC vào kích
     thước dữ liệu đầu vào (mindmap, ingest lớn, mọi job tương lai tương tự) — thời lượng "đo được"
     hôm nay không phải giới hạn trên thật.
  2. Nếu cần phát hiện "job có vẻ kẹt" để cảnh báo UI, dùng **stall-detection theo fingerprint tiến
     độ** (progress/current_node/kích thước partial-result không đổi trong N phút) thay vì đếm tổng
     thời gian trôi qua — cảnh báo là ĐỦ, đừng tự ý huỷ/báo lỗi thay người dùng.
  3. Mọi job chạy nền đủ dài để user có thể rời trang (F5, đóng tab, chuyển tab) nên lưu định danh
     job (`job_id` + ngữ cảnh tối thiểu) vào `localStorage` NGAY khi nhận được, để lần mount sau có
     thể resume polling thay vì bắt user tưởng đã mất tiến trình. Poller không tự guard double-start
     — caller phải dừng instance cũ trước khi gán instance mới vào ref khi user bấm tạo/tạo lại liên
     tiếp, nếu không sẽ rò rỉ vòng lặp polling.

## mind-elixir (và mọi editor bên thứ ba khác): đừng tin nó bảo toàn field lạ — giữ provenance ở sidecar

- **Root cause:** Adapter 2 chiều record↔mind-elixir cần giữ field nghiệp vụ (`note`, `chunk_refs`,
  `kind`) qua các thao tác kéo/xoá/gõ/thêm node của thư viện. `mind-elixir` không có hợp đồng nào
  cam kết bảo toàn field ngoài shape riêng của nó (`id`, `topic`, `children`, ...) — `getData()` chỉ
  trả về đúng những gì thư viện tự quản lý. Nếu adapter đọc field nghiệp vụ trực tiếp từ dữ liệu
  mind-elixir trả về, một vòng edit bất kỳ có thể âm thầm làm rớt `chunk_refs`/`note` của node đó.
  Một lỗi liên quan đã bị **reviewer bắt trong quá trình implement** (không phải test tự động): node
  mồ côi (parent trỏ tới id không còn tồn tại) hoặc root thừa (nhiều node cùng tự nhận `kind: "root"`)
  bị cây `toTree()` bỏ rơi hoàn toàn — một vòng load→save sẽ xoá câm lặng cả nhánh con của node đó.
- **Prevention:**
  1. Giữ field nghiệp vụ trong **sidecar map riêng** ở tầng gọi (không phải trong instance của thư
     viện), key theo `id` node — `FE/src/utils/mindElixirAdapter.js` dùng `Map<id, {note, chunkRefs,
     kind}>`, sống trong `useRef` ở component, merge lại theo `id` khi save
     (`mindElixirToRecord(mindData, sidecar, baseRecord)`).
  2. Khi load dữ liệu vào editor bên thứ ba: RÀ SOÁT và **rescue** mọi node mồ côi/root-thừa trước
     khi build cây cho nó — gắn lại dưới root (giữ nguyên nhánh con) thay vì loại bỏ. Không giả định
     dữ liệu đầu vào luôn "sạch" (một cây đúng nghĩa, đúng 1 root).
  3. Node mới do user tạo trong editor sẽ không có trong sidecar — adapter phải có default hợp lý
     (`chunk_refs: []`, `kind` suy theo độ sâu trong cây) thay vì crash hoặc để `undefined` rò vào
     record đã lưu.
  4. Test round-trip PURE (không import thư viện thật) phải cover: giữ nguyên note/chunk_refs/kind
     của node sống qua vòng record→adapter→record; node mới → default đúng; node xoá không rò lại;
     node mồ côi/root-thừa được rescue chứ không mất tích.
