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

  it("multi-root: node parent null thứ hai được đưa về dưới root + round-trip giữ lại", () => {
    const rec = {
      ...REC,
      nodes: [
        ...REC.nodes,
        { id: "n9", parent: null, kind: "section", title: "Gốc thừa", note: "", chunk_refs: [], order: 0 },
      ],
    };
    const { mindData, sidecar } = recordToMindElixir(rec);
    // đứng SAU các con sẵn có của root, không bị drop
    expect(mindData.nodeData.children.map((c) => c.id)).toEqual(["n1", "n2", "n9"]);
    const out = mindElixirToRecord(mindData, sidecar, rec);
    expect(out.nodes.find((n) => n.id === "n9")).toMatchObject({ parent: "n0", kind: "section" });
  });

  it("dangling parent: node trỏ GHOST vẫn hiện dưới root, round-trip giữ note/chunk_refs", () => {
    const rec = {
      ...REC,
      nodes: [
        ...REC.nodes,
        { id: "n8", parent: "GHOST", kind: "idea", title: "Mồ côi", note: "ghi chú mồ côi", chunk_refs: ["7"], order: 0 },
      ],
    };
    const { mindData, sidecar } = recordToMindElixir(rec);
    expect(mindData.nodeData.children.map((c) => c.id)).toContain("n8");
    const out = mindElixirToRecord(mindData, sidecar, rec);
    expect(out.nodes.find((n) => n.id === "n8")).toMatchObject({
      parent: "n0", kind: "idea", note: "ghi chú mồ côi", chunk_refs: ["7"],
    });
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

describe("redesign Phòng đọc (tags + relation color + type từ label)", () => {
  it("node có chunkRefs được gắn tag ※N; node không có thì không", () => {
    const { mindData } = recordToMindElixir(REC);
    const sec1 = mindData.nodeData.children[0];
    expect(sec1.tags).toEqual(["※ 1"]);
    expect(mindData.nodeData.tags).toBeUndefined(); // root không có refs
  });

  it("arrow dùng --mm-relation (seal đỏ chỉ dành cho provenance)", () => {
    const { mindData } = recordToMindElixir(REC);
    expect(mindData.arrows[0].style.stroke).toBe("var(--mm-relation)");
    expect(mindData.arrows[0].style.labelColor).toBe("var(--mm-relation)");
  });

  it("arrow mới suy type từ nhãn tiếng Việt thay vì rớt về relates_to", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    // user vẽ arrow mới n2→n3 với label "gây ra" (không có trong baseRecord)
    mindData.arrows.push({ id: "new", from: "n2", to: "n3", label: "gây ra" });
    const rec = mindElixirToRecord(mindData, sidecar, REC);
    const created = rec.relations.find((r) => r.source === "n2" && r.target === "n3");
    expect(created.type).toBe("causes");
    // arrow gốc vẫn giữ type theo (source,target)
    const kept = rec.relations.find((r) => r.source === "n1" && r.target === "n2");
    expect(kept.type).toBe("leads_to");
  });

  it("tags không rò vào record khi save (round-trip sạch)", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    const rec = mindElixirToRecord(mindData, sidecar, REC);
    for (const n of rec.nodes) expect(n.tags).toBeUndefined();
  });
});
