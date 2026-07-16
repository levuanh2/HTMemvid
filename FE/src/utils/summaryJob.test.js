import { describe, it, expect } from "vitest";
import { stageLabel, normalizeSummaryRecord, LENGTH_MODES, SUMMARY_MODES } from "./summaryJob";

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
