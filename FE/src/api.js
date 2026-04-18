import axios from "axios";

const API = import.meta.env.VITE_API_URL;

let baseURL = String(API ?? "")
  .trim()
  .replace(/\/+$/, "")
  .replace(/\/api$/, "");
if (!baseURL && import.meta.env.DEV) {
  baseURL = "http://localhost:8080";
}

const client = axios.create({
  baseURL: baseURL || undefined,
});

export default client;
