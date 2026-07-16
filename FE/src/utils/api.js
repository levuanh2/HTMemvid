// Base URL: support both env names. `VITE_API_URL` is what this file has always
// read; `VITE_API_BASE` is what the Docker build sets — accept either so the FE
// reaches the backend in every environment. DEV falls back to localhost:8080.
const API = import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE;

export const apiUrl = (path) => {
  let base = String(API ?? "")
    .trim()
    .replace(/\/+$/, "")
    .replace(/\/api$/, "");
  if (!base && import.meta.env.DEV) {
    base = "http://localhost:8080";
  }
  const p = path.startsWith("/") ? path : `/${path}`;
  return base ? `${base}${p}` : p;
};

import { getToken, clearToken } from "../auth/tokenStore";

// Auth endpoints own their own 401 semantics (login = bad creds, /auth/me = probe),
// so a 401 from these must NOT trigger the global sign-out flow — otherwise the
// restore probe would redirect-loop. Everything else is a protected app API.
const AUTH_PATH_RE = /^\/auth\//;

// Broadcast a global "you are signed out" signal exactly once per 401 so the
// AuthProvider can clear the token/state and ProtectedRoute can redirect to
// /login?next=<current>. Guarded for non-browser (SSR/test-without-window) envs.
function _notifyUnauthorized(path) {
  try {
    if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
      window.dispatchEvent(new CustomEvent("auth:unauthorized", { detail: { path } }));
    }
  } catch {
    /* dispatch unavailable — the caller still sees the 401 response */
  }
}

// apiFetch — attaches `Authorization: Bearer <token>` when a token exists and the
// caller hasn't already set one. Only the Authorization header is added; body,
// method and any caller headers (incl. multipart Content-Type for uploads) are
// left untouched, so existing query/upload/summary/mindmap calls are unchanged.
// On a 401 from a protected app API it fires `auth:unauthorized` (not for /auth/*).
export const apiFetch = async (path, options = {}) => {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  const hasAuth = Object.keys(headers).some((k) => k.toLowerCase() === "authorization");
  if (token && !hasAuth) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(apiUrl(path), { ...options, headers });
  if (res.status === 401 && !AUTH_PATH_RE.test(String(path))) {
    _notifyUnauthorized(String(path));
  }
  return res;
};

// ── Error helpers — permission-safe, no existence oracle ────────────────────
// App API callers throw an Error with `.status` set (see `_appError`); these read
// that to decide UX without exposing raw backend detail to the user.
export const isUnauthorizedError = (e) => e?.status === 401;
export const isForbiddenError = (e) => e?.status === 403;
export const isNotFoundOrForbiddenError = (e) => e?.status === 403 || e?.status === 404;

export function getUserFriendlyApiError(e) {
  const s = e?.status;
  if (s === 401) return "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.";
  if (s === 403) return "Bạn không có quyền truy cập tài liệu này.";
  if (s === 404) return "Tài nguyên không tồn tại hoặc bạn không có quyền truy cập.";
  if (s === 0 || e?.name === "TypeError") return "Không kết nối được máy chủ.";
  return "Đã có lỗi xảy ra. Vui lòng thử lại.";
}

// Build an Error carrying the HTTP status/code for a failed app response, so the
// UI can branch on 401/403/404 without re-reading the body. The raw backend
// `error` string is kept on `.code` (console/debug), never shown verbatim as the
// primary message under permission failures.
export async function _appError(res) {
  const body = await res.json().catch(() => ({}));
  const err = new Error(body?.error || `HTTP ${res.status}`);
  err.status = res.status;
  err.code = body?.error || `http_${res.status}`;
  return err;
}

// ── Auth API helpers (Bearer token) ────────────────────
async function _authJson(res) {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body?.error || `HTTP ${res.status}`);
    err.code = body?.error || `http_${res.status}`;
    err.status = res.status;
    throw err;
  }
  return body;
}

export const registerUser = async ({ email, password, display_name }) => {
  const res = await apiFetch(`/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, display_name: display_name || undefined }),
  });
  return _authJson(res); // { token, user }
};

export const loginUser = async ({ email, password }) => {
  const res = await apiFetch(`/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return _authJson(res); // { token, user }
};

export const logoutUser = async () => {
  try {
    await apiFetch(`/auth/logout`, { method: "POST" });
  } catch {
    // Best-effort — the client-side token is cleared regardless.
  }
};

export const getCurrentUser = async () => {
  const res = await apiFetch(`/auth/me`);
  if (res.status === 401) {
    clearToken();
    return null;
  }
  const body = await _authJson(res);
  return body.user; // { id, email, display_name }
};

// ── Mindmap: generate / poll / cancel ──────────────────
// `generateMindmap` posts the same body shape `/generate-mindmap` has always
// expected ({ sources, q, force }); kept as a helper so callers don't repeat
// the JSON/header boilerplate. Response is passed through as-is — the caller
// decides how to branch on `status` ("done" on cache-hit vs "started").
export const generateMindmap = async (sources, { force = false } = {}) => {
  const res = await apiFetch(`/generate-mindmap`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sources, q: "tóm tắt tài liệu", force: Boolean(force) }),
  });
  if (!res.ok) throw await _appError(res);
  return res.json();
};

// `cancelMindmap` — real (server-side) cancel: flips a cooperative-abort flag
// the worker checks between graph nodes. Best-effort: caller should still stop
// its own polling immediately rather than waiting on this request.
export const cancelMindmap = async (jobId) => {
  const res = await apiFetch(`/mindmap-cancel/${encodeURIComponent(jobId)}`, { method: "POST" });
  if (!res.ok) throw await _appError(res);
  return res.json();
};

// `updateMindmap` — Task 8 explicit Save: PUT the edited record back to BE
// (validates, protects id/hash/created_at/sources, sets generator.edited +
// updated_at, returns the saved record). 404 for unknown id — including the
// transient "preview" id, which callers must never PUT (see MindElixirView's
// Save-button visibility guard).
export const updateMindmap = async (id, record) => {
  const res = await apiFetch(`/mindmaps/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(record),
  });
  if (!res.ok) throw await _appError(res);
  return res.json();
};

// ── Summary v2: generate / cancel (mirror mindmap; poll qua /summary-status) ──
// Caller branch theo `status` ("done" cache-hit — KHÔNG có job_id — vs "started").
export const generateSummary = async (sources, { lengthMode = "medium", mode = "standard", force = false } = {}) => {
  const res = await apiFetch(`/generate-summary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sources, length_mode: lengthMode, mode, force: Boolean(force) }),
  });
  if (!res.ok) throw await _appError(res);
  return res.json();
};

export const cancelSummary = async (jobId) => {
  const res = await apiFetch(`/summary-cancel/${encodeURIComponent(jobId)}`, { method: "POST" });
  if (!res.ok) throw await _appError(res);
  return res.json();
};

// ── Conversation Context Layer: clear context / delete history / restore ──
// All are flag-gated on the backend: a 404 means the feature is off — callers
// treat that as a no-op success (the FE-side reset still happens).
export const clearConversationContext = async (conversationId) => {
  const res = await apiFetch(`/conversations/${encodeURIComponent(conversationId)}/clear-context`, { method: "POST" });
  if (res.status === 404) return { ok: false, disabled: true };
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
};

export const deleteConversation = async (conversationId) => {
  const res = await apiFetch(`/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
  if (res.status === 404) return { ok: false, disabled: true };
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
};

export const getConversationMessages = async (conversationId) => {
  const res = await apiFetch(`/conversations/${encodeURIComponent(conversationId)}/messages`);
  if (res.status === 404) return { messages: [], disabled: true };
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
};

// `fetchChunkText` — evidence lookup for the mindmap drawer. Returns the raw
// chunk text, or null when the chunk id has no text (BE 404s that case).
export const fetchChunkText = async (chunkId) => {
  const res = await apiFetch(`/chunk-text/${encodeURIComponent(chunkId)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return typeof data?.text === "string" ? data.text : null;
};
