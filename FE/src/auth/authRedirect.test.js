import { describe, it, expect } from "vitest";
import { getAuthRedirectState, safeNext } from "./authRedirect";

describe("getAuthRedirectState", () => {
  it("shows loading while auth is resolving", () => {
    expect(getAuthRedirectState({ loading: true, user: null, path: "/app" })).toEqual({ action: "loading" });
  });

  it("redirects unauthenticated users to /login with next", () => {
    const s = getAuthRedirectState({ loading: false, user: null, path: "/app" });
    expect(s.action).toBe("redirect");
    expect(s.to).toBe("/login?next=%2Fapp");
  });

  it("renders for an authenticated user", () => {
    expect(getAuthRedirectState({ loading: false, user: { id: "1" }, path: "/app" })).toEqual({ action: "render" });
  });
});

describe("safeNext", () => {
  it("keeps a safe in-app path", () => {
    expect(safeNext("/app")).toBe("/app");
    expect(safeNext("/app/chat")).toBe("/app/chat");
  });
  it("rejects external / protocol-relative / missing", () => {
    expect(safeNext("//evil.com")).toBe("/app");
    expect(safeNext("https://evil.com")).toBe("/app");
    expect(safeNext(null)).toBe("/app");
    expect(safeNext("")).toBe("/app");
  });
});
