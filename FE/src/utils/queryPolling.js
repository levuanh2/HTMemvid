import { _appError } from "./api";

const _sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Phase F.1 — decide whether to fall back to status polling after `streamQueryJob`
// rejected. Only a lost SSE *connection* warrants polling: raw EventSource cannot send
// an Authorization header, so under AUTH_PROTECT_APP_APIS the stream 401s and errors
// out even though the job is running fine. A genuine job error, a timeout, or a user
// cancel must propagate unchanged (polling would just repeat them).
export function shouldPollFallback(err, { cancelled = false } = {}) {
  if (cancelled) return false;
  return Boolean(err && err.sseConnectionLost);
}

// Poll `/query-status/<jobId>` via the injected `apiFetch` (which attaches the Bearer
// token — the whole reason for the fallback) until the job reaches a terminal state.
//
// Returns the job result object ({ payload, status }) on success. Throws:
//   * an Error carrying `.status` on 401/403/404 (401 already dispatched
//     `auth:unauthorized` inside apiFetch → Phase F sign-out/redirect; 403/404 map to a
//     safe message via getUserFriendlyApiError),
//   * an Error with the job's message on a real job error,
//   * Error("CANCELLED") when cancelled or the job is cancelled/interrupted,
//   * a timeout Error after maxAttempts.
//
// Never puts a token in any URL. `sleep` is injectable so tests run without real timers.
export async function pollQueryStatus(jobId, {
  apiFetch,
  intervalMs = 1200,
  maxAttempts = 500,        // ~10 min at 1.2s — matches the SSE timeout ceiling
  isCancelled = () => false,
  onStatus = null,
  sleep = _sleep,
} = {}) {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    if (isCancelled()) throw new Error("CANCELLED");
    const res = await apiFetch(`/query-status/${encodeURIComponent(jobId)}`);
    if (!res.ok) throw await _appError(res);   // 401/403/404 → Phase F handling
    const data = await res.json().catch(() => ({}));
    if (onStatus) { try { onStatus(data); } catch { /* progress UI is best-effort */ } }
    const st = data?.status;
    if (st === "done") return data.result;
    if (st === "error") {
      throw new Error(String(data?.error || "").trim() || "Loi khi xu ly truy van. Vui long thu lai.");
    }
    if (st === "cancelled" || st === "interrupted") throw new Error("CANCELLED");
    if (isCancelled()) throw new Error("CANCELLED");
    await sleep(intervalMs);
  }
  throw new Error("Quá thời gian chờ phản hồi. Vui lòng thử lại.");
}
