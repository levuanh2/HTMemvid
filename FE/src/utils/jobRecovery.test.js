// PR#8 — guard regenerate khi dirty, stall banner snooze, retry context.
import { describe, it, expect, vi } from "vitest";

import { confirmRegenerateIfDirty, stallBannerVisible, canRetry } from "./jobRecovery";
import { STALL_MS } from "./jobPoller";

describe("confirmRegenerateIfDirty", () => {
  it("not dirty → proceeds without asking", () => {
    const confirm = vi.fn();
    expect(confirmRegenerateIfDirty(false, confirm)).toBe(true);
    expect(confirm).not.toHaveBeenCalled(); // hành vi cũ giữ nguyên
  });

  it("dirty + user confirms → proceeds", () => {
    expect(confirmRegenerateIfDirty(true, () => true)).toBe(true);
  });

  it("dirty + user cancels → blocked (no regenerate)", () => {
    expect(confirmRegenerateIfDirty(true, () => false)).toBe(false);
  });

  it("asks with a message mentioning unsaved changes", () => {
    const confirm = vi.fn(() => true);
    confirmRegenerateIfDirty(true, confirm);
    expect(confirm.mock.calls[0][0]).toMatch(/chưa lưu/);
  });
});

describe("stallBannerVisible", () => {
  const now = 10_000_000;

  it("not stalled → hidden", () => {
    expect(stallBannerVisible(false, 0, now)).toBe(false);
    expect(stallBannerVisible(false, now - 1, now)).toBe(false);
  });

  it("stalled, never dismissed → visible", () => {
    expect(stallBannerVisible(true, 0, now)).toBe(true);
  });

  it("stalled, dismissed recently → snoozed (continue waiting)", () => {
    expect(stallBannerVisible(true, now - 1000, now)).toBe(false);
  });

  it("stalled, snooze window elapsed → nags again", () => {
    expect(stallBannerVisible(true, now - STALL_MS - 1, now)).toBe(true);
  });
});

describe("canRetry", () => {
  it("needs sources from the failed run", () => {
    expect(canRetry(null)).toBe(false);
    expect(canRetry({})).toBe(false);
    expect(canRetry({ sources: [] })).toBe(false);
    expect(canRetry({ sources: ["doc_a"] })).toBe(true);
    expect(canRetry({ sources: ["doc_a"], lengthMode: "short", mode: "study" })).toBe(true);
  });
});
