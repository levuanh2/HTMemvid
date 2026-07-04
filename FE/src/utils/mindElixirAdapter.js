// Adapter record v2 ↔ mind-elixir. Pure — không import mind-elixir (chỉ shape data).
// Sidecar: mind-elixir KHÔNG cam kết bảo toàn field lạ qua operations → note/chunk_refs/kind
// sống ở Map riêng, merge lại lúc save.
import { normalizeMindmapRecord } from "./mindmapNormalize";

export const REL_LABELS = {
  relates_to: "liên quan", leads_to: "dẫn tới", causes: "gây ra",
  supports: "bổ trợ", contrasts: "đối lập", contains: "bao hàm",
};

const ARROW_STYLE = {
  stroke: "var(--accent)", strokeWidth: 2, strokeDasharray: "6 4",
  labelColor: "var(--accent)", opacity: 0.9,
};

export function recordToMindElixir(record) {
  const norm = normalizeMindmapRecord(record);
  const sidecar = new Map();
  const byParent = new Map();
  let root = null;
  for (const n of norm.nodes) {
    sidecar.set(n.id, { note: n.note || "", chunkRefs: n.chunkRefs || [], kind: n.kind });
    if (n.kind === "root" || n.parent == null) { root = root || n; continue; }
    if (!byParent.has(n.parent)) byParent.set(n.parent, []);
    byParent.get(n.parent).push(n);
  }
  const toTree = (n) => ({
    id: n.id, topic: n.title,
    children: (byParent.get(n.id) || [])
      .slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0)).map(toTree),
  });
  const nodeData = root
    ? toTree(root)
    : { id: "n0", topic: norm.title || "Sơ đồ tư duy", children: [] };
  const arrows = (norm.relations || []).map((r, i) => ({
    id: `rel-${i}`, label: r.label || REL_LABELS[r.type] || "liên quan",
    from: r.source, to: r.target,
    delta1: { x: 80, y: -60 }, delta2: { x: -80, y: -60 },
    style: { ...ARROW_STYLE },
  }));
  return { mindData: { nodeData, arrows, direction: 2 /* MindElixir.SIDE */ }, sidecar };
}

export function mindElixirToRecord(mindData, sidecar, baseRecord) {
  const nodes = [];
  const walk = (node, parent, depth, order) => {
    const side = sidecar.get(node.id);
    nodes.push({
      id: node.id, parent,
      kind: side?.kind || (depth === 0 ? "root" : depth === 1 ? "section" : "idea"),
      title: node.topic || "", note: side?.note || "",
      chunk_refs: side?.chunkRefs || [], order,
    });
    (node.children || []).forEach((c, i) => walk(c, node.id, depth + 1, i));
  };
  walk(mindData.nodeData, null, 0, 0);

  const baseType = new Map(
    (baseRecord.relations || []).map((r) => [`${r.source}→${r.target}`, r.type])
  );
  const relations = (mindData.arrows || []).map((a) => ({
    source: a.from, target: a.to,
    type: baseType.get(`${a.from}→${a.to}`) || "relates_to",
    label: a.label || "",
  }));

  return { ...baseRecord, title: mindData.nodeData.topic || baseRecord.title, nodes, relations };
}
