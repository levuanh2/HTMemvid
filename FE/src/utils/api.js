// Chỉ dùng biến Vite (prefix VITE_), đọc qua import.meta.env.
const RAW_BASE = (
  import.meta.env.VITE_API_URL ||
  import.meta.env.VITE_API_BASE ||
  ""
).trim();
// Chuẩn hoá base: bỏ dấu / cuối; nếu lỡ set ".../api" thì gỡ để tránh /api/api/...
let API_BASE = RAW_BASE.replace(/\/+$/, "").replace(/\/api$/, "");

// Local dev: nếu chưa set env, fallback backend mặc định (cùng pattern /api/... trên server).
if (!API_BASE && import.meta.env.DEV) {
  API_BASE = "http://localhost:5000";
}

function isAbsoluteUrl(u = "") {
  return /^https?:\/\//i.test(u);
}

/**
 * Chuẩn hoá URL gọi API:
 * - Có VITE_API_URL (vd: deploy Vercel → Railway): `${VITE_API_URL}/api/...`
 * - Không có base (vd: Docker + nginx proxy): giữ relative `/api/...`
 */
export function apiUrl(path = "") {
  if (!path) return API_BASE || "";
  if (isAbsoluteUrl(path)) return path;

  const p = path.startsWith("/") ? path : `/${path}`;

  if (API_BASE) {
    const base = API_BASE.replace(/\/+$/, "");
    return `${base}${p}`;
  }

  return p;
}

export function apiFetch(path, options) {
  return fetch(apiUrl(path), options);
}

