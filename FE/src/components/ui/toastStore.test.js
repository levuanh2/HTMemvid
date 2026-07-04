import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { toast, subscribeToasts, dismissToast, _resetToasts } from "./Toaster";

describe("toast store", () => {
  beforeEach(() => { vi.useFakeTimers(); _resetToasts(); });
  afterEach(() => vi.useRealTimers());

  it("push + auto-expire theo duration", () => {
    const seen = [];
    subscribeToasts((list) => seen.push(list.map((t) => t.message)));
    toast("Sơ đồ sẵn sàng", { type: "success", duration: 3000 });
    expect(seen.at(-1)).toEqual(["Sơ đồ sẵn sàng"]);
    vi.advanceTimersByTime(3100);
    expect(seen.at(-1)).toEqual([]);
  });

  it("dismiss thủ công", () => {
    let latest = [];
    subscribeToasts((l) => { latest = l; });
    toast("x", { duration: 60_000 });
    dismissToast(latest[0].id);
    expect(latest).toEqual([]);
  });
});
