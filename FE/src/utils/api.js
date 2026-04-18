// Biến Vite: chỉ dùng prefix VITE_
let API = (
  import.meta.env.VITE_API_URL ||
  import.meta.env.VITE_API_BASE ||
  ""
)
  .trim()
  .replace(/\/+$/, "")
  .replace(/\/api$/, "");

// Dev: backend local nếu chưa set env
if (!API && import.meta.env.DEV) {
  API = "http://localhost:5000";
}

export const apiFetch = (path, options = {}) => {
  const p = path.startsWith("/") ? path : `/${path}`;
  const url = API ? `${API}${p}` : p;
  return fetch(url, options);
};
