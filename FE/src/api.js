import axios from "axios";

let baseURL = (
  import.meta.env.VITE_API_URL ||
  import.meta.env.VITE_API_BASE ||
  ""
)
  .trim()
  .replace(/\/+$/, "")
  .replace(/\/api$/, "");

if (!baseURL && import.meta.env.DEV) {
  baseURL = "http://localhost:5000";
}

const API = axios.create({
  baseURL: baseURL || undefined,
});

export default API;
