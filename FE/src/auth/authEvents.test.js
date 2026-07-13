import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { installUnauthorizedHandler } from "./authEvents";
import { setToken, getToken, clearToken } from "./tokenStore";

function memLocalStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

const realLS = globalThis.localStorage;
const realWindow = globalThis.window;

beforeEach(() => {
  globalThis.localStorage = memLocalStorage();
  globalThis.window = new EventTarget(); // real dispatch/add/removeEventListener
});
afterEach(() => {
  globalThis.localStorage = realLS;
  globalThis.window = realWindow;
  clearToken();
});

function fireUnauthorized() {
  globalThis.window.dispatchEvent(new Event("auth:unauthorized"));
}

describe("installUnauthorizedHandler", () => {
  it("clears the token and notifies on auth:unauthorized (401 → sign-out)", () => {
    const onSignout = vi.fn();
    setToken("tok-123");
    const uninstall = installUnauthorizedHandler(onSignout);
    fireUnauthorized();
    expect(getToken()).toBe(null);   // token cleared
    expect(onSignout).toHaveBeenCalledTimes(1);
    uninstall();
  });

  it("is idempotent — repeated 401s do not loop or throw", () => {
    const onSignout = vi.fn();
    setToken("tok");
    const uninstall = installUnauthorizedHandler(onSignout);
    fireUnauthorized();
    fireUnauthorized();
    expect(getToken()).toBe(null);
    expect(onSignout).toHaveBeenCalledTimes(2);
    uninstall();
  });

  it("uninstall stops handling further events", () => {
    const onSignout = vi.fn();
    const uninstall = installUnauthorizedHandler(onSignout);
    uninstall();
    fireUnauthorized();
    expect(onSignout).not.toHaveBeenCalled();
  });

  it("returns a no-op uninstaller when there is no window (SSR/node)", () => {
    globalThis.window = undefined;
    const uninstall = installUnauthorizedHandler(() => {});
    expect(typeof uninstall).toBe("function");
    expect(() => uninstall()).not.toThrow();
  });
});
