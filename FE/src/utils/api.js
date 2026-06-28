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
