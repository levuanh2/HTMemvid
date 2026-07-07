import { describe, it, expect, beforeEach } from "vitest";

const _mem = new Map();
globalThis.localStorage = {
  getItem: (k) => (_mem.has(k) ? _mem.get(k) : null),
  setItem: (k, v) => _mem.set(k, String(v)),
  removeItem: (k) => _mem.delete(k),
  clear: () => _mem.clear(),
};

import { makeActiveJobStore } from "./activeJob";

describe("makeActiveJobStore", () => {
  beforeEach(() => localStorage.clear());

  it("hai store khác key không đè nhau", () => {
    const a = makeActiveJobStore("k_mindmap");
    const b = makeActiveJobStore("k_summary");
    a.save({ jobId: "j1", sources: ["x"], startedAt: 1 });
    b.save({ jobId: "j2", sources: ["y"], startedAt: 2 });
    expect(a.load().jobId).toBe("j1");
    expect(b.load().jobId).toBe("j2");
    a.clear();
    expect(a.load()).toBeNull();
    expect(b.load().jobId).toBe("j2");
  });

  it("extra passthrough (lengthMode) chỉ hiện khi có", () => {
    const s = makeActiveJobStore("k");
    s.save({ jobId: "j", sources: [], startedAt: 1 });
    expect(s.load()).not.toHaveProperty("extra");
    s.save({ jobId: "j", sources: [], startedAt: 1, extra: { lengthMode: "short" } });
    expect(s.load().extra).toEqual({ lengthMode: "short" });
  });

  it("dữ liệu hỏng → null + tự dọn", () => {
    localStorage.setItem("k", "{broken");
    const s = makeActiveJobStore("k");
    expect(s.load()).toBeNull();
    expect(localStorage.getItem("k")).toBeNull();
    localStorage.setItem("k", JSON.stringify({ jobId: "" }));
    expect(s.load()).toBeNull();
  });
});
