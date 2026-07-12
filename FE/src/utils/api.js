const API = import.meta.env.VITE_API_URL;

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

export const apiFetch = (path, options = {}) => {
  return fetch(apiUrl(path), options);
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
