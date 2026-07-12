import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { getToken, setToken, clearToken } from "./tokenStore";

function memLocalStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

const realLS = globalThis.localStorage;
afterEach(() => { globalThis.localStorage = realLS; });

describe("tokenStore", () => {
  beforeEach(() => { globalThis.localStorage = memLocalStorage(); });

  it("set/get/clear round-trip", () => {
    expect(getToken()).toBe(null);
    setToken("abc123");
    expect(getToken()).toBe("abc123");
    clearToken();
    expect(getToken()).toBe(null);
  });

  it("setToken ignores empty values", () => {
    setToken("");
    expect(getToken()).toBe(null);
  });

  it("is safe when localStorage is unavailable", () => {
    globalThis.localStorage = undefined;
    expect(() => setToken("x")).not.toThrow();
    expect(getToken()).toBe(null);
    expect(() => clearToken()).not.toThrow();
  });
});
