import { describe, it, expect } from "vitest";
import { unescapeMd } from "./evidence";

describe("unescapeMd", () => {
  it("bỏ backslash-escape punctuation an toàn (mammoth)", () => {
    expect(unescapeMd("Phân tích \(Behavioral analysis\)\.")).toBe("Phân tích (Behavioral analysis).");
    expect(unescapeMd("__1\. Làm thế nào\?__")).toBe("__1. Làm thế nào?__");
  });
  it("KHÔNG unescape ký tự cấu trúc markdown", () => {
    expect(unescapeMd("\# x \* y \- z \[a\]")).toBe("\# x \* y \- z \[a\]");
  });
  it("null/undefined → chuỗi rỗng", () => {
    expect(unescapeMd(null)).toBe("");
    expect(unescapeMd(undefined)).toBe("");
  });
});
