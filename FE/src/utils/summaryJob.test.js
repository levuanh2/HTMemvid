import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { stageLabel, normalizeSummaryRecord, createSummaryPoller, LENGTH_MODES, SUMMARY_MODES } from "./summaryJob";

describe("stageLabel (summary)", () => {
  it("map node pipeline sang label Việt", () => {
    expect(stageLabel({ current_node: "Sections" })).toBe("Dựng mục lục…");
    expect(stageLabel({ current_node: "CollectInput" })).toBe("Dựng mục lục…");
    expect(stageLabel({ current_node: "Đang tóm tắt mục 2/5..." })).toContain("2/5");
    expect(stageLabel({ current_node: "Synthesize" })).toBe("Đang tổng hợp…");
    expect(stageLabel({ current_node: "AssemblePersist" })).toBe("Đang lưu tóm tắt…");
    expect(stageLabel({})).toBe("Đang tóm tắt…");
  });
});

describe("LENGTH_MODES", () => {
  it("khớp contract BE (short/medium/detailed)", () => {
    expect(LENGTH_MODES.map((m) => m.value)).toEqual(["short", "medium", "detailed"]);
  });
});

describe("SUMMARY_MODES", () => {
  it("khớp contract BE (standard/study)", () => {
    expect(SUMMARY_MODES.map((m) => m.value)).toEqual(["standard", "study"]);
  });
});

describe("createSummaryPoller — terminal cancel/interrupted (UI không kẹt 'Đang huỷ…')", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const mk = (statuses, cbs = {}) => {
    let i = 0;
    const fetchStatus = vi.fn(async () => statuses[Math.min(i++, statuses.length - 1)]);
    const events = { done: [], errors: [], cancelled: 0 };
    const poller = createSummaryPoller({
      fetchStatus,
      onDone: (r) => events.done.push(r),
      onError: (e) => events.errors.push(e),
      onCancelled: () => events.cancelled++,
      ...cbs,
    });
    return { poller, events, fetchStatus };
  };

  it("cancelled là terminal → onCancelled, dừng poll", async () => {
    const { poller, events, fetchStatus } = mk([
      { status: "running", progress: 36, current_node: "Đang tóm tắt mục 1/7..." },
      { status: "cancelled", progress: 0 },
    ]);
    poller.start("sj1");
    await vi.advanceTimersByTimeAsync(30_000);
    expect(events.cancelled).toBe(1);
    const calls = fetchStatus.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetchStatus.mock.calls.length).toBe(calls); // đã dừng hẳn
  });

  it("interrupted (job mồ côi sau BE restart) là terminal → onError, KHÔNG poll vô hạn", async () => {
    const { poller, events, fetchStatus } = mk([
      { status: "interrupted", progress: 36, current_node: "Đang tóm tắt mục 1/7..." },
    ]);
    poller.start("sj-orphan");
    await vi.advanceTimersByTimeAsync(120_000);
    expect(events.errors).toHaveLength(1);
    expect(events.errors[0].message).toContain("gián đoạn");
    expect(fetchStatus).toHaveBeenCalledTimes(1);
  });
});

describe("normalizeSummaryRecord", () => {
  it("record v2 giữ sections, không set legacyMd", () => {
    const rec = normalizeSummaryRecord({
      id: "s1", title: "T", sources: ["a"], created_at: "2026-07-06T00:00:00Z",
      length_mode: "short", overview: "ov",
      sections: [{ id: "x", title: "M", summary: "s", chunk_refs: ["0"] }],
      entities: ["E"], generator: { degraded: true, missing: ["synthesize"] },
    });
    expect(rec.sections).toHaveLength(1);
    expect(rec.legacyMd).toBe("");
    expect(rec.lengthMode).toBe("short");
    expect(rec.generator.degraded).toBe(true);
  });

  it("mode thiếu (record cũ) → standard, study → null", () => {
    const rec = normalizeSummaryRecord({ id: "s1", sections: [{ id: "x", title: "M" }] });
    expect(rec.mode).toBe("standard");
    expect(rec.study).toBeNull();
  });

  it("mode=study giữ mode + block study", () => {
    const rec = normalizeSummaryRecord({
      id: "s1", mode: "study", sections: [{ id: "x", title: "M" }],
      study: { key_concepts: ["A"], self_check: [{ q: "?", a_hint: "h" }] },
    });
    expect(rec.mode).toBe("study");
    expect(rec.study.key_concepts).toEqual(["A"]);
  });

  it("mode lạ → standard; study không phải object → null", () => {
    const rec = normalizeSummaryRecord({ id: "s1", mode: "bogus", study: "nope", sections: [{ id: "x" }] });
    expect(rec.mode).toBe("standard");
    expect(rec.study).toBeNull();
  });

  it("record legacy (migrate summaries.json) rơi về summary_md / data.summary", () => {
    expect(normalizeSummaryRecord({ id: "o1", summary_md: "## Cũ" }).legacyMd).toBe("## Cũ");
    expect(normalizeSummaryRecord({ id: "o2", data: { summary: "cũ hơn" } }).legacyMd).toBe("cũ hơn");
    // shape từ endpoint sync cũ (chưa từng migrate) — vẫn mở được
    expect(normalizeSummaryRecord({ id: "o3", summary: "raw" }).legacyMd).toBe("raw");
  });

  it("input rác → null, field thiếu → default an toàn", () => {
    expect(normalizeSummaryRecord(null)).toBeNull();
    expect(normalizeSummaryRecord("x")).toBeNull();
    const rec = normalizeSummaryRecord({});
    expect(rec.sections).toEqual([]);
    expect(rec.entities).toEqual([]);
    expect(rec.title).toBe("Tóm tắt");
  });
});
