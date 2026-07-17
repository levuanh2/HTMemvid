// streamPreview — throttle flush cho streaming markdown preview.
// Chứng minh: N token trong 1 cửa sổ throttle → 1 flush (không phải N),
// flush cuối vẫn thấy ĐỦ text tích luỹ, cancel chặn flush treo.
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

import { createPreviewThrottle, STREAM_PREVIEW_FLUSH_MS } from "./streamPreview";

describe("createPreviewThrottle", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("many tokens in one window → single flush with full accumulated text", () => {
    let acc = "";
    const flushes = [];
    const t = createPreviewThrottle(() => flushes.push(acc), 150);

    for (let i = 0; i < 50; i++) {
      acc += "tok";
      t.schedule();
    }
    expect(flushes.length).toBe(0); // chưa tới hạn — không flush per token
    vi.advanceTimersByTime(150);
    expect(flushes).toEqual(["tok".repeat(50)]); // MỘT flush, đủ 50 token
  });

  it("tokens across windows → one flush per window, not per token", () => {
    let acc = "";
    const flushes = [];
    const t = createPreviewThrottle(() => flushes.push(acc), 100);

    for (let w = 0; w < 3; w++) {
      for (let i = 0; i < 20; i++) {
        acc += "x";
        t.schedule();
        vi.advanceTimersByTime(2); // token mỗi 2ms — dày hơn cửa sổ 100ms nhiều
      }
      vi.advanceTimersByTime(100);
    }
    // 60 token → vài flush (mỗi cửa sổ ~1), KHÔNG phải 60 flush per token
    expect(flushes.length).toBeLessThanOrEqual(6);
    expect(flushes[flushes.length - 1]).toBe("x".repeat(60)); // flush cuối thấy đủ text
  });

  it("cancel prevents the pending flush", () => {
    const flush = vi.fn();
    const t = createPreviewThrottle(flush, 150);
    t.schedule();
    expect(t.pending()).toBe(true);
    t.cancel();
    vi.advanceTimersByTime(1000);
    expect(flush).not.toHaveBeenCalled();
    expect(t.pending()).toBe(false);
  });

  it("cancel is idempotent and schedule works again after cancel", () => {
    const flush = vi.fn();
    const t = createPreviewThrottle(flush, 150);
    t.cancel(); // chưa schedule — no-op
    t.schedule();
    t.cancel();
    t.cancel();
    t.schedule();
    vi.advanceTimersByTime(150);
    expect(flush).toHaveBeenCalledTimes(1);
  });

  it("default interval exported and sane", () => {
    expect(STREAM_PREVIEW_FLUSH_MS).toBeGreaterThanOrEqual(100); // yêu cầu ≥100ms
    expect(STREAM_PREVIEW_FLUSH_MS).toBeLessThanOrEqual(500);
  });
});
