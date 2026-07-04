import { describe, it, expect } from "vitest";
import { normalizeMindmapRecord } from "./mindmapNormalize";

describe("normalizeMindmapRecord", () => {
  it("maps v2 record fields", () => {
    const rec = {
      schema_version: 2, title: "T",
      nodes: [
        { id: "n0", parent: null, kind: "root", title: "T", note: "", chunk_refs: [], order: 0 },
        { id: "n1", parent: "n0", kind: "section", title: "S", note: "tóm", chunk_refs: ["3"], order: 0 },
      ],
      relations: [{ source: "n1", target: "n0", type: "relates_to", label: "" }],
      generator: { degraded: true, missing: ["relations"] },
    };
    const out = normalizeMindmapRecord(rec);
    expect(out.nodes[1].chunkRefs).toEqual(["3"]);
    expect(out.relations).toHaveLength(1);
    expect(out.degraded).toBe(true);
    expect(out.missing).toEqual(["relations"]);
  });

  it("handles legacy v1 nodes-only record", () => {
    const rec = { title: "L", nodes: [{ id: "root", parent: null, title: "L" }] };
    const out = normalizeMindmapRecord(rec);
    expect(out.nodes).toHaveLength(1);
    expect(out.nodes[0].kind).toBe("root");
    expect(out.relations).toEqual([]);
  });

  it("returns empty model for garbage", () => {
    expect(normalizeMindmapRecord(null).nodes).toEqual([]);
  });

  it("v1: diagram extra.type wins over flat node fields for kind", () => {
    const rec = {
      title: "flat",
      nodes: [
        { id: "a", parent: null, title: "A", type: "root", kind: "section" },
        { id: "b", parent: "a", title: "B" },
      ],
      diagram: { title: "diagram-title", nodes: [{ id: "b", type: "process" }], edges: [] },
    };
    const out = normalizeMindmapRecord(rec);
    expect(out.title).toBe("diagram-title");                    // diagram title first (v1)
    expect(out.nodes.find(n => n.id === "a").kind).toBe("root"); // parentless, no extra.type
    expect(out.nodes.find(n => n.id === "b").kind).toBe("process"); // extra.type wins
  });

  it("v1 diagram-only: only index 0 becomes root when type absent", () => {
    const rec = { diagram: { nodes: [{ id: "x" }, { id: "y" }], edges: [] } };
    const out = normalizeMindmapRecord(rec);
    expect(out.nodes[0].kind).toBe("root");
    expect(out.nodes[1].kind).not.toBe("root");
  });
});
