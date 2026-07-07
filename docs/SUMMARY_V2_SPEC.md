# SPEC: Summary v2 — Tóm tắt văn bản section-first, async job

> Trạng thái: **spec đã duyệt, đang implement**. Nếu phiên trước dừng giữa chừng, đọc file này
> + checklist trong `00_Active_Plans/MemVid - Summary v2 Plan.md` để tiếp tục. Plan gốc (đã
> approve): `C:\Users\Vu Anh\.claude\plans\read-and-explore-summary-l-n-structured-dawn.md`.

## Mục tiêu

Thay hẳn chức năng tóm tắt cũ (`advanced_summarize` — sync, 6 technique, không cache, không
citation, lưu JSON) bằng pipeline **section-first mirror mindmap**: cấu trúc mục lục
deterministic (0 LLM) làm khung, LLM chỉ ở 2 chỗ hẹp (tóm tắt từng section song song +
1 call synthesize), job async + poller, sqlite store + cache theo content_hash, citation
chunk_refs bấm được mở EvidenceDrawer.

Quyết định đã chốt với user:
- Kiến trúc section-first (tái dùng `services/mindmap/pipeline/skeleton.py` + `outline.py`).
- **Xóa hẳn** pipeline cũ (FROST/CoD/DANCER/extract/fact-check) + endpoint sync.
- Citation có — contract evidence sẵn có (EvidenceDrawer, `/chunk-text/<id>`).
- UX: progress chip, resume sau F5, chọn độ dài Ngắn/Vừa/Chi tiết.
- Summary chạy trong monolith (import plain-Python pipeline mindmap như `mindmap_factory.py`),
  KHÔNG tách gRPC.

## Kiến trúc

```
POST /generate-summary {sources, length_mode, force}
  → content_hash = sha256(PIPELINE_VERSION | stems | chunk texts | headings | length_mode)
  → cache hit (summaries.sqlite) → 200 {status:"done", result, cached:true}   (KHÔNG job_id)
  → miss → jobs_store.create_job(job_type="summary") → daemon thread → 202 {job_id}

SUMMARY_GRAPH (LangGraph, checkpoint sqlite):
  CollectInput → Sections → SummarizeSections → Synthesize → AssemblePersist
       (+ Cancelled / ErrorHandler, _guard cancel-check mọi node)

  Sections          : build_skeleton (heading_path) → fallback build_outline (1 LLM)
                      → flatten section top-level + descendant chunk_refs. 0-1 LLM call.
  SummarizeSections : mỗi section 1 LLM call (song song SUMMARY_PARALLEL, budget
                      SUMMARY_LLM_TIMEOUT_SEC, retry-once-malformed-JSON, cancel giữa batch)
                      → {summary, key_points, chunk_keys} — chunk_keys LỌC theo id thật.
  Synthesize        : 1 LLM call trên các section summary → {title, overview, entities}.
  AssemblePersist   : build_record → store.save_record → MỘT update_job(status="done",
                      progress=100, result=record)  ← done ATOMIC với result.

GET  /summary-status/<job_id>   → {status, progress, current_node, result, error}
POST /summary-cancel/<job_id>   → request_cancel
GET  /summaries                 → {summaries: [...]} (sqlite)
DELETE /summaries/<id>
```

## Record schema (schema_version 2)

```json
{
  "id": "<uuid>", "schema_version": 2,
  "title": "…", "sources": ["a_docx"],
  "content_hash": "<sha256>", "created_at": "…Z",
  "length_mode": "medium",
  "overview": "markdown tổng quan",
  "sections": [
    {"id": "s1", "title": "1. Mở đầu", "summary": "markdown",
     "key_points": ["…"], "chunk_refs": ["0","3"], "order": 0}
  ],
  "entities": ["…"],
  "generator": {"pipeline": "summary_sections_v1", "model": "qwen2.5:14b",
                "elapsed_sec": 42.1, "degraded": false, "missing": [],
                "skeleton_method": "headings"}
}
```

- `missing` entries: `"section:<title>"` (section lỗi LLM, giữ skeleton), `"synthesize"`,
  `"skeleton"`. Degraded phải TRUNG THỰC (bài học mindmap).
- Legacy record migrate từ `summaries.json`: `schema_version: 1`, body cũ giữ ở `summary_md`,
  `content_hash: ""` — FE render fallback markdown.
- `length_mode` ∈ `("short", "medium", "detailed")` — FE hiện Ngắn/Vừa/Chi tiết. Mode nằm
  TRONG content_hash (đổi mode = job mới, không đè cache mode khác).

## Module layout

| Module | Vai trò |
|---|---|
| `BE/services/summary/pipeline/schema.py` | PIPELINE_VERSION, LENGTH_MODES, content_hash, sanitize_sections, build_record |
| `BE/services/summary/pipeline/sections.py` | build_sections (skeleton → outline fallback → flatten) |
| `BE/services/summary/pipeline/summarize.py` | per-section LLM song song (clone shape enrich.py) |
| `BE/services/summary/pipeline/synthesize.py` | 1 call tổng hợp overview/entities |
| `BE/app/clients/summary_factory.py` | adapter local pipeline (mirror mindmap_factory) |
| `BE/app/graphs/summary_graph.py` + `state.py::SummaryState` | LangGraph 5 node |
| `BE/app/domains/summary/store.py` | summaries.sqlite (copy mindmap/store.py) + migrate_from_json |
| `FE/src/utils/jobPoller.js` | generalize createMindmapPoller (semantics giữ nguyên) |
| `FE/src/utils/activeJob.js` + `activeSummaryJob.js` | localStorage resume-F5 factory |
| `FE/src/utils/summaryJob.js` | stageLabel Việt + createSummaryPoller + normalizeSummaryRecord |
| `FE/src/components/Layout/SummaryModal.jsx` | v2: overview + sections + citation chips + EvidenceDrawer + degraded banner |
| `FE/src/components/ui/Markdown.jsx` | export MdProse (dedupe mdComponents) |

## Xóa (không giữ song song)

`BE/app/domains/summary/summarize_advanced.py`; endpoints `POST /summarize-file`,
`POST /summarize-documents`, `POST /summaries`; helpers `_load_summaries/_save_summaries/
_append_summary`; const `SLM_MODEL_SUMMARY` main.py (llm_factory giữ env map `qwen2.5:14b`);
`tests/test_summarize_modes.py`. **GIỮ**: `summarize_results` (query graph dùng),
`qa_chain.py` (RAG answer — misnomer, không đụng), `summarize_whole_document` bị xóa theo
endpoint `/summarize-file`.

## Env / config

```
SUMMARY_LLM_TIMEOUT_SEC=120   # compose: 240 (như MINDMAP_LLM_TIMEOUT_SEC, CPU chậm)
SUMMARY_PARALLEL=2
SLM_MODEL_SUMMARY=qwen2.5:14b # đã có, llm_factory feature="summary" resolve
```

## Ràng buộc playbook áp dụng (đọc .playbook trước khi sửa)

1. `status="done"` đi CÙNG result trong MỘT update_job (race 2026-07-06) — template
   `mindmap_graph.py::assemble_node`.
2. Đổi prompt/logic pipeline → bump `PIPELINE_VERSION` (tự vô hiệu cache).
3. Test build graph THẬT (conftest mock che lỗi StateGraph).
4. FE không hard-timeout; stall-detection fingerprint; lưu job_id localStorage ngay;
   stop poller cũ trước khi gán mới.
5. Cache-hit response KHÔNG có job_id — FE branch `status==="done" && result` TRƯỚC (aec6017).
6. `ask_ai(feature="summary")` temp 0; JSON repair string-aware; chunk_refs lọc id thật.
7. Text hiển thị qua Markdown component chung (MdProse), không `<p>{raw}</p>`.

## Verify

```bash
cd BE && python -m pytest tests/ -q          # global python
cd FE && npx vitest run && npm run build
```

Smoke thủ công: tạo tóm tắt → chip progress → modal sections + chips → EvidenceDrawer →
F5 resume → cache-hit tức thì → đổi mode → job mới → cancel không persist → legacy mở được.

## Skip có chủ đích

Partial preview trong job, "Hỏi về đoạn này" từ drawer, rename title, timeout theo mode,
fact-check — thêm khi được yêu cầu.
