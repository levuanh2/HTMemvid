import { describe, it, expect, beforeEach } from "vitest";

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};

import {
  ACTIVE_JOB_KEY,
  saveActiveMindmapJob,
  loadActiveMindmapJob,
  clearActiveMindmapJob,
} from "./activeMindmapJob";

describe("activeMindmapJob", () => {
  beforeEach(() => localStorage.clear());

  it("save/load roundtrip", () => {
    saveActiveMindmapJob({ jobId: "j1", sources: ["a_docx"], startedAt: 123 });
    expect(loadActiveMindmapJob()).toEqual({
      jobId: "j1",
      sources: ["a_docx"],
      startedAt: 123,
    });
  });

  it("clear xoa key", () => {
    saveActiveMindmapJob({ jobId: "j1", sources: [], startedAt: 1 });
    clearActiveMindmapJob();
    expect(loadActiveMindmapJob()).toBeNull();
    expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
  });

  it("JSON rac -> null + don key", () => {
    localStorage.setItem(ACTIVE_JOB_KEY, "{khong phai json");
    expect(loadActiveMindmapJob()).toBeNull();
    expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
  });

  it("thieu jobId -> null", () => {
    localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify({ sources: [] }));
    expect(loadActiveMindmapJob()).toBeNull();
  });
});
