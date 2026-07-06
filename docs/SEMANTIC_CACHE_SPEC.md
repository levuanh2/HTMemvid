# SPEC: 3-Tier LLM Cache (Redis) — Semantic Response Cache + Retrieval Cache + Prompt-Prefix Ordering

> Trạng thái: **spec đã duyệt, đang implement**. Nếu phiên làm việc trước dừng giữa chừng,
> đọc file này + checklist cuối để tiếp tục. Plan gốc (đã approve):
> `C:\Users\Vu Anh\.claude\plans\use-skills-suitable-for-twinkling-melody.md`.

## Mục tiêu

Giảm latency + cost LLM cho câu hỏi lặp lại / tương tự ngữ nghĩa. Cache **ngắn hạn 1–2 ngày**,
production-safe: chống cache poisoning, fail-open (Redis chết → app chạy bình thường),
không cache dữ liệu nhạy cảm. KHÔNG làm long-term memory.

Quyết định đã chốt với user:
- Store = **Redis** (thêm service docker-compose + dep `redis>=5,<6`). Dev Windows không có
  Redis → mọi tầng cache tự tắt im lặng (log WARNING đúng 1 lần).
- Tier 3 = **retrieval result cache** (app không có tool-calling — LLM chỉ generate text).

## Hiện trạng liên quan (đã explore, line number tại thời điểm viết)

- Flask + LangGraph 0.2.x. KHÔNG có Redis trước đây; persistence = SQLite + FAISS + OrderedDict LRU.
- Exact-match answer cache đã có: `_query_cache` + `_make_query_cache_key`/`_get_cached_query`/
  `_set_cached_query` (`BE/app/main.py:200-236`), inject vào graph (`main.py:~602`), dùng ở
  `cache_lookup_node` (`BE/app/graphs/query_graph.py:138-165` — đã bypass multi-turn history +
  processing_message), ghi ở `finalize_node` (~:613, chỉ khi có answer thật).
- Cache-hit short-circuit `done=True` trước GenerateAnswer → không stream token; FE đã xử lý
  đúng hành vi này hôm nay (semantic hit sẽ y hệt).
- Embed query: `encode_query_cached(q)` (`BE/app/clients/llm_factory.py:348`) — bge-m3 mean-pool,
  trả `None` khi `SKIP_MODEL_LOAD=1`. KHÔNG assume vector đã normalize → tính cosine đầy đủ.
- `META_PATH` = `index/index.json` (`BE/app/domains/vectorstore/store.py:51`); `_save_meta`
  atomic tmp→replace nên **mtime đổi mỗi lần ingest/delete** → dùng làm index_version.
  KHÔNG đọc nội dung index.json (nặng) — chỉ `os.stat`.
- Playbook: KHÔNG bump langgraph/langchain/pydantic. Sau mọi thay đổi dep:
  `python -c "import app.graphs.query_graph"`. Test graph phải có bản build graph THẬT
  (`BE/tests/_qg_build.py`).

## Kiến trúc 3 tầng

### Tier 1 — Prompt/Prefix ordering (comment-only, không đổi hành vi)
- Default path `qa_chain.py:_qa_messages`: System(instruction + Context) → history → question
  — **đã static-first, tối ưu cho provider KV/prefix cache** (Ollama KV reuse, Gemini implicit
  caching). Chỉ thêm comment giải thích + note "đổi prompt này phải bump `llm_cache.PROMPT_VERSION`".
- Legacy `summarize_results` (llm_factory.py:535): KHÔNG reorder (system prompt đã đứng đầu;
  reorder trong user_msg không thêm prefix-benefit, rủi ro drift). Comment only.

### Tier 2 — Semantic Response Cache (Redis)

**Điểm cắm (giữ diff nhỏ):** hoàn toàn trong `main.py` — `_get_cached_query` (sau miss LRU local)
gọi `llm_cache.semantic_lookup(cache_key)`; `_set_cached_query` (sau write local) gọi
`llm_cache.semantic_store(cache_key, value)`. **query_graph không đổi cho Tier 2** — thừa hưởng
mọi guard sẵn có. LRU local giữ nguyên làm L1 per-worker; Redis là L2 cross-worker.

**Module mới:**
- `BE/app/clients/redis_client.py`: `get_redis() -> Optional[Redis]`, `mark_unavailable()`,
  `reset_for_tests()`. `REDIS_URL` rỗng hoặc `CACHE_ENABLED=0` → None ngay. Lần đầu:
  `redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5, decode_responses=True)`
  + ping; fail → WARNING 1 lần + `_unavailable_until = now + 60` (retry window 60s).
  Không bao giờ raise.
- `BE/app/domains/cache/llm_cache.py` (~250 dòng): toàn bộ logic dưới đây.

**Key layout Redis** (plain `redis:7-alpine`, không RediSearch):
```
Bucket B = sha256(NS|ENV|PROMPT_VERSION|EMBEDDING_MODEL_NAME|LATE_CHUNKING|
                  index_version()|sorted_sources|language|category|use_memory_tree)[:16]

{NS}:{ENV}:sc:{B}:e:{eid}   = JSON entry, SETEX TTL 48h
                              eid = sha256(q_norm)[:16], q_norm = q.strip().lower()
{NS}:{ENV}:sc:{B}:ids       = SET các eid, EXPIRE refresh mỗi write
{NS}:{ENV}:ret:{key24}      = retrieval cache (Tier 3)
```
- Bucket encode MỌI điều kiện match → **chống poisoning theo cấu trúc**: khác sources/lang/
  category/prompt_version/index_version = khác bucket = không bao giờ nhìn thấy nhau.
- Ingest/delete tự invalidate: index_version (mtime index.json) đổi → bucket mới; bucket cũ
  chết theo TTL + `allkeys-lru`.
- Exact repeat = 1 GET O(1) (eid theo q_norm) **trước khi cần embed** → exact cache cross-worker free.

**Entry JSON:** `{q, q_norm, payload, status, created_at, expires_at, model, prompt_version,
index_version, risk_class, vec_b64, dim}` — payload là đúng shape finalize
(`{"payload": ..., "status": ...}` — gồm answer + sources/chunks evidence).

**semantic_lookup(cache_key):** parse JSON cache_key (sinh bởi `_make_query_cache_key` — comment
chéo 2 phía; đổi format bên kia = miss im lặng, fail-safe) → (1) GET exact eid → `hits_exact`;
(2) `encode_query_cached(q)`; None → `bypass_no_embedding`; (3) SMEMBERS (cap 200 id), MGET,
decode vec_b64 float32, **cosine đầy đủ (chia norm)**, best ≥ threshold → `hits_semantic`.
MGET nil (expired) → SREM dọn. Log mọi lookup kèm best sim; sim trong (threshold−0.03, threshold)
→ log INFO "near-threshold" để tune.

**semantic_store(cache_key, value):** chỉ khi `classify_risk(q)` cho phép; SETEX entry + SADD +
EXPIRE ids. `writes` +1, denied → `bypass_risk` +1.

**classify_risk(question) -> (cacheable: bool, risk_class: str):** 1 compiled regex VI+EN:
- personal: `tài khoản|mật khẩu|số dư|của tôi|của em|của mình|my account|password|balance|my grade|api key|secret|token`
- realtime: `hôm nay|bây giờ|hiện tại|lúc này|mới nhất|today|now|current|latest|weather|thời tiết|giá\b|price`
- Match → `(False, "personal"/"realtime")`. Không match → `(True, "low")`.
- Allowlist tinh thần: chỉ cache low-risk deterministic (FAQ/giải thích tài liệu công khai đã ingest).
  Multi-turn đã bị chặn từ tầng graph (không bao giờ tới đây).

**Threshold floor:** `SEMANTIC_CACHE_THRESHOLD < 0.80` → clamp 0.80 + `logger.warning` to, trừ khi
`SEMANTIC_CACHE_THRESHOLD_FLOOR_OVERRIDE=1`.

**Metrics:** `METRICS = collections.Counter` — hits_exact, hits_semantic, misses, bypass_risk,
bypass_no_embedding, bypass_history, writes, errors, saved_llm_calls, latency_saved_ms (ước lượng
thô: đếm hit × LLM_AVG_MS ~ 20000 hoặc đo lúc store), ret_hits, ret_misses. `stats()` = dict(METRICS)
+ echo config. Expose: thêm key `"cache"` vào response `GET /stats` có sẵn (main.py:~256-290).
Caveat: gunicorn 2 workers → counter per-process; aggregate thật xem `redis-cli INFO stats`.

**Fail-open:** mọi op Redis bọc `try/except Exception: mark_unavailable(); METRICS["errors"]+=1;
return None`. Không bao giờ raise vào đường trả lời.

**invalidate_all():** SCAN `{NS}:{ENV}:*` + DEL batch. Gọi best-effort sau
`delete_source_from_index` trong main.py (~:1635; check thêm path `DELETE /sources/<id>` ~:2053)
— belt-and-suspenders, index_version đã tự orphan.

### Tier 3 — Retrieval cache
- Wrap quanh `_do_hybrid_retrieve` trong `retrieve_faiss_node` (query_graph.py:~212-228) — cover
  cả 2 nhánh USE_LC_ENSEMBLE + parallel history path; đúng cả khi CRAG rewrite `state["q"]`
  (key theo `state["q"]` tại thời điểm gọi).
- Key: `{NS}:{ENV}:ret:{sha256(q_norm|sorted_sources|top_k|index_version|emb_model)[:24]}`,
  SETEX `RETRIEVAL_CACHE_TTL_SECONDS` (3600).
- Value: `[dataclasses.asdict(c) for c in chunks]` — `RetrievedChunk`
  (`BE/app/domains/retrieval/hybrid.py:20-28`, frozen dataclass primitive) round-trip JSON sạch;
  rebuild `RetrievedChunk(**d)` (lazy import tránh cycle).

## Env vars (BE/.env.example, section `# ===== SESSION & CACHE =====`)

| Var | Default | Ý nghĩa |
|---|---|---|
| CACHE_ENABLED | 1 | master switch mọi tầng Redis |
| REDIS_URL | (rỗng) | rỗng ⇒ Redis tiers off im lặng (dev Windows) |
| SEMANTIC_CACHE_ENABLED | 1 | Tier 2 |
| RETRIEVAL_CACHE_ENABLED | 1 | Tier 3 |
| SEMANTIC_CACHE_TTL_SECONDS | 172800 | 48h |
| SEMANTIC_CACHE_THRESHOLD | 0.85 | cosine hit threshold |
| SEMANTIC_CACHE_THRESHOLD_FLOOR_OVERRIDE | 0 | cho phép <0.80 (warning to) |
| RETRIEVAL_CACHE_TTL_SECONDS | 3600 | 1h |
| CACHE_NAMESPACE | memvid | prefix key |
| CACHE_ENV | dev | prefix key (compose set prod) |

Config đọc bằng module-level `os.getenv` trong 2 module mới (pattern inline của repo,
KHÔNG đụng `shared/config.py` Settings).

## docker-compose

```yaml
redis:
  image: redis:7-alpine
  command: ["redis-server","--maxmemory","256mb","--maxmemory-policy","allkeys-lru","--appendonly","no","--save",""]
  expose: ["6379"]
  healthcheck: { test: ["CMD","redis-cli","ping"], interval: 10s, timeout: 3s, retries: 10 }
  restart: always
```
Backend thêm: `REDIS_URL: redis://redis:6379/0`, `CACHE_ENV: prod`, `depends_on: redis`.

## Tuning TTL & threshold

- **TTL 48h** (mặc định) cho answer từ tài liệu đã ingest — tĩnh trong đời index. Muốn ngắn hơn
  cho dữ liệu hay re-upload: hạ `SEMANTIC_CACHE_TTL_SECONDS` (re-ingest vốn đã tự invalidate
  qua index_version, TTL chỉ là lưới đáy).
- **Threshold 0.85**: nâng lên 0.88–0.92 nếu thấy false-hit (2 câu hỏi khác intent trúng nhau);
  hạ về tối thiểu 0.80 nếu hit-rate quá thấp và log "near-threshold" cho thấy nhiều cặp paraphrase
  thật bị trượt. ĐỪNG hạ dưới 0.80 chỉ để tăng hit rate — đó là công thức cache poisoning.
- Theo dõi: `GET /stats` key `cache` — nếu `hits_semantic` tăng kèm phàn nàn answer sai → nâng
  threshold + `invalidate_all()`.

## Safety tradeoffs / false-hit đã biết

- Cosine bge-m3 với 2 câu cùng từ vựng khác intent ("X là gì" vs "X KHÔNG phải là gì") có thể
  ≥0.85 → false hit. Giảm nhẹ bằng: bucket hẹp (cùng sources/lang/category), temp 0 (answer
  deterministic), threshold có floor, log sim để audit. Không loại bỏ được 100% — chấp nhận với
  use-case QA tài liệu tĩnh.
- Multi-turn không bao giờ cache (guard tầng graph). Câu hỏi nhạy cảm/realtime bị regex chặn ghi.
- Payload cache chỉ chứa answer + evidence từ tài liệu đã ingest (dữ liệu người dùng tự upload,
  không secret hệ thống).

## Test list (BE/tests/test_llm_cache.py — FakeRedis dict+TTL + RaisingRedis in-file, KHÔNG dep fakeredis)

1. exact repeat hit; 2. semantic hit sim~0.9 (monkeypatch encode); 3. sim thấp miss; 4. khác
sources → bucket khác miss; 5. index_version đổi → miss; 6. history bypass (real graph node qua
`_qg_build`); 7. câu nhạy cảm không store (`bypass_risk`); 8. expired → miss + SREM; 9. RaisingRedis
→ fail-open không exception, lần 2 skip (unavailable window); 10. threshold 0.5 → clamp 0.80 +
warning / có override giữ nguyên; 11. embedding None → bypass; 12. retrieval round-trip
RetrievedChunk; 13. retrieval invalidation theo index_version; 14. metrics + `/stats` có key "cache".

## Verification

- `python -m pytest BE/tests/test_llm_cache.py -v` (global python — playbook; 5 file graph-test
  đã drift sẵn, ignore nếu đụng: test_crag/hitl/nli/rerank/supervisor_graph).
- `python -c "import app.graphs.query_graph"` sau khi cài `redis`.
- Regression: `python -m pytest BE/tests/test_query.py BE/tests/test_upload_query_e2e.py`.
- Smoke thật: `docker compose up redis backend` → hỏi 1 câu 2 lần (lần 2 tức thì), câu paraphrase
  (hit semantic), `GET /stats`.

## Checklist implement (đánh dấu khi xong — phiên sau đọc từ đây)

- [x] 1. Infra: `BE/app/clients/redis_client.py` + `redis>=5,<6` vào requirements.txt +
      docker-compose redis service + `.env.example` vars
- [x] 2. Core: `BE/app/domains/cache/llm_cache.py` (classify_risk, index_version, bucket/entry,
      semantic_lookup/store, retrieval_get/put, invalidate_all, METRICS, floor clamp)
- [x] 3. Wire Tier 2: hooks `_get_cached_query`/`_set_cached_query` main.py + `/stats` key cache
      + comment chéo `_make_query_cache_key`
- [x] 4. Tier 3: wrap `_do_hybrid_retrieve` query_graph.py + bypass_history counter
- [x] 5. Invalidation hook sau delete-source + Tier 1 comments qa_chain.py
- [x] 6. Tests `BE/tests/test_llm_cache.py` (14 case) — 14/14 xanh
- [x] 7. Verify: `import app.graphs.query_graph` OK; full suite 215 passed / 1 skipped
      (đã ignore 5 file graph-test drift theo playbook); `.playbook/lessons-learned.md` đã có
      mục "Cache 3 tầng (Redis)"

### Còn lại (smoke thủ công, cần Docker/Redis thật)
- [ ] `docker compose up redis backend` → hỏi 1 câu 2 lần (lần 2 tức thì), câu paraphrase
      (semantic hit), `GET /stats` xem counters
- [ ] Dev không Redis: xác nhận app chạy bình thường, không log lỗi lặp
