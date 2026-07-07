// Adapter record v2 ↔ mind-elixir. Pure — không import mind-elixir (chỉ shape data).
// Sidecar: mind-elixir KHÔNG cam kết bảo toàn field lạ qua operations → note/chunk_refs/kind
// sống ở Map riêng, merge lại lúc save.
import { normalizeMindmapRecord } from "./mindmapNormalize";

export const REL_LABELS = {
  relates_to: "liên quan", leads_to: "dẫn tới", causes: "gây ra",
  supports: "bổ trợ", contrasts: "đối lập", contains: "bao hàm",
};

// --mm-relation (định nghĩa trong mindmap.css, flip theo html.dark) chứ KHÔNG phải
// --accent: seal đỏ dành riêng cho provenance/active/error, không dùng trang trí quan hệ.
const ARROW_STYLE = {
  stroke: "var(--mm-relation)", strokeWidth: 2, strokeDasharray: "6 4",
  labelColor: "var(--mm-relation)", opacity: 0.9,
};

// Ngược của REL_LABELS: nhãn tiếng Việt → type, để arrow user vẽ/reconnect vẫn giữ
// đúng semantic type nếu label còn khớp (thay vì luôn rớt về relates_to).
const LABEL_TO_TYPE = Object.fromEntries(
  Object.entries(REL_LABELS).map(([type, label]) => [label, type])
);

export function recordToMindElixir(record) {
  const norm = normalizeMindmapRecord(record);
  const sidecar = new Map();
  const byParent = new Map();
  const ids = new Set(norm.nodes.map((n) => n.id));
  let root = null;
  const orphans = []; // extra parentless nodes + dangling parent refs — rescued under root
  for (const n of norm.nodes) {
    sidecar.set(n.id, { note: n.note || "", chunkRefs: n.chunkRefs || [], kind: n.kind });
    if (!root && (n.kind === "root" || n.parent == null)) { root = n; continue; }
    if (n.parent == null || !ids.has(n.parent)) { orphans.push(n); continue; }
    if (!byParent.has(n.parent)) byParent.set(n.parent, []);
    byParent.get(n.parent).push(n);
  }
  // Reparent orphans under root, AFTER its existing children — otherwise the tree
  // walk drops them and a load+save round-trip silently deletes those subtrees.
  if (root && orphans.length) {
    if (!byParent.has(root.id)) byParent.set(root.id, []);
    const kids = byParent.get(root.id);
    let next = kids.reduce((m, c) => Math.max(m, c.order ?? 0), -1) + 1;
    for (const n of orphans) kids.push({ ...n, order: next++ });
  }
  const toTree = (n) => ({
    id: n.id, topic: n.title,
    // Tag ※N = node có N trích đoạn nguồn — hiện provenance ngay trên canvas.
    // mindElixirToRecord bỏ qua tags nên round-trip an toàn.
    ...(n.chunkRefs?.length ? { tags: [`※ ${n.chunkRefs.length}`] } : {}),
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
    // Ưu tiên type gốc theo (source,target); arrow mới/reconnect thì suy từ nhãn
    // (LABEL_TO_TYPE) trước khi rớt về relates_to — codex #5.
    type: baseType.get(`${a.from}→${a.to}`) || LABEL_TO_TYPE[a.label] || "relates_to",
    label: a.label || "",
  }));

  return { ...baseRecord, title: mindData.nodeData.topic || baseRecord.title, nodes, relations };
}
