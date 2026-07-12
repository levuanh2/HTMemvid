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

// apiFetch — attaches `Authorization: Bearer <token>` when a token exists and the
// caller hasn't already set one. Only the Authorization header is added; body,
// method and any caller headers (incl. multipart Content-Type for uploads) are
// left untouched, so existing query/upload/summary/mindmap calls are unchanged.
export const apiFetch = (path, options = {}) => {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  const hasAuth = Object.keys(headers).some((k) => k.toLowerCase() === "authorization");
  if (token && !hasAuth) headers["Authorization"] = `Bearer ${token}`;
  return fetch(apiUrl(path), { ...options, headers });
};

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
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
};

// `cancelMindmap` — real (server-side) cancel: flips a cooperative-abort flag
// the worker checks between graph nodes. Best-effort: caller should still stop
// its own polling immediately rather than waiting on this request.
export const cancelMindmap = async (jobId) => {
  const res = await apiFetch(`/mindmap-cancel/${encodeURIComponent(jobId)}`, { method: "POST" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
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
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.error || `HTTP ${res.status}`);
  }
  return res.json();
};

// ── Summary v2: generate / cancel (mirror mindmap; poll qua /summary-status) ──
// Caller branch theo `status` ("done" cache-hit — KHÔNG có job_id — vs "started").
export const generateSummary = async (sources, { lengthMode = "medium", force = false } = {}) => {
  const res = await apiFetch(`/generate-summary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sources, length_mode: lengthMode, force: Boolean(force) }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
};

export const cancelSummary = async (jobId) => {
  const res = await apiFetch(`/summary-cancel/${encodeURIComponent(jobId)}`, { method: "POST" });
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
