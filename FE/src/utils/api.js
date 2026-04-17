// Ưu tiên biến bạn set trên Vercel: VITE_API_URL.
// Hỗ trợ thêm VITE_API_BASE (nếu bạn dùng tên cũ).
const RAW_BASE = (
  import.meta.env?.VITE_API_URL ||
  import.meta.env?.VITE_API_BASE ||
  ""
).trim();
// Normalize base:
// - remove trailing slashes
// - if user accidentally sets ".../api" (from old proxy pattern), strip it
let API_BASE = RAW_BASE
  .replace(/\/+$/, "")
  .replace(/\/api$/, "");

// Local dev (vite) tiện dụng:
// - Nếu bạn chạy FE bằng `npm run dev` và backend chạy ở :5000,
//   nhưng bạn quên set env, thì tự fallback về localhost:5000.
if (!API_BASE && import.meta.env?.DEV) {
  API_BASE = "http://localhost:5000";
}

function isAbsoluteUrl(u = "") {
  return /^https?:\/\//i.test(u);
}

/**
 * Chuẩn hoá đường dẫn API theo môi trường:
 * - Local Docker (nginx proxy): gọi "/api/..." (relative) để nginx proxy sang backend.
 * - Production (Vercel -> Railway): ghép với VITE_API_URL và tự bỏ prefix "/api" (vì backend không có prefix này).
 */
export function apiUrl(path = "") {
  if (!path) return API_BASE || "";
  if (isAbsoluteUrl(path)) return path;

  let p = path.startsWith("/") ? path : `/${path}`;

  // Nếu có API_BASE (prod) thì bỏ "/api" để trỏ đúng endpoint backend
  if (API_BASE) {
    p = p.replace(/^\/api(?=\/)/, "");
    return `${API_BASE}${p}`;
  }

  // Không có base (local) → giữ nguyên relative (vd: /api/...)
  return p;
}

export function apiFetch(path, options) {
  return fetch(apiUrl(path), options);
}

