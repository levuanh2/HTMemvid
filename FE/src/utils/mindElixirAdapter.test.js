// FE/src/utils/mindElixirAdapter.test.js
import { describe, it, expect } from "vitest";
import { recordToMindElixir, mindElixirToRecord } from "./mindElixirAdapter";

const REC = {
  schema_version: 2, id: "m1", title: "Tài liệu X", content_hash: "h".repeat(64),
  created_at: "2026-07-04T00:00:00Z", sources: ["x_docx"],
  nodes: [
    { id: "n0", parent: null, kind: "root", title: "Tài liệu X", note: "", chunk_refs: [], order: 0 },
    { id: "n1", parent: "n0", kind: "section", title: "1. Mở đầu", note: "tóm ý", chunk_refs: ["3"], order: 0 },
    { id: "n2", parent: "n0", kind: "section", title: "2. Phương pháp", note: "", chunk_refs: ["4"], order: 1 },
    { id: "n3", parent: "n1", kind: "idea", title: "Bối cảnh", note: "n3", chunk_refs: ["3"], order: 0 },
  ],
  relations: [{ source: "n1", target: "n2", type: "leads_to", label: "dẫn tới" }],
  generator: { pipeline: "skeleton_v1", degraded: false, missing: [] },
};

describe("recordToMindElixir", () => {
  it("dựng tree lồng đúng thứ tự + sidecar giữ note/chunkRefs/kind", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    expect(mindData.nodeData.id).toBe("n0");
    expect(mindData.nodeData.topic).toBe("Tài liệu X");
    expect(mindData.nodeData.children.map((c) => c.topic)).toEqual(["1. Mở đầu", "2. Phương pháp"]);
    expect(mindData.nodeData.children[0].children[0].id).toBe("n3");
    expect(sidecar.get("n1")).toEqual({ note: "tóm ý", chunkRefs: ["3"], kind: "section" });
  });

  it("relations → arrows nét đứt màu son có label", () => {
    const { mindData } = recordToMindElixir(REC);
    expect(mindData.arrows).toHaveLength(1);
    const a = mindData.arrows[0];
    expect(a.from).toBe("n1");
    expect(a.to).toBe("n2");
    expect(a.label).toBe("dẫn tới");
    expect(a.style.strokeDasharray).toBe("6 4");
    expect(a.delta1).toBeTruthy(); // arrows inject qua data cần delta
  });

  it("v1 legacy đi qua normalize không vỡ", () => {
    const legacy = { title: "L", nodes: [{ id: "root", parent: null, title: "L" }] };
    const { mindData } = recordToMindElixir(legacy);
    expect(mindData.nodeData.topic).toBe("L");
    expect(mindData.arrows).toEqual([]);
  });
});

describe("mindElixirToRecord", () => {
  it("round-trip bảo toàn cây + note/chunk_refs qua sidecar", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    const out = mindElixirToRecord(mindData, sidecar, REC);
    expect(out.id).toBe("m1");
    expect(out.nodes).toHaveLength(4);
    const n1 = out.nodes.find((n) => n.id === "n1");
    expect(n1).toMatchObject({ parent: "n0", kind: "section", note: "tóm ý", chunk_refs: ["3"] });
    expect(out.relations).toEqual([{ source: "n1", target: "n2", type: "leads_to", label: "dẫn tới" }]);
  });

  it("node user thêm → kind theo depth, refs rỗng; node xoá không rò sidecar", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    mindData.nodeData.children[0].children.push({ id: "me-new-1", topic: "Ý mới", children: [] });
    mindData.nodeData.children.splice(1, 1); // xoá nhánh n2
    const out = mindElixirToRecord(mindData, sidecar, REC);
    const added = out.nodes.find((n) => n.id === "me-new-1");
    expect(added).toMatchObject({ kind: "idea", note: "", chunk_refs: [], parent: "n1" });
    expect(out.nodes.find((n) => n.id === "n2")).toBeUndefined();
    // relation trỏ tới node đã xoá vẫn được trả — BE validate_relations sẽ lọc (không lọc 2 lần ở FE)
  });

  it("arrow mới → relates_to + label", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    mindData.arrows.push({ id: "a9", label: "ghi chú", from: "n3", to: "n2", delta1: { x: 0, y: 0 }, delta2: { x: 0, y: 0 } });
    const out = mindElixirToRecord(mindData, sidecar, REC);
    expect(out.relations).toContainEqual({ source: "n3", target: "n2", type: "relates_to", label: "ghi chú" });
  });
});
