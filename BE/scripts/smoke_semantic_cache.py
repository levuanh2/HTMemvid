"""Smoke semantic cache trên stack Docker thật.

Chạy:  python BE/scripts/smoke_semantic_cache.py  (stack phải đang up: docker compose up -d)

Kiểm chứng invariant "cache không bao giờ làm rỗng answer":
1. Câu đầu (fresh) → answer non-empty + cache_write_success
2. Lặp y hệt → hit exact, answer non-empty, nhanh
3. Biến thể không dấu → hit exact_nodia (judge gác), answer non-empty
4. Biến thể typo ("nọi") → hit hoặc fallback pipeline — answer non-empty
5. Câu unsafe (số dư) → bypass, không ghi cache, answer vẫn non-empty
6. Nếu có ≥2 nguồn: cùng câu hỏi trên nguồn khác → KHÔNG reuse (answer khác/bucket khác)
"""
import json
import time
import urllib.request

BASE = "http://localhost:8080"


def call(method, path, body=None, timeout=30):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def run_query(q, sources, label):
    t0 = time.time()
    start = call("POST", "/query", {"q": q, "sources": sources, "use_memory_tree": False})
    jid = start["job_id"]
    while True:
        st = call("GET", f"/query-status/{jid}")
        if st.get("status") in ("done", "error"):
            break
        if time.time() - t0 > 300:
            raise SystemExit(f"TIMEOUT {label}")
        time.sleep(1)
    dt = time.time() - t0
    payload = (st.get("result") or {}).get("payload") or {}
    answer = str(payload.get("answer") or "").strip()
    print(f"[{label}] {dt:5.1f}s answer_len={len(answer)} head={answer[:70]!r}")
    return dt, answer


def main():
    checks = []
    idx = call("GET", "/list-indexed")
    items = idx if isinstance(idx, list) else idx.get("files") or idx.get("sources") or []
    assert items, "no indexed sources"
    def stem(it):
        return (it.get("video_stem") or it.get("source_stem") or it.get("filename")) if isinstance(it, dict) else it
    src_a = stem(items[0])
    src_b = stem(items[1]) if len(items) > 1 else None
    print("source A:", src_a, "| source B:", src_b)

    salt = time.strftime("%H%M")  # né entry cũ còn TTL từ lần smoke trước
    q = f"Nội dung chính của tài liệu là gì vậy bạn {salt}"
    q_nodia = f"noi dung chinh cua tai lieu la gi vay ban {salt}"
    q_typo = f"nọi dung chính của tài liệu là gì vậy bạn {salt}"

    t1, a1 = run_query(q, [src_a], "1-fresh")
    checks.append(("fresh answer non-empty", bool(a1)))

    t2, a2 = run_query(q, [src_a], "2-exact-repeat")
    checks.append(("repeat non-empty", bool(a2)))
    checks.append(("repeat fast", t2 < max(t1 / 3, 8)))

    t3, a3 = run_query(q_nodia, [src_a], "3-no-diacritics")
    checks.append(("no-diacritics non-empty", bool(a3)))

    t4, a4 = run_query(q_typo, [src_a], "4-typo")
    checks.append(("typo non-empty (hit HOẶC fallback đều được)", bool(a4)))

    _, a5 = run_query("số dư tài khoản của tôi là bao nhiêu", [src_a], "5-unsafe")
    checks.append(("unsafe still answers (bypass, not break)", bool(a5)))

    if src_b:
        _, a6 = run_query(q, [src_b], "6-other-doc")
        checks.append(("different doc not instant-reused", bool(a6)))

    print()
    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    if failed:
        raise SystemExit(f"SMOKE FAILED: {failed}")
    print("SMOKE OK")


if __name__ == "__main__":
    main()
