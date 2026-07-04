import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { pollIntervalMs, stageLabel, createMindmapPoller, STALL_MS } from "./mindmapJob";

describe("pollIntervalMs", () => {
  it("giãn 2s → 5s → 10s", () => {
    expect(pollIntervalMs(0)).toBe(2000);
    expect(pollIntervalMs(29_000)).toBe(2000);
    expect(pollIntervalMs(31_000)).toBe(5000);
    expect(pollIntervalMs(121_000)).toBe(10_000);
  });
});

describe("stageLabel", () => {
  it("map node pipeline sang label", () => {
    expect(stageLabel({ current_node: "Skeleton" })).toBe("Dựng khung xương…");
    expect(stageLabel({ current_node: "Enrich", message: "nhánh 2/5" })).toContain("nhánh 2/5");
    expect(stageLabel({ current_node: "Relations" })).toBe("Tìm quan hệ chéo…");
    expect(stageLabel({})).toBe("Đang tạo sơ đồ…");
  });
});

describe("createMindmapPoller", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const mk = (statuses, cbs = {}) => {
    let i = 0;
    const fetchStatus = vi.fn(async () => statuses[Math.min(i++, statuses.length - 1)]);
    const events = { ticks: [], done: [], errors: [], cancelled: 0 };
    const poller = createMindmapPoller({
      fetchStatus,
      onTick: (s, meta) => events.ticks.push([s, meta]),
      onDone: (r) => events.done.push(r),
      onError: (e) => events.errors.push(e),
      onCancelled: () => events.cancelled++,
      ...cbs,
    });
    return { poller, events, fetchStatus };
  };

  it("poll tới done, KHÔNG có hard timeout (chạy quá 190s vẫn sống)", async () => {
    const running = { status: "running", progress: 50, current_node: "Enrich" };
    const statuses = Array(60).fill(running).concat([{ status: "done", result: { id: "r1" } }]);
    const { poller, events } = mk(statuses);
    poller.start("j1");
    await vi.advanceTimersByTimeAsync(600_000); // 10 phút
    expect(events.done).toEqual([{ id: "r1" }]);
    expect(events.errors).toHaveLength(0);
  });

  it("stalled=true khi fingerprint đứng yên quá STALL_MS", async () => {
    const frozen = { status: "running", progress: 40, current_node: "Enrich" };
    const { poller, events } = mk(Array(200).fill(frozen));
    poller.start("j1");
    await vi.advanceTimersByTimeAsync(STALL_MS + 60_000);
    const lastMeta = events.ticks.at(-1)[1];
    expect(lastMeta.stalled).toBe(true);
    poller.stop();
  });

  it("fetch lỗi mạng không dừng poll", async () => {
    let calls = 0;
    const fetchStatus = vi.fn(async () => {
      calls++;
      if (calls < 3) throw new Error("mạng rớt");
      return { status: "done", result: { id: "ok" } };
    });
    const { poller, events } = mk([], { fetchStatus });
    poller.start("j1");
    await vi.advanceTimersByTimeAsync(30_000);
    expect(events.done).toEqual([{ id: "ok" }]);
  });

  it("cancelled → onCancelled; stop() chặn tick sau", async () => {
    const { poller, events } = mk([{ status: "cancelled" }]);
    poller.start("j1");
    await vi.advanceTimersByTimeAsync(5000);
    expect(events.cancelled).toBe(1);
    poller.stop(); // idempotent, không ném
  });
});
