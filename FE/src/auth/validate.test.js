import { describe, it, expect } from "vitest";
import { isValidEmail, isValidPassword, passwordsMatch } from "./validate";

describe("isValidEmail", () => {
  it("accepts a normal email", () => {
    expect(isValidEmail("a@b.com")).toBe(true);
    expect(isValidEmail("  user.name@memvid.io  ")).toBe(true);
  });
  it("rejects malformed input", () => {
    expect(isValidEmail("a@b")).toBe(false);
    expect(isValidEmail("no-at-sign")).toBe(false);
    expect(isValidEmail("")).toBe(false);
    expect(isValidEmail(null)).toBe(false);
  });
});

describe("isValidPassword", () => {
  it("requires at least 8 chars", () => {
    expect(isValidPassword("12345678")).toBe(true);
    expect(isValidPassword("short")).toBe(false);
    expect(isValidPassword("")).toBe(false);
    expect(isValidPassword(undefined)).toBe(false);
  });
});

describe("passwordsMatch", () => {
  it("true only when equal and non-empty", () => {
    expect(passwordsMatch("abcd1234", "abcd1234")).toBe(true);
    expect(passwordsMatch("abcd1234", "different")).toBe(false);
    expect(passwordsMatch("", "")).toBe(false);
  });
});
