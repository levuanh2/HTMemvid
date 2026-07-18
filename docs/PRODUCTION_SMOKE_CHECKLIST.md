# Production/Staging Docker Smoke Checklist

Chạy sau mỗi lần rebuild stack production/staging. Mọi lệnh PowerShell, chạy từ repo root.

**LUÔN pin project name** — stack thật của UI là `memvid_auth_smoke`; `docker compose` trần
build/chạy project `memvid_new` KHÁC và đụng port 8080 (xem `.playbook/known-issues.md`,
mục cancel-flow stale-deploy):

```powershell
$env:COMPOSE_PROJECT_NAME = "memvid_auth_smoke"
```

## 1. Build + recreate

```powershell
docker compose --profile worker up -d --build backend llm-gateway mindmap-service frontend rq-worker
docker ps --filter "name=memvid_auth_smoke" --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"
```

- [ ] Mọi container `Up`; cột `RunningFor` chứng minh image MỚI (không phải build cũ vài ngày trước).
- [ ] Deploy-freshness check (bài học stale-deploy): grep một chuỗi đặc trưng của commit vừa deploy
      bên TRONG container, ví dụ:
      `docker exec memvid_auth_smoke-backend-1 grep -c "jobs_maintenance" app/main.py`

## 2. Health

```powershell
curl.exe -fsS http://localhost:8080/health          # backend
curl.exe -fsS -o NUL -w "%{http_code}" http://localhost:3000/   # frontend (200)
docker exec memvid_auth_smoke-redis-1 redis-cli ping           # PONG
docker inspect --format "{{.State.Health.Status}}" memvid_auth_smoke-llm-gateway-1     # healthy (PR#5 TCP check)
docker inspect --format "{{.State.Health.Status}}" memvid_auth_smoke-mindmap-service-1 # healthy
```

- [ ] backend `/health` OK, frontend 200, redis PONG, 2 gRPC service `healthy`.

## 3. Warmup (PR#5)

```powershell
docker logs memvid_auth_smoke-backend-1 2>&1 | Select-String "\[warmup\]"
```

- [ ] Log liệt kê ĐÚNG các model đã cấu hình (chat/summary/mindmap từ env, KHÔNG phải `qwen3.5:9b` stale).
- [ ] Không có warmup failure bất thường (Ollama down thì fail-open, app vẫn chạy).

## 4. FE delivery headers (PR#3)

```powershell
# Asset có hash → immutable 1 năm
$asset = (curl.exe -fsS http://localhost:3000/ | Select-String -Pattern "assets/[^\"]+\.js" | ForEach-Object { $_.Matches[0].Value } | Select-Object -First 1)
curl.exe -fsSI "http://localhost:3000/$asset" | Select-String "Cache-Control|Content-Encoding"
# index.html → no-cache
curl.exe -fsSI http://localhost:3000/index.html | Select-String "Cache-Control"
```

- [ ] Asset: `Cache-Control: public, max-age=31536000, immutable` (+ gzip khi có `Accept-Encoding`).
- [ ] `index.html`: `Cache-Control: no-cache`.

## 5. Core flows (UI tại http://localhost:3000)

- [ ] Đăng nhập/đăng ký nếu `AUTH_PROTECT_APP_APIS=true`; API không token trả 401.
- [ ] Upload một tài liệu nhỏ (PDF/docx) → status chuyển `index_ready`.
- [ ] Query tài liệu đó → answer có citation; SSE stream mượt (preview throttle PR#3).
- [ ] Query LẶP LẠI cùng câu → cache hit (nhanh, `/stats` cache counters tăng).
- [ ] Tạo tóm tắt → job chạy nền, chip tiến độ, kết quả mở được.
- [ ] Tạo sơ đồ → tương tự; sửa 1 node rồi bấm "Tạo lại" → PHẢI hiện confirm chưa-lưu (PR#8).
- [ ] Huỷ một job đang chạy → chip thoát "Đang huỷ…" trong ~10s, notice "Đã huỷ…".
- [ ] Job lỗi (tắt Ollama tạm để ép) → banner "Thử lại" hiện; bấm Thử lại chạy job mới (PR#8).
- [ ] Timeline: `curl.exe -fsS -H "Authorization: Bearer <token>" http://localhost:8080/jobs/<job_id>/timeline`
      → events + totals (PR#1).
- [ ] Xoá source → source biến mất; query lại KHÔNG trả chunk của source đã xoá.

## 6. LLM lane smoke (PR#4 — chỉ khi bật thử)

```powershell
$env:MAX_CONCURRENT_LLM_CALLS = "2"
$env:LLM_PRIORITY_LANES_ENABLED = "true"
$env:LLM_RESERVED_QUERY_SLOTS = "1"
docker compose up -d llm-gateway
```

- [ ] Chạy 1 summary/mindmap (batch) + 1 query đồng thời → query KHÔNG bị chặn sau 2 batch call.
- [ ] `docker logs memvid_auth_smoke-llm-gateway-1 | Select-String "lane="` → thấy `lane=batch`/`lane=query`, `waited_ms`.
- [ ] Xong thì trả env về mặc định và `docker compose up -d llm-gateway` lại.

## 7. Retention/sweep spot-check (PR#2)

```powershell
docker logs memvid_auth_smoke-backend-1 2>&1 | Select-String "jobs_maintenance"
```

- [ ] Có log `jobs_maintenance swept=... pruned=... logs=...` sau startup (khi có gì để dọn).

## Notes

- KHÔNG `docker compose down -v` — volume `hf_cache` giữ bge-m3 (~2.3GB, PR#5) và `data/`
  là dữ liệu user. Muốn xoá volume phải nói rõ và backup trước.
- Baseline test BE: 40 fail auth-env 401 là pre-existing — không phải regression.
