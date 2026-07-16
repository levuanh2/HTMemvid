import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  apiFetch,
  _appError,
  generateSummary,
  isUnauthorizedError,
  isForbiddenError,
  isNotFoundOrForbiddenError,
  getUserFriendlyApiError,
} from "./api";
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
const realWindow = globalThis.window;
const realCE = globalThis.CustomEvent;

beforeEach(() => {
  globalThis.localStorage = memLocalStorage();
  globalThis.CustomEvent = globalThis.CustomEvent
    || class { constructor(type, init) { this.type = type; this.detail = init?.detail; } };
  globalThis.window = { dispatchEvent: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn() };
});
afterEach(() => {
  globalThis.localStorage = realLS;
  globalThis.fetch = realFetch;
  globalThis.window = realWindow;
  globalThis.CustomEvent = realCE;
  clearToken();
});

function mockStatus(status) {
  globalThis.fetch = vi.fn(async () => ({ ok: status < 400, status, json: async () => ({ error: "detail" }) }));
}

function dispatchedTypes() {
  return globalThis.window.dispatchEvent.mock.calls.map((c) => c[0]?.type);
}

describe("apiFetch 401 handling", () => {
  it("fires auth:unauthorized on a protected-API 401 (with the path)", async () => {
    setToken("tok");
    mockStatus(401);
    await apiFetch("/query");
    expect(dispatchedTypes()).toContain("auth:unauthorized");
    const evt = globalThis.window.dispatchEvent.mock.calls[0][0];
    expect(evt.detail.path).toBe("/query");
  });

  it("does NOT fire on a 401 from /auth/* (login/register/me — no redirect loop)", async () => {
    mockStatus(401);
    await apiFetch("/auth/me");
    await apiFetch("/auth/login", { method: "POST" });
    expect(dispatchedTypes()).not.toContain("auth:unauthorized");
  });

  it("does NOT fire on a successful response", async () => {
    mockStatus(200);
    await apiFetch("/list-indexed");
    expect(globalThis.window.dispatchEvent).not.toHaveBeenCalled();
  });

  it("does NOT fire on 403/404 (permission errors are not sign-outs)", async () => {
    mockStatus(403);
    await apiFetch("/generate-summary", { method: "POST" });
    mockStatus(404);
    await apiFetch("/mindmaps/x", { method: "DELETE" });
    expect(dispatchedTypes()).not.toContain("auth:unauthorized");
  });
});

describe("generateSummary body", () => {
  it("gửi length_mode + mode (mặc định standard khi thiếu)", async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ job_id: "j" }) }));
    await generateSummary(["a"], { lengthMode: "short", mode: "study" });
    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body);
    expect(body.length_mode).toBe("short");
    expect(body.mode).toBe("study");
    await generateSummary(["a"]);
    const body2 = JSON.parse(globalThis.fetch.mock.calls[1][1].body);
    expect(body2.mode).toBe("standard");
  });
});

describe("error classifiers", () => {
  it("isUnauthorizedError / isForbiddenError / isNotFoundOrForbiddenError", () => {
    expect(isUnauthorizedError({ status: 401 })).toBe(true);
    expect(isUnauthorizedError({ status: 403 })).toBe(false);
    expect(isForbiddenError({ status: 403 })).toBe(true);
    expect(isNotFoundOrForbiddenError({ status: 403 })).toBe(true);
    expect(isNotFoundOrForbiddenError({ status: 404 })).toBe(true);
    expect(isNotFoundOrForbiddenError({ status: 401 })).toBe(false);
    expect(isNotFoundOrForbiddenError({ status: 500 })).toBe(false);
  });

  it("getUserFriendlyApiError maps status → safe message (no raw JSON/id)", () => {
    expect(getUserFriendlyApiError({ status: 401 })).toMatch(/đăng nhập/i);
    expect(getUserFriendlyApiError({ status: 403 })).toBe("Bạn không có quyền truy cập tài liệu này.");
    expect(getUserFriendlyApiError({ status: 404 })).toBe("Tài nguyên không tồn tại hoặc bạn không có quyền truy cập.");
    expect(getUserFriendlyApiError({ name: "TypeError" })).toMatch(/kết nối/i);
    expect(getUserFriendlyApiError({ status: 500 })).toMatch(/lỗi/i);
  });
});

describe("_appError", () => {
  it("attaches status + code from a failed response", async () => {
    const res = { status: 403, json: async () => ({ error: "forbidden_source" }) };
    const err = await _appError(res);
    expect(err.status).toBe(403);
    expect(err.code).toBe("forbidden_source");
    expect(isForbiddenError(err)).toBe(true);
  });

  it("falls back to HTTP <status> when the body has no error", async () => {
    const res = { status: 404, json: async () => ({}) };
    const err = await _appError(res);
    expect(err.status).toBe(404);
    expect(err.message).toBe("HTTP 404");
  });
});
