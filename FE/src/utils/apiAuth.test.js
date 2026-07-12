import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { apiFetch } from "./api";
import { setToken, clearToken } from "../auth/tokenStore";

function memLocalStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

const realLS = globalThis.localStorage;
const realFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.localStorage = memLocalStorage();
  globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
});
afterEach(() => { globalThis.localStorage = realLS; globalThis.fetch = realFetch; clearToken(); });

function lastHeaders() {
  return globalThis.fetch.mock.calls[0][1].headers;
}

describe("apiFetch Authorization injection", () => {
  it("adds Bearer header when a token exists", async () => {
    setToken("tok-123");
    await apiFetch("/anything");
    expect(lastHeaders()["Authorization"]).toBe("Bearer tok-123");
  });

  it("adds no Authorization when no token", async () => {
    await apiFetch("/anything");
    const h = lastHeaders() || {};
    expect(Object.keys(h).some((k) => k.toLowerCase() === "authorization")).toBe(false);
  });

  it("does not overwrite a caller-provided Authorization header", async () => {
    setToken("tok-123");
    await apiFetch("/anything", { headers: { Authorization: "Bearer caller" } });
    expect(lastHeaders()["Authorization"]).toBe("Bearer caller");
  });

  it("preserves caller headers (e.g. Content-Type)", async () => {
    setToken("tok-123");
    await apiFetch("/anything", { method: "POST", headers: { "Content-Type": "application/json" } });
    expect(lastHeaders()["Content-Type"]).toBe("application/json");
    expect(lastHeaders()["Authorization"]).toBe("Bearer tok-123");
  });
});
