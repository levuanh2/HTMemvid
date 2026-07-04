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

// `fetchChunkText` — evidence lookup for the mindmap drawer. Returns the raw
// chunk text, or null when the chunk id has no text (BE 404s that case).
export const fetchChunkText = async (chunkId) => {
  const res = await apiFetch(`/chunk-text/${encodeURIComponent(chunkId)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return typeof data?.text === "string" ? data.text : null;
};
