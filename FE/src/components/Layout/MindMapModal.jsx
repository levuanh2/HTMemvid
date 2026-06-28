import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import ReactFlow, {
  MiniMap, Controls, Background, useReactFlow, ReactFlowProvider, BaseEdge,
} from "reactflow";
import "reactflow/dist/style.css";
import ELK from "elkjs/lib/elk.bundled.js";
import { Handle, Position } from "reactflow";
import { createPortal } from "react-dom";

const elk = new ELK();

// =====================
// BRANCH COLORS
// =====================
const BRANCH_COLORS = [
  { bg: "bg-blue-50",     border: "border-blue-200",   text: "text-blue-800",    edge: "#3b82f6", badge: "bg-blue-100 text-blue-700" },
  { bg: "bg-emerald-50",  border: "border-emerald-200", text: "text-emerald-800", edge: "#10b981", badge: "bg-emerald-100 text-emerald-700" },
  { bg: "bg-violet-50",   border: "border-violet-200",  text: "text-violet-800",  edge: "#8b5cf6", badge: "bg-violet-100 text-violet-700"  },
  { bg: "bg-amber-50",   border: "border-amber-200",   text: "text-amber-800",   edge: "#f59e0b", badge: "bg-amber-100 text-amber-700"   },
  { bg: "bg-rose-50",    border: "border-rose-200",    text: "text-rose-800",    edge: "#f43f5e", badge: "bg-rose-100 text-rose-700"    },
  { bg: "bg-cyan-50",     border: "border-cyan-200",    text: "text-cyan-800",    edge: "#06b6d4", badge: "bg-cyan-100 text-cyan-700"    },
  { bg: "bg-fuchsia-50", border: "border-fuchsia-200",  text: "text-fuchsia-800", edge: "#d946ef", badge: "bg-fuchsia-100 text-fuchsia-700"  },
  { bg: "bg-teal-50",    border: "border-teal-200",    text: "text-teal-800",    edge: "#14b8a6", badge: "bg-teal-100 text-teal-700"    },
];

export const LAYOUT_OPTIONS = [
  { value: "auto",              label: "Tự động",         description: "Tự chọn bố cục" },
  { value: "presentation-map",   label: "Trình bày",      description: "Gọn đẹp, nhiều node" },
  { value: "clean-mindmap",    label: "Mindmap sạch",   description: "Giống Miro / Whimsical" },
  { value: "compact-mindmap",   label: "Mindmap gọn",   description: "Nhiều node, tiết kiệm" },
  { value: "tree-compact",      label: "Cây gọn",        description: "Root trên, con dưới" },
  { value: "visual-center",    label: "Sơ đồ trung tâm", description: "Root giữa, nhánh hai bên" },
];

const DISPLAY_MODES = [
  { value: "overview", label: "Tổng quan" },
  { value: "focus",    label: "Tập trung" },
  { value: "full",     label: "Đầy đủ" },
];

const EDGE_MODES = [
  { value: "clean",    label: "Gọn" },
  { value: "minimal",  label: "Tối giản" },
  { value: "full",    label: "Đầy đủ dây" },
  { value: "semantic", label: "Phụ" },
];

// =====================
// AUTO SELECTORS
// =====================
const getAutoLayout = (displayMode, nodeCount) => {
  if (displayMode === "overview") return "presentation-map";
  if (nodeCount > 45) return "compact-mindmap";
  return "clean-mindmap";
};

const getAutoDisplayMode = (nodeCount) => {
  if (nodeCount > 35) return "overview";
  return "full";
};

const getAutoEdgeMode = (nodeCount) => {
  return nodeCount > 35 ? "clean" : "full";
};

// =====================
// CUSTOM HOOKS
// =====================
const useIsMobile = (breakpoint = 768) => {
  const [isMobile, setIsMobile] = useState(() => typeof window === "undefined" ? false : window.innerWidth < breakpoint);
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < breakpoint);
    window.addEventListener("resize", handler, { passive: true });
    return () => window.removeEventListener("resize", handler);
  }, [breakpoint]);
  return isMobile;
};

// =====================
// TREE HELPERS
// =====================
const buildChildrenMap = (nodes) => {
  const map = new Map();
  (nodes || []).forEach((n) => {
    const parent = n.parent ?? null;
    if (!map.has(parent)) map.set(parent, []);
    map.get(parent).push(n.id);
  });
  return map;
};

const buildParentMap = (nodes) => {
  const map = new Map();
  (nodes || []).forEach((n) => { if (n.parent != null) map.set(n.id, n.parent); });
  return map;
};

const getSubtreeSize = (nodeId, childrenMap) => {
  const children = childrenMap.get(nodeId) || [];
  if (children.length === 0) return 1;
  return 1 + children.reduce((s, c) => s + getSubtreeSize(c, childrenMap), 0);
};

const getVisibleNodeIds = (nodes, childrenMap, expandedNodes, rootNode) => {
  if (!rootNode) return new Set(nodes.map((n) => n.id));
  const visible = new Set([rootNode.id]);
  const walk = (id) => {
    (childrenMap.get(id) || []).forEach((childId) => {
      if (expandedNodes.has(id)) { visible.add(childId); walk(childId); }
    });
  };
  walk(rootNode.id);
  return visible;
};

const getAncestorIds = (nodeId, parentMap) => {
  const result = new Set();
  let current = nodeId;
  while (parentMap.has(current)) {
    const parent = parentMap.get(current);
    if (!parent) break;
    result.add(parent);
    current = parent;
  }
  return result;
};

const getDescendantIdsLimited = (nodeId, childrenMap, maxDepth = 2) => {
  const result = new Set();
  const walk = (id, depth) => {
    if (depth > maxDepth) return;
    (childrenMap.get(id) || []).forEach((childId) => { result.add(childId); walk(childId, depth + 1); });
  };
  walk(nodeId, 1);
  return result;
};

const getOverviewNodeIds = ({ nodes, root, childrenMap }) => {
  if (!root) return new Set(nodes.map((n) => n.id));
  const visible = new Set([root.id]);
  const rootChildren = childrenMap.get(root.id) || [];
  rootChildren.forEach((branchId) => {
    visible.add(branchId);
    const branchChildren = childrenMap.get(branchId) || [];
    branchChildren.slice(0, 7).forEach((childId) => {
      visible.add(childId);
      const grandchildren = childrenMap.get(childId) || [];
      grandchildren.slice(0, 3).forEach((g) => visible.add(g));
    });
  });
  return visible;
};

const getFocusNodeIds = ({ focusedNodeId, root, parentMap, childrenMap }) => {
  if (!focusedNodeId || !root) return new Set();
  const visible = new Set([root.id]);
  visible.add(focusedNodeId);
  getAncestorIds(focusedNodeId, parentMap).forEach((id) => visible.add(id));
  getDescendantIdsLimited(focusedNodeId, childrenMap, 2).forEach((id) => visible.add(id));
  return visible;
};

// =====================
// BRANCH COLOR
// =====================
const assignBranchColors = (nodes, rootId, childrenMap) => {
  if (!nodes.length) return nodes;
  const branchIndexMap = new Map();
  const rootChildren = childrenMap.get(rootId) || [];
  rootChildren.forEach((childId, idx) => branchIndexMap.set(childId, idx % BRANCH_COLORS.length));

  const getBranchIndex = (nodeId) => {
    if (nodeId === rootId) return -1;
    let current = nodeId;
    while (current !== rootId && current != null) {
      const parent = nodes.find((n) => n.id === current)?.parent;
      if (parent === rootId) return branchIndexMap.get(current) ?? 0;
      current = parent;
    }
    return 0;
  };

  return nodes.map((n) => ({
    ...n,
    branchIndex: getBranchIndex(n.id),
    branchColor: BRANCH_COLORS[getBranchIndex(n.id)] || BRANCH_COLORS[0],
  }));
};

// =====================
// DATA NORMALIZATION
// =====================
const normalizeHierarchyFromData = (data) => {
  const flatNodes = Array.isArray(data?.nodes) ? data.nodes : [];
  const diagramNodes = Array.isArray(data?.diagram?.nodes) ? data.diagram.nodes : [];
  const diagramMap = new Map(diagramNodes.map((n) => [String(n.id), n]));

  let unifiedNodes;
  if (flatNodes.length > 0) {
    unifiedNodes = flatNodes.filter((n) => n && n.id).map((n, index) => {
      const id = String(n.id);
      const extra = diagramMap.get(id) || {};
      return {
        id, parent: n.parent == null ? null : String(n.parent),
        title: extra.title || n.title || `Node ${index + 1}`,
        subtitle: extra.subtitle || "",
        type: extra.type || (n.parent == null ? "root" : "concept"),
        group: extra.group || "other",
        level: Number.isFinite(Number(extra.level)) ? Number(extra.level) : (n.parent == null ? 0 : 1),
        icon: extra.icon || (n.parent == null ? "brain" : "lightbulb"),
        order: Number.isFinite(Number(extra.order)) ? Number(extra.order) : index,
      };
    });
  } else if (diagramNodes.length > 0) {
    unifiedNodes = diagramNodes.filter((n) => n && n.id).map((n, index) => ({
      id: String(n.id), parent: n.parent == null ? null : String(n.parent),
      title: n.title || `Node ${index + 1}`,
      subtitle: n.subtitle || "",
      type: n.type || (index === 0 ? "root" : "concept"),
      group: n.group || "other",
      level: Number.isFinite(Number(n.level)) ? Number(n.level) : index === 0 ? 0 : 1,
      icon: n.icon || (index === 0 ? "brain" : "lightbulb"),
      order: Number.isFinite(Number(n.order)) ? Number(n.order) : index,
    }));
  } else {
    return { diagramType: "concept_map", title: data?.title || "Sơ đồ tư duy", summary: "", nodes: [], treeEdges: [], semanticEdges: [] };
  }

  const parentMap = {};
  unifiedNodes.forEach((n) => { if (n.parent) parentMap[n.id] = n.parent; });
  const getDepth = (id, visited = new Set()) => {
    if (visited.has(id)) return 0;
    visited.add(id);
    if (!parentMap[id]) return 0;
    return 1 + getDepth(parentMap[id], visited);
  };
  unifiedNodes = unifiedNodes.map((n) => ({ ...n, level: getDepth(n.id) }));

  const nodeIds = new Set(unifiedNodes.map((n) => n.id));
  const treeEdges = unifiedNodes
    .filter((n) => n.parent && nodeIds.has(n.parent))
    .map((n, index) => ({ id: `tree-${index}-${n.parent}-${n.id}`, source: n.parent, target: n.id, isSemantic: false, relationType: "tree" }));

  if (treeEdges.length === 0 && unifiedNodes.length > 1) {
    const root = unifiedNodes.find((n) => n.parent == null || n.type === "root") || unifiedNodes[0];
    unifiedNodes.filter((n) => n.id !== root.id).forEach((n, i) => {
      treeEdges.push({ id: `star-${i}-${root.id}-${n.id}`, source: root.id, target: n.id, isSemantic: false, relationType: "star" });
    });
  }

  const diagramEdges = Array.isArray(data?.diagram?.edges) ? data.diagram.edges : [];
  const seen = new Set();
  const semanticEdges = (diagramEdges || [])
    .filter((e) => e?.source && e?.target)
    .map((e, index) => ({ id: `sem-${index}-${e.source}-${e.target}`, source: String(e.source), target: String(e.target), isSemantic: true, relationType: "semantic" }))
    .filter((e) => {
      if (!nodeIds.has(e.source) || !nodeIds.has(e.target)) return false;
      if (e.source === e.target) return false;
      const k = `${e.source}->${e.target}`;
      if (seen.has(k)) return false;
      seen.add(k); return true;
    })
    .slice(0, 12);

  return { diagramType: data?.diagram?.diagramType || "concept_map", title: data?.diagram?.title || data?.title || "Sơ đồ tư duy", summary: data?.diagram?.summary || "", nodes: unifiedNodes, treeEdges, semanticEdges };
};

// =====================
// GEOMETRY
// =====================
const getNodeBox = (node) => {
  const x = node.position?.x ?? 0;
  const y = node.position?.y ?? 0;

  const w =
    node.measured?.width ??
    node.width ??
    (typeof node.style?.width === "number" ? node.style.width : null) ??
    (typeof node.style?.minWidth === "number" ? node.style.minWidth : null) ??
    220;

  const h =
    node.measured?.height ??
    node.height ??
    (typeof node.style?.height === "number" ? node.style.height : null) ??
    (typeof node.style?.minHeight === "number" ? node.style.minHeight : null) ??
    90;

  const width = Number(w) || 220;
  const height = Number(h) || 90;

  return {
    id: String(node.id),
    x,
    y,
    width,
    height,
    left: x,
    right: x + width,
    top: y,
    bottom: y + height,
    cx: x + width / 2,
    cy: y + height / 2,
  };
};

const getGraphBounds = (nodes) => {
  if (!nodes?.length) return { left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, cx: 0, cy: 0 };
  const boxes = nodes.map(getNodeBox);
  const left = Math.min(...boxes.map((b) => b.left)), right = Math.max(...boxes.map((b) => b.right));
  const top = Math.min(...boxes.map((b) => b.top)), bottom = Math.max(...boxes.map((b) => b.bottom));
  return { left, right, top, bottom, width: right - left, height: bottom - top, cx: (left + right) / 2, cy: (top + bottom) / 2 };
};

// =====================
// NODE OVERLAP VALIDATOR
// =====================
const boxesOverlap = (a, b, padding = 24) => {
  return !(
    a.right + padding < b.left ||
    a.left - padding > b.right ||
    a.bottom + padding < b.top ||
    a.top - padding > b.bottom
  );
};

const countNodeOverlaps = (nodes, padding = 24) => {
  const boxes = nodes.map(getNodeBox);
  let count = 0;
  const pairs = [];
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      if (boxesOverlap(boxes[i], boxes[j], padding)) {
        count += 1;
        pairs.push([boxes[i].id, boxes[j].id]);
      }
    }
  }
  return { count, pairs };
};

const translateNodes = (nodes, dx, dy) => nodes.map((node) => ({
  ...node,
  position: { x: Math.round((node.position?.x ?? 0) + dx), y: Math.round((node.position?.y ?? 0) + dy) },
}));

const getRootNodeFromPositioned = (nodes, rootId) => nodes.find((n) => String(n.id) === String(rootId)) || nodes[0] || null;

const getSideVector = (side) => {
  switch (side) {
    case "left":    return { x: -1, y:  0 };
    case "right":   return { x:  1, y:  0 };
    case "top":     return { x:  0, y: -1 };
    case "bottom":  return { x:  0, y:  1 };
    default:        return { x:  1, y:  0 };
  }
};

const getSourceHandleId = (side) => `${side}-source`;
const getTargetHandleId = (side) => side;

const getNodeCenter = (node) => {
  const x = node.position?.x ?? 0, y = node.position?.y ?? 0;
  const w = node.width ?? 220, h = node.height ?? 90;
  return { x: x + w / 2, y: y + h / 2 };
};

const getHandlePoint = (node, side) => {
  const x = node.position?.x ?? 0, y = node.position?.y ?? 0;
  const w = node.width ?? 220, h = node.height ?? 90;
  switch (side) {
    case "left":   return { x,            y: y + h / 2 };
    case "right":  return { x: x + w,    y: y + h / 2 };
    case "top":    return { x: x + w / 2, y };
    case "bottom": return { x: x + w / 2, y: y + h };
    default:       return { x: x + w,    y: y + h / 2 };
  }
};

const getNodeRect = (node, padding = 30) => {
  const x = node.position?.x ?? 0, y = node.position?.y ?? 0;
  const w = node.width ?? 220, h = node.height ?? 90;
  return { id: String(node.id), left: x - padding, right: x + w + padding, top: y - padding, bottom: y + h + padding };
};

// =====================
// OBSTACLE DETECTION
// =====================
const pointInRect = (p, r) => p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom;
const orientation = (a, b, c) => {
  const val = (b.y - a.y) * (c.x - b.x) - (b.x - a.x) * (c.y - b.y);
  if (Math.abs(val) < 0.0001) return 0;
  return val > 0 ? 1 : 2;
};
const onSegment = (a, b, c) => b.x <= Math.max(a.x, c.x) && b.x >= Math.min(a.x, c.x) && b.y <= Math.max(a.y, c.y) && b.y >= Math.min(a.y, c.y);
const segmentsIntersect = (p1, q1, p2, q2) => {
  const o1 = orientation(p1, q1, p2), o2 = orientation(p1, q1, q2), o3 = orientation(p2, q2, p1), o4 = orientation(p2, q2, q1);
  if (o1 !== o2 && o3 !== o4) return true;
  if (o1 === 0 && onSegment(p1, p2, q1)) return true;
  if (o2 === 0 && onSegment(p1, q2, q1)) return true;
  if (o3 === 0 && onSegment(p2, p1, q2)) return true;
  if (o4 === 0 && onSegment(p2, q1, q2)) return true;
  return false;
};
const segmentIntersectsRect = (p1, p2, r) => {
  if (pointInRect(p1, r) || pointInRect(p2, r)) return true;
  const tl = { x: r.left, y: r.top }, tr = { x: r.right, y: r.top };
  const bl = { x: r.left, y: r.bottom }, br = { x: r.right, y: r.bottom };
  return segmentsIntersect(p1, p2, tl, tr) || segmentsIntersect(p1, p2, tr, br) || segmentsIntersect(p1, p2, br, bl) || segmentsIntersect(p1, p2, bl, tl);
};
const pathIntersectsNodeRects = (points, obstacleRects, sourceId, targetId) => {
  let hits = 0;
  const sid = String(sourceId), tid = String(targetId);
  for (let i = 0; i < points.length - 1; i++) {
    for (const rect of obstacleRects) {
      if (rect.id === sid || rect.id === tid) continue;
      if (segmentIntersectsRect(points[i], points[i + 1], rect)) hits++;
    }
  }
  return hits;
};

// =====================
// EDGE-EDGE COLLISION
// =====================
const normalizeSegment = (a, b) => {
  const ax = Math.round(a.x / 22) * 22, ay = Math.round(a.y / 22) * 22;
  const bx = Math.round(b.x / 22) * 22, by = Math.round(b.y / 22) * 22;
  const k1 = `${ax},${ay}-${bx},${by}`, k2 = `${bx},${by}-${ax},${ay}`;
  return k1 < k2 ? k1 : k2;
};
const countSharedSegments = (points, usedSegments) => {
  let count = 0;
  for (let i = 0; i < points.length - 1; i++) { const key = normalizeSegment(points[i], points[i + 1]); count += usedSegments.get(key) || 0; }
  return count;
};
const registerRouteSegments = (points, usedSegments) => {
  for (let i = 0; i < points.length - 1; i++) { const key = normalizeSegment(points[i], points[i + 1]); usedSegments.set(key, (usedSegments.get(key) || 0) + 1); }
};
const getManhattanLength = (points) => {
  let len = 0;
  for (let i = 0; i < points.length - 1; i++) len += Math.abs(points[i + 1].x - points[i].x) + Math.abs(points[i + 1].y - points[i].y);
  return len;
};

// =====================
// HANDLE CANDIDATES
// =====================
const getHandleCandidates = (sourceNode, targetNode, layoutType) => {
  const sc = getNodeCenter(sourceNode), tc = getNodeCenter(targetNode);
  const dx = tc.x - sc.x, dy = tc.y - sc.y;
  const candidates = [];
  if (Math.abs(dx) >= Math.abs(dy)) { candidates.push(dx >= 0 ? { sourceSide: "right", targetSide: "left" } : { sourceSide: "left", targetSide: "right" }); }
  else { candidates.push(dy >= 0 ? { sourceSide: "bottom", targetSide: "top" } : { sourceSide: "top", targetSide: "bottom" }); }
  if (layoutType === "clean-mindmap" || layoutType === "presentation-map") {
    candidates.push({ sourceSide: "right", targetSide: "left" }, { sourceSide: "left", targetSide: "right" });
    candidates.push({ sourceSide: "bottom", targetSide: "top" }, { sourceSide: "top", targetSide: "bottom" });
  }
  if (layoutType === "compact-mindmap" || layoutType === "tree-compact") {
    candidates.push({ sourceSide: "left", targetSide: "right" }, { sourceSide: "top", targetSide: "bottom" });
    candidates.push({ sourceSide: "bottom", targetSide: "top" });
  }
  candidates.push({ sourceSide: "right", targetSide: "left" }, { sourceSide: "left", targetSide: "right" });
  candidates.push({ sourceSide: "bottom", targetSide: "top" }, { sourceSide: "top", targetSide: "bottom" });
  const seen = new Set();
  return candidates.filter((c) => { const key = `${c.sourceSide}-${c.targetSide}`; if (seen.has(key)) return false; seen.add(key); return true; });
};

// =====================
// ROUTE CANDIDATES
// =====================
const makeRouteCandidates = ({ sourcePoint, targetPoint, sourceSide, targetSide, busGap = 100, laneIndex = 0 }) => {
  const sv = getSideVector(sourceSide), tv = getSideVector(targetSide);
  const exitGap = 36, entryGap = 36;
  const start = sourcePoint, end = targetPoint;
  const s1 = { x: start.x + sv.x * exitGap, y: start.y + sv.y * exitGap };
  const t1 = { x: end.x   + tv.x * entryGap, y: end.y   + tv.y * entryGap };
  const minX = Math.min(s1.x, t1.x), maxX = Math.max(s1.x, t1.x);
  const minY = Math.min(s1.y, t1.y), maxY = Math.max(s1.y, t1.y);
  const laneOffset = ((laneIndex % 9) - 4) * 24;
  const midX = (s1.x + t1.x) / 2 + laneOffset, midY = (s1.y + t1.y) / 2 + laneOffset;
  const leftBus   = minX - busGap - Math.abs(laneOffset);
  const rightBus  = maxX + busGap + Math.abs(laneOffset);
  const topBus    = minY - busGap - Math.abs(laneOffset);
  const bottomBus = maxY + busGap + Math.abs(laneOffset);
  return [
    [start, s1, { x: t1.x, y: s1.y }, t1, end],
    [start, s1, { x: s1.x, y: t1.y }, t1, end],
    [start, s1, { x: midX, y: s1.y }, { x: midX, y: t1.y }, t1, end],
    [start, s1, { x: s1.x, y: midY }, { x: t1.x, y: midY }, t1, end],
    [start, s1, { x: leftBus,   y: s1.y }, { x: leftBus,   y: t1.y }, t1, end],
    [start, s1, { x: rightBus,  y: s1.y }, { x: rightBus,  y: t1.y }, t1, end],
    [start, s1, { x: s1.x, y: topBus },    { x: t1.x, y: topBus },    t1, end],
    [start, s1, { x: s1.x, y: bottomBus }, { x: t1.x, y: bottomBus }, t1, end],
  ];
};

// =====================
// SCORING
// =====================
const scoreRoute = ({ points, obstacleRects, sourceId, targetId, usedSegments }) => {
  const nodeHits = pathIntersectsNodeRects(points, obstacleRects, sourceId, targetId);
  const shared = countSharedSegments(points, usedSegments);
  const length = getManhattanLength(points);
  const bends  = Math.max(0, points.length - 2);
  return nodeHits * 10000000 + shared * 50000 + bends * 1000 + length * 2;
};

const pickBestHandleAndRoute = ({ sourceNode, targetNode, layoutType, obstacleRects, usedSegments, laneIndex }) => {
  const handlePairs = getHandleCandidates(sourceNode, targetNode, layoutType);
  let best = null, bestScore = Infinity;
  for (const pair of handlePairs) {
    const sp = getRawHandlePoint(sourceNode, pair.sourceSide);
    const tp = getRawHandlePoint(targetNode, pair.targetSide);
    const candidates = makeRouteCandidates({ sourcePoint: sp, targetPoint: tp, sourceSide: pair.sourceSide, targetSide: pair.targetSide, busGap: 100, laneIndex });
    for (const pts of candidates) {
      if (!pts || pts.length < 2) continue;
      const s = scoreRoute({ points: pts, obstacleRects, sourceId: String(sourceNode.id), targetId: String(targetNode.id), usedSegments });
      if (s < bestScore) { bestScore = s; best = { sourceSide: pair.sourceSide, targetSide: pair.targetSide, points: pts, score: s }; }
    }
  }
  return best;
};

// =====================
// PATH BUILDERS
// =====================
const roundedPolylinePath = (points, radius = 16) => {
  if (!points || points.length < 2) return "";

  const safePoints = cleanRoutePoints(points);
  if (!safePoints || safePoints.length < 2) return "";

  let path = `M ${safePoints[0].x} ${safePoints[0].y}`;

  for (let i = 1; i < safePoints.length; i++) {
    const prev = safePoints[i - 1];
    const curr = safePoints[i];
    const next = safePoints[i + 1];

    if (!next) {
      path += ` L ${curr.x} ${curr.y}`;
      continue;
    }

    const dx1 = curr.x - prev.x;
    const dy1 = curr.y - prev.y;
    const dx2 = next.x - curr.x;
    const dy2 = next.y - curr.y;

    const len1 = Math.hypot(dx1, dy1);
    const len2 = Math.hypot(dx2, dy2);

    const nearEndpoint = i === 1 || i === safePoints.length - 2;

    if (nearEndpoint || len1 < radius * 2 || len2 < radius * 2) {
      path += ` L ${curr.x} ${curr.y}`;
      continue;
    }

    const r = Math.min(radius, len1 / 2, len2 / 2);

    const p1 = {
      x: curr.x - (dx1 / len1) * r,
      y: curr.y - (dy1 / len1) * r,
    };

    const p2 = {
      x: curr.x + (dx2 / len2) * r,
      y: curr.y + (dy2 / len2) * r,
    };

    path += ` L ${p1.x} ${p1.y} Q ${curr.x} ${curr.y} ${p2.x} ${p2.y}`;
  }

  return path;
};

// =====================
// PROFESSIONAL CLEAN CURVE (fixed endpoints)
// =====================
const CleanCurveEdge = ({ id, data, style, markerEnd }) => {
  const points = cleanRoutePoints(data?.routePoints);
  if (!points || points.length < 2) return null;

  const start = points[0];
  const end = points[points.length - 1];
  const sourceSide = data?.sourceSide || "right";
  const targetSide = data?.targetSide || "left";
  const sv = getSideVector(sourceSide), tv = getSideVector(targetSide);

  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const dist = Math.sqrt(dx * dx + dy * dy);

  const curve = Math.max(70, Math.min(dist * 0.36, 220));

  const c1 = { x: start.x + sv.x * curve, y: start.y + sv.y * curve };
  const c2 = { x: end.x + tv.x * curve, y: end.y + tv.y * curve };

  const path = `M ${start.x} ${start.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${end.x} ${end.y}`;

  const branchColor = data?.branchColor || "#94a3b8";
  const edgeStyle = data?.edgeStyle || {};

  return (
    <BaseEdge
      id={id}
      path={path}
      markerEnd={markerEnd}
      style={{ fill: "none", stroke: branchColor, strokeWidth: edgeStyle.strokeWidth || 1.5, opacity: edgeStyle.opacity || 0.5, strokeLinecap: "round", strokeLinejoin: "round", ...style }}
    />
  );
};

const SmartOrthogonalEdge = ({ id, data, style, markerEnd }) => {
  const points = cleanRoutePoints(data?.routePoints);
  if (!points || points.length < 2) return null;
  const path = roundedPolylinePath(points, 16);
  const branchColor = data?.branchColor || "#94a3b8";
  const edgeStyle = data?.edgeStyle || {};
  return (
    <BaseEdge
      id={id}
      path={path}
      markerEnd={markerEnd}
      style={{ fill: "none", stroke: branchColor, strokeWidth: edgeStyle.strokeWidth || 1.5, opacity: edgeStyle.opacity || 0.5, strokeDasharray: edgeStyle.strokeDasharray || "none", strokeLinecap: "round", strokeLinejoin: "round", ...style }}
    />
  );
};

// =====================
// PROFESSIONAL EDGE STYLE (updated)
// =====================
const getProfessionalEdgeStyle = ({ edge, sourceNode, targetNode, edgeMode, hoveredNodeId, focusedNodeId }) => {
  return getProfessionalEdgeVisual({
    edge,
    sourceNode,
    targetNode,
    edgeMode,
    hoveredNodeId,
    focusedNodeId,
  });
};

// =====================
// EDGE VISIBILITY HELPER
// =====================
const shouldShowTreeEdge = ({
  edge,
  edgeMode,
  totalNodes,
  targetDepth,
  hoveredNodeId,
  focusedNodeId,
}) => {
  const relatedToHover =
    hoveredNodeId &&
    (String(edge.source) === String(hoveredNodeId) ||
     String(edge.target) === String(hoveredNodeId));

  const relatedToFocus =
    focusedNodeId &&
    (String(edge.source) === String(focusedNodeId) ||
     String(edge.target) === String(focusedNodeId));

  if (edgeMode === "minimal") {
    return targetDepth <= 1 || relatedToHover || relatedToFocus;
  }

  if (edgeMode === "clean") {
    if (totalNodes > 35) {
      return targetDepth <= 2 || relatedToHover || relatedToFocus;
    }
    return targetDepth <= 3 || relatedToHover || relatedToFocus;
  }

  if (edgeMode === "full") {
    return true;
  }

  return targetDepth <= 2;
};

// =====================
// PROFESSIONAL EDGE VISUAL (brighter)
// =====================
const getProfessionalEdgeVisual = ({
  edge,
  sourceNode,
  targetNode,
  edgeMode,
  hoveredNodeId,
  focusedNodeId,
}) => {
  const depth = targetNode?.data?.depth ?? targetNode?.data?.level ?? 1;
  const branchColor =
    targetNode?.data?.branchColor?.edge ||
    sourceNode?.data?.branchColor?.edge ||
    "#64748b";

  const isSemantic = Boolean(edge.isSemantic || edge.data?.isSemantic);

  const isHoverRelated =
    hoveredNodeId &&
    (String(edge.source) === String(hoveredNodeId) ||
      String(edge.target) === String(hoveredNodeId));

  const isFocusRelated =
    focusedNodeId &&
    (String(edge.source) === String(focusedNodeId) ||
      String(edge.target) === String(focusedNodeId));

  let strokeWidth = 1.6;
  let opacity = 0.58;
  let strokeDasharray = undefined;

  if (depth <= 1) {
    strokeWidth = 2.35;
    opacity = 0.82;
  } else if (depth === 2) {
    strokeWidth = 1.8;
    opacity = 0.68;
  } else if (depth === 3) {
    strokeWidth = 1.45;
    opacity = edgeMode === "full" ? 0.46 : 0.36;
  } else {
    strokeWidth = 1.2;
    opacity = edgeMode === "full" ? 0.34 : 0.24;
  }

  if (isSemantic) {
    strokeWidth = 1.1;
    opacity = 0.2;
    strokeDasharray = "6 6";
  }

  if (isHoverRelated || isFocusRelated) {
    strokeWidth += 0.65;
    opacity = 0.95;
  } else if (hoveredNodeId || focusedNodeId) {
    opacity *= 0.35;
  }

  return {
    stroke: branchColor,
    strokeWidth,
    opacity,
    strokeDasharray,
    strokeLinecap: "round",
    strokeLinejoin: "round",
    fill: "none",
  };
};

// =====================
// EDGE HELPERS
// =====================
const sanitizeEdges = (edges, nodeMap) => {
  const seen = new Set();
  return edges.filter((edge) => {
    const source = String(edge.source), target = String(edge.target);
    if (!nodeMap.has(source) || !nodeMap.has(target)) return false;
    if (source === target) return false;
    const key = `${source}->${target}`;
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
};

const getRawHandlePoint = (node, side, offset = { dx: 0, dy: 0 }) => {
  const box = getNodeBox(node);
  let point;
  switch (side) {
    case "left":   point = { x: box.left, y: box.cy }; break;
    case "right":  point = { x: box.right, y: box.cy }; break;
    case "top":    point = { x: box.cx, y: box.top }; break;
    case "bottom": point = { x: box.cx, y: box.bottom }; break;
    default:       point = { x: box.right, y: box.cy };
  }
  return { x: point.x + (offset.dx || 0), y: point.y + (offset.dy || 0) };
};

const getExitPointFromHandle = (handlePoint, side, gap = 18) => {
  const v = getSideVector(side);
  return { x: handlePoint.x + v.x * gap, y: handlePoint.y + v.y * gap };
};

const isFinitePoint = (p) => p && Number.isFinite(p.x) && Number.isFinite(p.y);

const samePoint = (a, b, epsilon = 0.5) => Math.abs(a.x - b.x) <= epsilon && Math.abs(a.y - b.y) <= epsilon;

const isCollinear = (a, b, c, epsilon = 0.01) => {
  const area = Math.abs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x));
  return area <= epsilon;
};

const cleanRoutePoints = (points) => {
  const valid = (points || []).filter(isFinitePoint);
  if (valid.length < 2) return valid;

  const deduped = [];
  valid.forEach((p, index) => {
    const prev = deduped[deduped.length - 1];

    const preserve =
      index === 0 ||
      index === 1 ||
      index === valid.length - 2 ||
      index === valid.length - 1;

    if (preserve) {
      if (!prev || !samePoint(prev, p)) deduped.push(p);
      return;
    }

    if (!prev || !samePoint(prev, p)) deduped.push(p);
  });

  if (deduped.length <= 4) return deduped;

  const cleaned = [deduped[0]];

  for (let i = 1; i < deduped.length - 1; i++) {
    const prev = cleaned[cleaned.length - 1];
    const curr = deduped[i];
    const next = deduped[i + 1];

    const preserve =
      i === 1 ||
      i === deduped.length - 2;

    if (preserve || !isCollinear(prev, curr, next)) {
      cleaned.push(curr);
    }
  }

  const finalLast = deduped[deduped.length - 1];

  if (!samePoint(cleaned[cleaned.length - 1], finalLast)) {
    cleaned.push(finalLast);
  }

  return cleaned;
};

const sanitizeRawEdges = (edges, positionedNodes) => {
  const nodeIds = new Set(positionedNodes.map((n) => String(n.id)));
  const seen = new Set();
  return (edges || []).filter((edge) => {
    const source = String(edge.source), target = String(edge.target);
    if (!source || !target) return false;
    if (!nodeIds.has(source)) return false;
    if (!nodeIds.has(target)) return false;
    if (source === target) return false;
    const key = `${source}->${target}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

const getRootToBranchHandle = (rootNode, branchNode) => {
  const rb = getNodeBox(rootNode), bb = getNodeBox(branchNode);
  const dx = bb.cx - rb.cx, dy = bb.cy - rb.cy;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? { sourceSide: "right", targetSide: "left" } : { sourceSide: "left", targetSide: "right" };
  return dy >= 0 ? { sourceSide: "bottom", targetSide: "top" } : { sourceSide: "top", targetSide: "bottom" };
};

const getNaturalHandlePair = (sourceNode, targetNode) => {
  const sc = getNodeCenter(sourceNode), tc = getNodeCenter(targetNode);
  const dx = tc.x - sc.x, dy = tc.y - sc.y;
  if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? { sourceSide: "right", targetSide: "left" } : { sourceSide: "left", targetSide: "right" };
  return dy >= 0 ? { sourceSide: "bottom", targetSide: "top" } : { sourceSide: "top", targetSide: "bottom" };
};

// =====================
// BEST HANDLE + EXPANDED HANDLE + PORT OFFSET
// =====================
const getBestHandlePair = (sourceNode, targetNode) => {
  const s = getNodeBox(sourceNode), t = getNodeBox(targetNode);
  const dx = t.cx - s.cx, dy = t.cy - s.cy;
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0
      ? { sourceSide: "right", targetSide: "left" }
      : { sourceSide: "left", targetSide: "right" };
  }
  return dy >= 0
    ? { sourceSide: "bottom", targetSide: "top" }
    : { sourceSide: "top", targetSide: "bottom" };
};

const getExpandedHandlePoint = (node, side, gap = 12) => {
  const box = getNodeBox(node);
  switch (side) {
    case "left":   return { x: box.left - gap, y: box.cy };
    case "right":  return { x: box.right + gap, y: box.cy };
    case "top":    return { x: box.cx, y: box.top - gap };
    case "bottom": return { x: box.cx, y: box.bottom + gap };
    default:       return { x: box.right + gap, y: box.cy };
  }
};

const buildRouteWithAttachedEndpoints = ({
  sourceNode,
  targetNode,
  sourceSide,
  targetSide,
  middlePoints = [],
  gap = 18,
}) => {
  const sourceHandle = getRawHandlePoint(sourceNode, sourceSide);
  const targetHandle = getRawHandlePoint(targetNode, targetSide);

  const sourceExit = getExitPointFromHandle(sourceHandle, sourceSide, gap);
  const targetEntry = getExitPointFromHandle(targetHandle, targetSide, gap);

  return cleanRoutePoints([
    sourceHandle,
    sourceExit,
    ...middlePoints,
    targetEntry,
    targetHandle,
  ]);
};

const getPortOffset = ({ siblingIndex = 0, siblingCount = 1, side }) => {
  const spread = 16;
  const centered = siblingIndex - (siblingCount - 1) / 2;
  const offset = centered * spread;
  if (side === "left" || side === "right") {
    return { dx: 0, dy: offset };
  }
  return { dx: offset, dy: 0 };
};

// =====================
// BRANCH META
// =====================
const assignBranchMeta = ({ nodes, rootId, childrenMap }) => {
  const rootChildren = childrenMap.get(String(rootId)) || [];
  const branchByNodeId = new Map();
  rootChildren.forEach((branchId, index) => {
    const branchKey = String(branchId);
    const walk = (id) => {
      branchByNodeId.set(String(id), { branchId: branchKey, branchIndex: index });
      (childrenMap.get(String(id)) || []).forEach(walk);
    };
    walk(branchKey);
  });
  branchByNodeId.set(String(rootId), { branchId: String(rootId), branchIndex: -1 });
  return nodes.map((node) => ({
    ...node,
    data: { ...node.data, branchId: branchByNodeId.get(String(node.id))?.branchId, branchIndex: branchByNodeId.get(String(node.id))?.branchIndex },
  }));
};

// =====================
// BRANCH LANE
// =====================
const getBranchLane = ({ branchNode, rootNode, branchIndex }) => {
  const rootBox = getNodeBox(rootNode), branchBox = getNodeBox(branchNode);
  const side = branchBox.cx < rootBox.cx ? "left" : branchBox.cx > rootBox.cx ? "right" : branchBox.cy < rootBox.cy ? "top" : "bottom";
  const laneGap = 44 + (branchIndex % 4) * 12;
  if (side === "left")  return { side, x: Math.min(rootBox.left, branchBox.left) - laneGap, y: null };
  if (side === "right") return { side, x: Math.max(rootBox.right, branchBox.right) + laneGap, y: null };
  if (side === "top")   return { side, x: null, y: Math.min(rootBox.top, branchBox.top) - laneGap };
  return { side, x: null, y: Math.max(rootBox.bottom, branchBox.bottom) + laneGap };
};

// =====================
// BUNDLED EDGE ROUTING
// =====================
const routeBundledEdge = ({ sourceNode, targetNode, rootNode, branchLane, depth }) => {
  const sourceBox = getNodeBox(sourceNode), targetBox = getNodeBox(targetNode);

  if (String(sourceNode.id) === String(rootNode.id) || depth <= 1) {
    const handles = getRootToBranchHandle(rootNode, targetNode);
    return {
      type: "cleanCurve",
      sourceSide: handles.sourceSide,
      targetSide: handles.targetSide,
      points: [getRawHandlePoint(sourceNode, handles.sourceSide), getRawHandlePoint(targetNode, handles.targetSide)],
    };
  }

  const natural = getNaturalHandlePair(sourceNode, targetNode);
  const sourceHandle = getRawHandlePoint(sourceNode, natural.sourceSide);
  const targetHandle = getRawHandlePoint(targetNode, natural.targetSide);
  const dx = Math.abs(targetBox.cx - sourceBox.cx);
  const dy = Math.abs(targetBox.cy - sourceBox.cy);

  const manhattan = Math.abs(targetHandle.x - sourceHandle.x) + Math.abs(targetHandle.y - sourceHandle.y);

  if (dx < 420 && dy < 220) {
    return { type: "cleanCurve", sourceSide: natural.sourceSide, targetSide: natural.targetSide, points: [sourceHandle, targetHandle] };
  }

  if (manhattan < 360) {
    return {
      type: "cleanCurve",
      sourceSide: natural.sourceSide,
      targetSide: natural.targetSide,
      points: [sourceHandle, targetHandle],
    };
  }

  const sourceExit = getExitPointFromHandle(sourceHandle, natural.sourceSide, 18);
  const targetEntry = getExitPointFromHandle(targetHandle, natural.targetSide, 18);

  if (branchLane.side === "left" || branchLane.side === "right") {
    const laneX = branchLane.x;
    return {
      type: "smartOrthogonal",
      sourceSide: natural.sourceSide,
      targetSide: natural.targetSide,
      points: cleanRoutePoints([sourceHandle, sourceExit, { x: laneX, y: sourceExit.y }, { x: laneX, y: targetEntry.y }, targetEntry, targetHandle]),
    };
  }

  const laneY = branchLane.y;
  return {
    type: "smartOrthogonal",
    sourceSide: natural.sourceSide,
    targetSide: natural.targetSide,
    points: cleanRoutePoints([sourceHandle, sourceExit, { x: sourceExit.x, y: laneY }, { x: targetEntry.x, y: laneY }, targetEntry, targetHandle]),
  };
};

// =====================
// BUNDLED EDGE CREATION
// =====================
const createBundledMindmapEdges = ({ rawEdges, positionedNodes, rootId, childrenMap, edgeMode, hoveredNodeId }) => {
  const nodeMap = new Map(positionedNodes.map((n) => [String(n.id), n]));
  const rootNode = nodeMap.get(String(rootId));
  if (!rootNode) return [];

  const rootChildren = childrenMap.get(String(rootId)) || [];
  const branchLanes = new Map();
  rootChildren.forEach((branchId, index) => {
    const branchNode = nodeMap.get(String(branchId));
    if (branchNode) branchLanes.set(String(branchId), getBranchLane({ branchNode, rootNode, branchIndex: index }));
  });

  const getNodeDepth = (id) => nodeMap.get(String(id))?.data?.depth ?? nodeMap.get(String(id))?.level ?? 0;

  const visibleEdges = rawEdges.filter((edge) => {
    const d = getNodeDepth(edge.target);
    return shouldShowTreeEdge({
      edge,
      edgeMode,
      totalNodes: positionedNodes.length,
      targetDepth: d,
      hoveredNodeId,
      focusedNodeId: null,
    });
  });

  const getBranchId = (nodeId) => nodeMap.get(String(nodeId))?.data?.branchId || String(rootId);

  return visibleEdges
    .sort((a, b) => getNodeDepth(a.target) - getNodeDepth(b.target))
    .map((edge) => {
      const sourceNode = nodeMap.get(String(edge.source));
      const targetNode = nodeMap.get(String(edge.target));
      if (!sourceNode || !targetNode) return null;

      const depth = getNodeDepth(edge.target);
      const branchId = getBranchId(edge.target);
      const lane = branchLanes.get(branchId) || { side: "left", x: 0, y: null };

      const route = routeBundledEdge({ sourceNode, targetNode, rootNode, branchLane: lane, depth });
      const edgeStyle = getProfessionalEdgeStyle({ edge, sourceNode, targetNode, edgeMode, hoveredNodeId, focusedNodeId: null });
      const branchColor = targetNode?.data?.branchColor?.edge || "#94a3b8";

      return {
        id: edge.id,
        source: String(edge.source),
        target: String(edge.target),
        sourceHandle: getSourceHandleId(route.sourceSide),
        targetHandle: getTargetHandleId(route.targetSide),
        type: route.type,
        animated: false, label: "", markerEnd: undefined,
        data: { routePoints: route.points, sourceSide: route.sourceSide, targetSide: route.targetSide, branchColor, depth, edgeStyle },
      };
    })
    .filter(Boolean);
};

// =====================
// FALLBACK SMART ROUTING
// =====================
const createSmartRoutedEdgesFallback = ({ rawEdges, positionedNodes, layoutType, edgeMode, hoveredNodeId }) => {
  const nodeMap = new Map(positionedNodes.map((n) => [String(n.id), n]));
  const obstacleRects = positionedNodes.map((n) => getNodeRect(n, 30));
  const usedSegments = new Map();
  const useCurve = ["presentation-map", "clean-mindmap", "visual-center"].includes(layoutType);
  const edgeType = useCurve ? "cleanCurve" : "smartOrthogonal";

  const sortedEdges = [...rawEdges].sort((a, b) => {
    const sa = nodeMap.get(String(a.source)), sb = nodeMap.get(String(b.source));
    return (sa?.level ?? 0) - (sb?.level ?? 0);
  });

  return sortedEdges
    .map((edge, index) => {
      const sourceNode = nodeMap.get(String(edge.source));
      const targetNode = nodeMap.get(String(edge.target));
      if (!sourceNode || !targetNode) return null;

      const best = useCurve
        ? { sourceSide: "right", targetSide: "left", points: [getRawHandlePoint(sourceNode, "right"), getRawHandlePoint(targetNode, "left")], score: 0 }
        : pickBestHandleAndRoute({ sourceNode, targetNode, layoutType, obstacleRects, usedSegments, laneIndex: index });

      if (!best) return null;
      if (!useCurve) registerRouteSegments(best.points, usedSegments);

      const branchColor = sourceNode?.data?.branchColor?.edge || "#94a3b8";
      const edgeStyle = getProfessionalEdgeStyle({ edge, sourceNode, targetNode, edgeMode, hoveredNodeId, focusedNodeId: null });

      return {
        id: edge.id || `smart-${index}-${edge.source}-${edge.target}`,
        source: String(edge.source), target: String(edge.target),
        sourceHandle: getSourceHandleId(best.sourceSide),
        targetHandle: getTargetHandleId(best.targetSide),
        type: edgeType,
        animated: false, label: "", markerEnd: undefined,
        data: { routePoints: best.points, sourceSide: best.sourceSide, targetSide: best.targetSide, branchColor, depth: targetNode?.level ?? 1, edgeStyle },
      };
    })
    .filter(Boolean);
};

// =====================
// PROFESSIONAL EDGE PIPELINE
// =====================
const createProfessionalEdges = ({ rawEdges, positionedNodes, rootId, childrenMap, layoutType, edgeMode, hoveredNodeId, semanticEdges }) => {
  const nodeMap = new Map(positionedNodes.map((n) => [String(n.id), n]));
  const sanitized = sanitizeRawEdges(rawEdges, positionedNodes);

  const useBundled = ["presentation-map", "clean-mindmap", "compact-mindmap"].includes(layoutType);

  let treePart = [];
  if (useBundled) {
    treePart = createBundledMindmapEdges({ rawEdges: sanitized, positionedNodes, rootId, childrenMap, edgeMode, hoveredNodeId });
  } else {
    treePart = createSmartRoutedEdgesFallback({ rawEdges: sanitized, positionedNodes, layoutType, edgeMode, hoveredNodeId });
  }

  // clean route points for all edges
  treePart = treePart.map((edge) => {
    if (!edge) return edge;
    const cleanedPoints = cleanRoutePoints(edge.data?.routePoints);
    if (!cleanedPoints || cleanedPoints.length < 2) return null;
    return {
      ...edge,
      data: { ...edge.data, routePoints: cleanedPoints },
    };
  }).filter(Boolean);

  const semanticPart = [];
  if (edgeMode === "semantic" && semanticEdges?.length) {
    semanticEdges.slice(0, 10).forEach((edge) => {
      const src = nodeMap.get(String(edge.source)), tgt = nodeMap.get(String(edge.target));
      if (!src || !tgt) return;
      const natural = getNaturalHandlePair(src, tgt);
      const sp = getRawHandlePoint(src, natural.sourceSide), tp = getRawHandlePoint(tgt, natural.targetSide);
      const semPoints = cleanRoutePoints([sp, tp]);
      if (!semPoints || semPoints.length < 2) return;
      semanticPart.push({
        id: edge.id, source: String(edge.source), target: String(edge.target),
        sourceHandle: getSourceHandleId(natural.sourceSide), targetHandle: getTargetHandleId(natural.targetSide),
        type: "smartOrthogonal",
        animated: false, label: "", markerEnd: undefined,
        data: { routePoints: semPoints, sourceSide: natural.sourceSide, targetSide: natural.targetSide, branchColor: "#94a3b8", depth: 99, edgeStyle: { strokeWidth: 1, opacity: 0.2, strokeDasharray: "6 6" } },
      });
    });
  }

  return [...treePart, ...semanticPart];
};

// Legacy alias
const createSmartRoutedEdges = (opts) => createSmartRoutedEdgesFallback({ ...opts, edgeMode: opts.layoutType, hoveredNodeId: null });

// =====================
// ADAPTIVE SPACING
// =====================
const getAdaptiveSpacing = ({ layoutType, isMobile = false }) => {
  const m = isMobile ? 0.82 : 1.0;
  const presets = {
    "presentation-map": { rootGap: 320, levelGap: 240, siblingGap: 72,  branchGap: 150, itemGap: 85 },
    "clean-mindmap":    { rootGap: 360, levelGap: 235, siblingGap: 88,  branchGap: 165, itemGap: 95 },
    "compact-mindmap":  { rootGap: 300, levelGap: 210, siblingGap: 65,  branchGap: 125, itemGap: 76 },
    "tree-compact":     { rootGap: 260, levelGap: 180, siblingGap: 48,  branchGap: 90,  itemGap: 55 },
    "visual-center":   { rootGap: 360, levelGap: 220, siblingGap: 70,  branchGap: 130, itemGap: 78 },
  };
  const p = presets[layoutType] || presets["clean-mindmap"];
  return { rootGap: Math.round(p.rootGap * m), levelGap: Math.round(p.levelGap * m), siblingGap: Math.round(p.siblingGap * m), branchGap: Math.round(p.branchGap * m), itemGap: Math.round(p.itemGap * m) };
};

// =====================
// ADAPTIVE NODE SIZE
// =====================
const getAdaptiveNodeSize = (node, isMobile = false, layoutType = "clean-mindmap") => {
  const title = String(node?.title || "");
  const type = node?.type || "concept";
  const level = node?.level ?? 1;
  const isRoot = type === "root" || level === 0;
  const hasSub = !!node?.subtitle;
  let width, height;
  if (isRoot) { width = 300; height = 110; if (title.length > 30) width = 340; }
  else if (level === 1) { width = 230; height = 90; if (title.length > 24) width = 260; }
  else { width = 210; height = 78; if (title.length > 20) width = 240; }
  if (title.length > 50) { width += 16; height += 16; }
  if (hasSub) height += 22;
  width  = Math.max(isRoot ? 280 : 190, Math.min(width, isRoot ? 360 : 290));
  height = Math.max(70, Math.min(height, 140));
  if (isMobile) { width = Math.max(isRoot ? 200 : 160, Math.min(width - 24, isRoot ? 300 : 240)); height = Math.max(isRoot ? 90 : 72, Math.min(height - 8, isRoot ? 120 : 110)); }
  if (layoutType === "compact-mindmap" || layoutType === "tree-compact") { width = Math.max(160, width * 0.88); height = Math.max(65, height * 0.90); }
  return { width, height };
};

// =====================
// LAYOUT HELPERS
// =====================
const sortChildrenForLayout = (children, nodesById, subtreeSizes) => {
  return [...children].sort((a, b) => {
    const sa = subtreeSizes.get(a) || 1, sb = subtreeSizes.get(b) || 1;
    if (sb !== sa) return sb - sa;
    return (nodesById.get(a)?.order ?? 0) - (nodesById.get(b)?.order ?? 0);
  });
};

const subtreeHeight = (nodeId, childrenMap, subtreeSizes, spacing) => {
  const children = childrenMap.get(nodeId) || [];

  if (children.length === 0) {
    return spacing.itemGap;
  }

  const childHeights = children.map((c) =>
    subtreeHeight(c, childrenMap, subtreeSizes, spacing)
  );

  const totalChildrenHeight =
    childHeights.reduce((sum, h) => sum + h, 0) +
    Math.max(0, children.length - 1) * spacing.siblingGap;

  return Math.max(spacing.branchGap, totalChildrenHeight);
};

const orderBranchesForBalancedMindmap = (branchIds, subtreeSizes) => {
  const sorted = [...branchIds].sort((a, b) => (subtreeSizes.get(b) || 1) - (subtreeSizes.get(a) || 1));
  const ordered = [];
  sorted.forEach((id, index) => { if (index % 2 === 0) ordered.unshift(id); else ordered.push(id); });
  return ordered;
};

const splitBranchesBalanced = (branchIds, subtreeSizes) => {
  const sorted = [...branchIds].sort((a, b) => (subtreeSizes.get(b) || 1) - (subtreeSizes.get(a) || 1));
  const left = [], right = [];
  let leftW = 0, rightW = 0;
  sorted.forEach((id) => {
    const w = subtreeSizes.get(id) || 1;
    if (leftW <= rightW) { left.push(id); leftW += w; }
    else { right.push(id); rightW += w; }
  });
  return { left, right };
};

const splitBranchesBalancedByHeight = (branchIds, childrenMap, subtreeSizes, spacing) => {
  const sorted = [...branchIds].sort((a, b) => {
    const ha = subtreeHeight(a, childrenMap, subtreeSizes, spacing);
    const hb = subtreeHeight(b, childrenMap, subtreeSizes, spacing);
    return hb - ha;
  });

  const left = [];
  const right = [];
  let leftH = 0;
  let rightH = 0;

  sorted.forEach((id) => {
    const h = subtreeHeight(id, childrenMap, subtreeSizes, spacing);

    if (leftH <= rightH) {
      left.push(id);
      leftH += h;
    } else {
      right.push(id);
      rightH += h;
    }
  });

  return { left, right };
};

const placeOrphans = ({ nodes, positions, spacing, layoutType = "clean-mindmap" }) => {
  let orphanIndex = 0;

  nodes.forEach((n) => {
    if (positions.has(n.id)) return;

    const size = getAdaptiveNodeSize(n, false, layoutType);
    const col = orphanIndex % 3;
    const row = Math.floor(orphanIndex / 3);

    positions.set(n.id, {
      ...size,
      node: n,
      position: {
        x: spacing.rootGap + col * (size.width + 80),
        y: 420 + row * (size.height + 70),
      },
      data: { ...n.data, depth: 5, isOrphan: true },
    });

    orphanIndex++;
  });
};

// =====================
// MANUAL POSITION HELPERS
// =====================
const mergeManualPositions = (autoNodes, currentNodes) => {
  const currentMap = new Map((currentNodes || []).map((n) => [String(n.id), n]));

  return (autoNodes || []).map((node) => {
    const current = currentMap.get(String(node.id));

    if (current?.data?.isManualPosition) {
      return {
        ...node,
        position: current.position,
        data: {
          ...node.data,
          isManualPosition: true,
        },
      };
    }

    return {
      ...node,
      data: {
        ...node.data,
        isManualPosition: false,
      },
    };
  });
};

const placeNodeNearParent = ({ node, parentNode, siblingIndex = 0, layoutType = "clean-mindmap" }) => {
  if (!parentNode) return node;

  const parentBox = getNodeBox(parentNode);
  const size = getAdaptiveNodeSize(node, false, layoutType);
  const side =
    parentNode.data?.branchSide ||
    parentNode.data?.branchIndex % 2 === 0
      ? "right"
      : "left";

  const offsetX = 260;
  const offsetY = siblingIndex * 90;

  return {
    ...node,
    width: size.width,
    height: size.height,
    position: {
      x: side === "left"
        ? parentBox.left - offsetX - size.width
        : parentBox.right + offsetX,
      y: parentBox.cy + offsetY - size.height / 2,
    },
    data: {
      ...node.data,
      isManualPosition: false,
    },
  };
};

// =====================
// LAYOUT ENGINES
// =====================
const layoutCleanMindmap = (nodes, spacing, rootId, childrenMap) => {
  if (!nodes.length) return [];
  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  const root = nodesById.get(rootId);
  if (!root) return [];
  const positions = new Map();
  const subtreeSizes = new Map();
  nodes.forEach((n) => subtreeSizes.set(n.id, getSubtreeSize(n.id, childrenMap)));
  const { levelGap, siblingGap } = spacing;

  positions.set(root.id, { ...getAdaptiveNodeSize(root), node: root, position: { x: 0, y: 0 } });

  const rootChildren = sortChildrenForLayout(childrenMap.get(root.id) || [], nodesById, subtreeSizes);
  const orderedRootChildren = orderBranchesForBalancedMindmap(rootChildren, subtreeSizes);
  const { left: leftBranches, right: rightBranches } = splitBranchesBalancedByHeight(
    orderedRootChildren,
    childrenMap,
    subtreeSizes,
    spacing
  );

  const layoutBranch = (nodeId, depth, bandTop, bandBottom, side) => {
    if (positions.has(nodeId)) return;
    const node = nodesById.get(nodeId);
    if (!node) return;
    const children = sortChildrenForLayout(childrenMap.get(nodeId) || [], nodesById, subtreeSizes);
    const size = getAdaptiveNodeSize(node);
    const xBase = side === "left" ? -depth * levelGap : depth * levelGap;
    const centerY = (bandTop + bandBottom) / 2;
    positions.set(nodeId, { ...size, node, position: { x: xBase, y: centerY }, data: { ...node.data, depth } });
    if (!children.length) return;
    const totalW = children.reduce((s, cid) => s + subtreeSizes.get(cid), 0);
    let cursor = bandTop;
    children.forEach((childId) => {
      const w = subtreeSizes.get(childId);
      const childBand = Math.max(siblingGap + 65, ((bandBottom - bandTop) * w) / Math.max(totalW, 1));
      layoutBranch(childId, depth + 1, cursor, cursor + childBand, side);
      cursor += childBand;
    });
    const ys = children.map((cid) => positions.get(cid)?.position.y).filter((y) => y != null);
    if (ys.length) positions.get(nodeId).position.y = ys.reduce((a, b) => a + b, 0) / ys.length;
  };

  const leftTotal = leftBranches.reduce((s, id) => s + subtreeHeight(id, childrenMap, subtreeSizes, spacing), 0);
  let leftCursor = -leftTotal / 2;
  leftBranches.forEach((branchId) => {
    const band = subtreeHeight(branchId, childrenMap, subtreeSizes, spacing);
    layoutBranch(branchId, 1, leftCursor, leftCursor + band, "left");
    leftCursor += band;
  });

  const rightTotal = rightBranches.reduce((s, id) => s + subtreeHeight(id, childrenMap, subtreeSizes, spacing), 0);
  let rightCursor = -rightTotal / 2;
  rightBranches.forEach((branchId) => {
    const band = subtreeHeight(branchId, childrenMap, subtreeSizes, spacing);
    layoutBranch(branchId, 1, rightCursor, rightCursor + band, "right");
    rightCursor += band;
  });

  placeOrphans({ nodes, positions, spacing, layoutType: "clean-mindmap" });

  return resolveVerticalOverlaps(nodes.map((n) => {
    const pos = positions.get(n.id);
    if (!pos) return { ...n, position: { x: 0, y: 0 }, width: 220, height: 88, style: { ...(n.style || {}), width: 220, minWidth: 220, height: 88, minHeight: 88 } };
    return { ...n, width: pos.width, height: pos.height, position: pos.position, style: { ...(n.style || {}), width: pos.width, minWidth: pos.width, height: pos.height, minHeight: pos.height }, data: { ...n.data, ...pos.data } };
  }));
};

const layoutPresentationMap = (nodes, spacing, rootId, childrenMap) => {
  if (!nodes.length) return [];
  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  const root = nodesById.get(rootId);
  if (!root) return [];
  const positions = new Map();
  const subtreeSizes = new Map();
  nodes.forEach((n) => subtreeSizes.set(n.id, getSubtreeSize(n.id, childrenMap)));
  const { rootGap, levelGap, siblingGap } = spacing;

  positions.set(root.id, { ...getAdaptiveNodeSize(root), node: root, position: { x: 0, y: 0 }, data: { ...root.data, depth: 0 } });

  const rootChildren = sortChildrenForLayout(childrenMap.get(root.id) || [], nodesById, subtreeSizes);
  const orderedRootChildren = orderBranchesForBalancedMindmap(rootChildren, subtreeSizes);
  const { left: leftBranches, right: rightBranches } = splitBranchesBalancedByHeight(
    orderedRootChildren,
    childrenMap,
    subtreeSizes,
    spacing
  );

  const layoutBranch = (nodeId, depth, x, bandTop, bandBottom, side) => {
    if (positions.has(nodeId)) return;
    const node = nodesById.get(nodeId);
    if (!node) return;
    const children = sortChildrenForLayout(childrenMap.get(nodeId) || [], nodesById, subtreeSizes);
    const size = getAdaptiveNodeSize(node);
    const centerY = (bandTop + bandBottom) / 2;
    positions.set(nodeId, { ...size, node, position: { x, y: centerY }, data: { ...node.data, depth } });
    if (!children.length) return;
    const totalW = children.reduce((s, cid) => s + subtreeSizes.get(cid), 0);
    let cursor = bandTop;
    children.forEach((childId) => {
      const w = subtreeSizes.get(childId);
      const childBand = Math.max(siblingGap + 55, ((bandBottom - bandTop) * w) / Math.max(totalW, 1));
      layoutBranch(childId, depth + 1, x - levelGap, cursor, cursor + childBand, side);
      cursor += childBand;
    });
    const ys = children.map((cid) => positions.get(cid)?.position.y).filter((y) => y != null);
    if (ys.length) positions.get(nodeId).position.y = ys.reduce((a, b) => a + b, 0) / ys.length;
  };

  const leftTotal = leftBranches.reduce((s, id) => s + subtreeHeight(id, childrenMap, subtreeSizes, spacing), 0);
  let leftCursor = -leftTotal / 2;
  leftBranches.forEach((branchId) => {
    const band = subtreeHeight(branchId, childrenMap, subtreeSizes, spacing);
    layoutBranch(branchId, 1, -rootGap, leftCursor, leftCursor + band, "left");
    leftCursor += band;
  });

  const rightTotal = rightBranches.reduce((s, id) => s + subtreeHeight(id, childrenMap, subtreeSizes, spacing), 0);
  let rightCursor = -rightTotal / 2;
  rightBranches.forEach((branchId) => {
    const band = subtreeHeight(branchId, childrenMap, subtreeSizes, spacing);
    layoutBranch(branchId, 1, rootGap, rightCursor, rightCursor + band, "right");
    rightCursor += band;
  });

  placeOrphans({ nodes, positions, spacing, layoutType: "presentation-map" });

  return resolveVerticalOverlaps(nodes.map((n) => {
    const pos = positions.get(n.id);
    if (!pos) return { ...n, position: { x: 0, y: 0 }, width: 220, height: 88, style: { ...(n.style || {}), width: 220, minWidth: 220, height: 88, minHeight: 88 } };
    return { ...n, width: pos.width, height: pos.height, position: pos.position, style: { ...(n.style || {}), width: pos.width, minWidth: pos.width, height: pos.height, minHeight: pos.height }, data: { ...n.data, ...pos.data } };
  }));
};

const layoutCompactMindmap = (nodes, spacing, rootId, childrenMap) => {
  if (!nodes.length) return [];
  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  const root = nodesById.get(rootId);
  if (!root) return [];
  const positions = new Map();
  const subtreeSizes = new Map();
  nodes.forEach((n) => subtreeSizes.set(n.id, getSubtreeSize(n.id, childrenMap)));
  const { levelGap, siblingGap, rootGap } = spacing;

  positions.set(root.id, { ...getAdaptiveNodeSize(root, false, "compact-mindmap"), node: root, position: { x: rootGap, y: 0 }, data: { ...root.data, depth: 0 } });

  const calcSize = (id) => 1 + (childrenMap.get(id) || []).reduce((s, c) => s + calcSize(c), 0);
  const rootChildren = sortChildrenForLayout(childrenMap.get(root.id) || [], nodesById, subtreeSizes);
  const orderedRootChildren = orderBranchesForBalancedMindmap(rootChildren, subtreeSizes);
  const { left: leftBranches, right: rightBranches } = splitBranchesBalancedByHeight(
    orderedRootChildren,
    childrenMap,
    subtreeSizes,
    spacing
  );

  const layoutBranch = (nodeId, depth, x, bandTop, bandBottom, side) => {
    if (positions.has(nodeId)) return;
    const node = nodesById.get(nodeId);
    if (!node) return;
    const children = sortChildrenForLayout(childrenMap.get(nodeId) || [], nodesById, subtreeSizes);
    const size = getAdaptiveNodeSize(node, false, "compact-mindmap");
    const xPos = side === "left" ? x - depth * levelGap : x + depth * levelGap;
    const centerY = (bandTop + bandBottom) / 2;
    positions.set(nodeId, { ...size, node, position: { x: xPos, y: centerY }, data: { ...node.data, depth } });
    if (!children.length) return;
    const totalW = children.reduce((s, cid) => s + calcSize(cid), 0);
    let cursor = bandTop;
    children.forEach((childId) => {
      const childBand = Math.max(siblingGap + 40, ((bandBottom - bandTop) * calcSize(childId)) / Math.max(totalW, 1));
      layoutBranch(childId, depth + 1, xPos, cursor, cursor + childBand, side);
      cursor += childBand;
    });
    const ys = children.map((cid) => positions.get(cid)?.position.y).filter((y) => y != null);
    if (ys.length) positions.get(nodeId).position.y = ys.reduce((a, b) => a + b, 0) / ys.length;
  };

  const leftTotal = leftBranches.reduce((s, id) => s + subtreeHeight(id, childrenMap, subtreeSizes, spacing), 0);
  let leftCursor = -leftTotal / 2;
  leftBranches.forEach((branchId) => {
    const band = subtreeHeight(branchId, childrenMap, subtreeSizes, spacing);
    layoutBranch(branchId, 1, rootGap, leftCursor, leftCursor + band, "left");
    leftCursor += band;
  });

  const rightTotal = rightBranches.reduce((s, id) => s + subtreeHeight(id, childrenMap, subtreeSizes, spacing), 0);
  let rightCursor = -rightTotal / 2;
  rightBranches.forEach((branchId) => {
    const band = subtreeHeight(branchId, childrenMap, subtreeSizes, spacing);
    layoutBranch(branchId, 1, rootGap, rightCursor, rightCursor + band, "right");
    rightCursor += band;
  });

  placeOrphans({ nodes, positions, spacing, layoutType: "compact-mindmap" });

  const allY = Array.from(positions.values()).map((p) => p.position.y);
  const minY = Math.min(...allY);
  const offsetY = -minY + 80;

  return resolveVerticalOverlaps(nodes.map((n) => {
    const pos = positions.get(n.id);
    if (!pos) return { ...n, position: { x: 0, y: 0 }, width: 200, height: 80, style: { ...(n.style || {}), width: 200, minWidth: 200, height: 80, minHeight: 80 } };
    return { ...n, width: pos.width, height: pos.height, position: { x: pos.position.x, y: pos.position.y + offsetY }, style: { ...(n.style || {}), width: pos.width, minWidth: pos.width, height: pos.height, minHeight: pos.height }, data: { ...n.data, ...pos.data } };
  }), 18);
};

const layoutTreeCompact = (nodes, spacing, rootId, childrenMap) => {
  if (!nodes.length) return [];
  const nodesById = new Map(nodes.map((n) => [n.id, n]));
  const root = nodesById.get(rootId);
  if (!root) return [];
  const positions = new Map();
  const subtreeSizes = new Map();
  nodes.forEach((n) => subtreeSizes.set(n.id, getSubtreeSize(n.id, childrenMap)));
  const { levelGap, siblingGap, rootGap } = spacing;

  positions.set(root.id, { ...getAdaptiveNodeSize(root, false, "tree-compact"), node: root, position: { x: rootGap, y: 0 }, data: { ...root.data, depth: 0 } });

  const calcSize = (id) => 1 + (childrenMap.get(id) || []).reduce((s, c) => s + calcSize(c), 0);
  const rootChildren = sortChildrenForLayout(childrenMap.get(root.id) || [], nodesById, subtreeSizes);
  const totalHeight = rootChildren.reduce((s, id) => s + subtreeHeight(id, childrenMap, subtreeSizes, spacing), 0);
  let cursor = -totalHeight / 2;

  const layoutBranch = (nodeId, depth, bandTop, bandBottom) => {
    if (positions.has(nodeId)) return;
    const node = nodesById.get(nodeId);
    if (!node) return;
    const children = sortChildrenForLayout(childrenMap.get(nodeId) || [], nodesById, subtreeSizes);
    const size = getAdaptiveNodeSize(node, false, "tree-compact");
    const x = rootGap + depth * levelGap;
    const centerY = (bandTop + bandBottom) / 2;
    positions.set(nodeId, { ...size, node, position: { x, y: centerY }, data: { ...node.data, depth } });
    if (!children.length) return;
    const totalW = children.reduce((s, cid) => s + calcSize(cid), 0);
    let childCursor = bandTop;
    children.forEach((childId) => {
      const childBand = Math.max(siblingGap + 38, ((bandBottom - bandTop) * calcSize(childId)) / Math.max(totalW, 1));
      layoutBranch(childId, depth + 1, childCursor, childCursor + childBand);
      childCursor += childBand;
    });
    const ys = children.map((cid) => positions.get(cid)?.position.y).filter((y) => y != null);
    if (ys.length) positions.get(nodeId).position.y = ys.reduce((a, b) => a + b, 0) / ys.length;
  };

  rootChildren.forEach((childId) => {
    const band = subtreeHeight(childId, childrenMap, subtreeSizes, spacing);
    layoutBranch(childId, 1, cursor, cursor + band);
    cursor += band;
  });

  placeOrphans({ nodes, positions, spacing, layoutType: "tree-compact" });

  const allY = Array.from(positions.values()).map((p) => p.position.y);
  const minY = Math.min(...allY);
  const offsetY = -minY + 80;

  return resolveVerticalOverlaps(nodes.map((n) => {
    const pos = positions.get(n.id);
    if (!pos) return { ...n, position: { x: 0, y: 0 }, width: 200, height: 80, style: { ...(n.style || {}), width: 200, minWidth: 200, height: 80, minHeight: 80 } };
    return { ...n, width: pos.width, height: pos.height, position: { x: pos.position.x, y: pos.position.y + offsetY }, style: { ...(n.style || {}), width: pos.width, minWidth: pos.width, height: pos.height, minHeight: pos.height }, data: { ...n.data, ...pos.data } };
  }), 16);
};

const resolveVerticalOverlaps = (nodes, minGap = 22) => {
  const sorted = [...nodes].sort((a, b) => a.position.y - b.position.y);
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1], curr = sorted[i];
    const minY = (prev.position.y + (prev.height || 90) / 2) + (curr.height || 90) / 2 + minGap;
    if (curr.position.y < minY) curr.position.y = minY;
  }
  return sorted;
};

// =====================
// SAFE FALLBACK LAYOUT
// =====================
const layoutSafeLayeredTree = ({ nodes, rootId, childrenMap, direction = "RIGHT", isMobile = false }) => {
  const nodeMap = new Map(nodes.map((n) => [String(n.id), n]));
  const root = nodeMap.get(String(rootId)) || nodes[0];

  const levelMap = new Map();

  const walk = (id, depth) => {
    if (!levelMap.has(depth)) levelMap.set(depth, []);
    if (!levelMap.get(depth).includes(String(id))) {
      levelMap.get(depth).push(String(id));
    }
    const children = childrenMap.get(String(id)) || [];
    children.forEach((childId) => {
      if (nodeMap.has(String(childId))) {
        walk(String(childId), depth + 1);
      }
    });
  };

  if (root) walk(String(root.id), 0);

  // orphan nodes
  nodes.forEach((n) => {
    const exists = [...levelMap.values()].some((arr) => arr.includes(String(n.id)));
    if (!exists) {
      const depth = n.data?.depth ?? n.level ?? 1;
      if (!levelMap.has(depth)) levelMap.set(depth, []);
      levelMap.get(depth).push(String(n.id));
    }
  });

  const levelGap = isMobile ? 210 : 300;
  const nodeGap = isMobile ? 95 : 125;

  const positioned = [];

  [...levelMap.entries()].forEach(([depth, ids]) => {
    const totalHeight = Math.max(0, (ids.length - 1) * nodeGap);
    const startY = -totalHeight / 2;

    ids.forEach((id, index) => {
      const original = nodeMap.get(String(id));
      if (!original) return;

      const width = original.width ?? 220;
      const height = original.height ?? 90;

      let x = depth * levelGap;
      let y = startY + index * nodeGap;

      if (direction === "DOWN") {
        x = startY + index * nodeGap * 2;
        y = depth * levelGap * 0.75;
      }

      positioned.push({
        ...original,
        width,
        height,
        position: { x, y },
        style: { ...(original.style || {}), width, minWidth: width, height, minHeight: height },
      });
    });
  });

  return positioned;
};

// =====================
// FINAL OVERLAP RESOLVER
// =====================
const resolveFinalOverlaps = (nodes, { padding = 32, maxIterations = 80 } = {}) => {
  let result = nodes.map((n) => ({
    ...n,
    position: { ...n.position },
  }));

  for (let iter = 0; iter < maxIterations; iter++) {
    let moved = false;
    const boxes = result.map(getNodeBox);

    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i];
        const b = boxes[j];

        if (!boxesOverlap(a, b, padding)) continue;

        const dx = b.cx - a.cx || 1;
        const dy = b.cy - a.cy || 1;

        const overlapX = Math.min(a.right - b.left, b.right - a.left) + padding;
        const overlapY = Math.min(a.bottom - b.top, b.bottom - a.top) + padding;

        if (overlapX < overlapY) {
          const push = overlapX / 2;
          result[i].position.x -= Math.sign(dx) * push;
          result[j].position.x += Math.sign(dx) * push;
        } else {
          const push = overlapY / 2;
          result[i].position.y -= Math.sign(dy) * push;
          result[j].position.y += Math.sign(dy) * push;
        }

        moved = true;
      }
    }

    if (!moved) break;
  }

  return result.map((n) => ({
    ...n,
    position: {
      x: Math.round(n.position.x),
      y: Math.round(n.position.y),
    },
  }));
};

// =====================
// ENSURE SAFE LAYOUT
// =====================
const ensureSafeLayout = ({ nodes, rootId, childrenMap, layoutType, isMobile }) => {
  let result = nodes;

  // pass 1: light resolve
  result = resolveFinalOverlaps(result, {
    padding: isMobile ? 18 : 28,
    maxIterations: 50,
  });

  let overlapInfo = countNodeOverlaps(result, isMobile ? 12 : 20);

  // if still too many overlaps, fallback to safe layered tree
  if (overlapInfo.count > Math.max(2, result.length * 0.06)) {
    const direction =
      layoutType === "tree-compact" || layoutType === "tree-down"
        ? "DOWN"
        : "RIGHT";

    result = layoutSafeLayeredTree({
      nodes: result,
      rootId,
      childrenMap,
      direction,
      isMobile,
    });

    result = resolveFinalOverlaps(result, {
      padding: isMobile ? 18 : 28,
      maxIterations: 80,
    });
  }

  return result;
};

// =====================
// CENTER FINAL LAYOUT
// =====================
const centerFinalLayout = ({ nodes, layoutType, displayMode, focusedNodeId, rootId }) => {
  if (!nodes?.length) return nodes;

  if (displayMode === "focus" && focusedNodeId) {
    const focusNode = nodes.find((n) => String(n.id) === String(focusedNodeId));
    if (focusNode) {
      const box = getNodeBox(focusNode);
      return translateNodes(nodes, -box.cx, -box.cy);
    }
  }

  const shouldCenterByRoot = [
    "presentation-map",
    "clean-mindmap",
    "compact-mindmap",
    "visual-center",
  ].includes(layoutType);

  if (shouldCenterByRoot && rootId) {
    const rootNode = nodes.find((n) => String(n.id) === String(rootId));
    if (rootNode) {
      const rootBox = getNodeBox(rootNode);
      return translateNodes(nodes, -rootBox.cx, -rootBox.cy);
    }
  }

  const bounds = getGraphBounds(nodes);
  return translateNodes(nodes, -bounds.cx, -bounds.cy);
};

// =====================
// ELK
// =====================
const ELK_CONFIGS = {
  "tree-down": { algorithm: "layered", direction: "DOWN", nodeSpacing: 65, layerSpacing: 110 },
  "visual-center": { algorithm: "layered", direction: "RIGHT", nodeSpacing: 65, layerSpacing: 130 },
  "tree-compact": { algorithm: "layered", direction: "DOWN", nodeSpacing: 70, layerSpacing: 100 },
};

const getElkOptions = (layoutType, nodeCount) => {
  const cfg = ELK_CONFIGS[layoutType] || ELK_CONFIGS["tree-down"];
  const isLarge = nodeCount > 50;
  const opts = {
    "elk.algorithm": cfg.algorithm,
    "elk.spacing.nodeNode": String(isLarge ? Math.max(50, cfg.nodeSpacing - 15) : cfg.nodeSpacing),
    "elk.spacing.edgeNode": "30",
    "elk.separateConnectedComponents": "true",
  };
  if (cfg.algorithm === "layered") {
    opts["elk.direction"] = cfg.direction;
    opts["elk.layered.spacing.nodeNodeBetweenLayers"] = String(isLarge ? Math.max(85, cfg.layerSpacing - 20) : cfg.layerSpacing);
    opts["elk.layered.nodePlacement.strategy"] = "BRANDES_KOEPF";
    opts["elk.layered.crossingMinimization.strategy"] = "LAYER_SWEEP";
    opts["elk.layered.edgeRouting"] = "ORTHOGONAL";
  }
  return opts;
};

// =====================
// CENTER & STABILIZE
// =====================
const centerLayout = ({ nodes, rootId, layoutType, displayMode, focusedNodeId }) => {
  if (!nodes?.length) return nodes;
  const bounds = getGraphBounds(nodes);
  const rootNode = getRootNodeFromPositioned(nodes, rootId);
  const focusNode = focusedNodeId ? getRootNodeFromPositioned(nodes, focusedNodeId) : null;

  if (displayMode === "focus" && focusNode) {
    const box = getNodeBox(focusNode);
    return translateNodes(nodes, -box.cx, -box.cy);
  }

  const mindmapLayouts = ["presentation-map", "clean-mindmap", "compact-mindmap", "visual-center"];
  if (mindmapLayouts.includes(layoutType) && rootNode) {
    const rootBox = getNodeBox(rootNode);
    let centered = translateNodes(nodes, -rootBox.cx, -rootBox.cy);
    const newBounds = getGraphBounds(centered);
    const maxGraphOffsetX = Math.max(220, newBounds.width * 0.12);
    const maxGraphOffsetY = Math.max(160, newBounds.height * 0.12);
    let corrX = 0, corrY = 0;
    if (Math.abs(newBounds.cx) > maxGraphOffsetX) corrX = -newBounds.cx * 0.35;
    if (Math.abs(newBounds.cy) > maxGraphOffsetY) corrY = -newBounds.cy * 0.35;
    if (corrX || corrY) centered = translateNodes(centered, corrX, corrY);
    return centered;
  }

  if (["tree-compact", "tree-down"].includes(layoutType)) {
    return translateNodes(nodes, -bounds.cx, -bounds.top + 80);
  }

  return translateNodes(nodes, -bounds.cx, -bounds.cy);
};

const collectVisibleDescendants = (nodeId, childrenMap, nodeMap, acc = new Set()) => {
  const children = childrenMap.get(String(nodeId)) || [];
  children.forEach((childId) => {
    if (!nodeMap.has(String(childId))) return;
    acc.add(String(childId));
    collectVisibleDescendants(String(childId), childrenMap, nodeMap, acc);
  });
  return acc;
};

const moveSubtreesToSides = ({ nodes, rootId, leftSet, rightSet, childrenMap }) => {
  const nodeMap = new Map(nodes.map((n) => [String(n.id), n]));
  const root = nodeMap.get(String(rootId));
  if (!root) return nodes;
  const rootBox = getNodeBox(root), rootCx = rootBox.cx;
  const sideGap = 320;
  const idsToMove = new Map();
  [...leftSet].forEach((branchId) => { const ids = collectVisibleDescendants(branchId, childrenMap, nodeMap, new Set([branchId])); ids.forEach((id) => idsToMove.set(id, "left")); });
  [...rightSet].forEach((branchId) => { const ids = collectVisibleDescendants(branchId, childrenMap, nodeMap, new Set([branchId])); ids.forEach((id) => idsToMove.set(id, "right")); });
  return nodes.map((node) => {
    const side = idsToMove.get(String(node.id));
    if (!side) return node;
    const box = getNodeBox(node);
    const distance = Math.max(sideGap, Math.abs(box.cx - rootCx));
    const newCx = side === "left" ? rootCx - distance : rootCx + distance;
    return { ...node, position: { x: Math.round(node.position.x + newCx - box.cx), y: node.position.y } };
  });
};

const balanceBranchSides = ({ nodes, rootId, layoutType, childrenMap }) => {
  if (!["presentation-map", "clean-mindmap", "compact-mindmap", "visual-center"].includes(layoutType)) return nodes;
  const nodeMap = new Map(nodes.map((n) => [String(n.id), n]));
  const root = nodeMap.get(String(rootId));
  if (!root) return nodes;
  const rootBox = getNodeBox(root);
  const rootChildren = childrenMap.get(String(rootId)) || [];
  const visibleRootChildren = rootChildren.filter((id) => nodeMap.has(String(id)));
  if (visibleRootChildren.length <= 1) return nodes;
  const leftChildren = [], rightChildren = [];
  visibleRootChildren.forEach((id) => {
    const child = nodeMap.get(String(id));
    if (!child) return;
    const box = getNodeBox(child);
    if (box.cx < rootBox.cx) leftChildren.push(String(id)); else rightChildren.push(String(id));
  });
  if (!leftChildren.length || !rightChildren.length) {
    const sorted = [...visibleRootChildren];
    const leftSet = new Set(), rightSet = new Set();
    sorted.forEach((id, index) => { if (index % 2 === 0) leftSet.add(String(id)); else rightSet.add(String(id)); });
    return moveSubtreesToSides({ nodes, rootId, leftSet, rightSet, childrenMap });
  }
  return nodes;
};

const normalizeBranchVerticalCenter = ({ nodes, rootId, layoutType, childrenMap }) => {
  if (!["presentation-map", "clean-mindmap", "compact-mindmap", "visual-center"].includes(layoutType)) return nodes;
  const nodeMap = new Map(nodes.map((n) => [String(n.id), n]));
  const root = nodeMap.get(String(rootId));
  if (!root) return nodes;
  const rootBox = getNodeBox(root), rootY = rootBox.cy;
  const rootChildren = childrenMap.get(String(rootId)) || [];
  const visibleRootChildren = rootChildren.filter((id) => nodeMap.has(String(id)));
  const branchIdsWithDesc = visibleRootChildren.map((branchId) => ({
    branchId: String(branchId),
    ids: collectVisibleDescendants(branchId, childrenMap, nodeMap, new Set([String(branchId)])),
  }));
  const leftBranches = [], rightBranches = [];
  branchIdsWithDesc.forEach((branch) => {
    const node = nodeMap.get(branch.branchId);
    if (!node) return;
    const box = getNodeBox(node);
    if (box.cx < rootBox.cx) leftBranches.push(branch); else rightBranches.push(branch);
  });
  const adjustSide = (branches) => {
    if (!branches.length) return new Map();
    const allIds = new Set();
    branches.forEach((b) => b.ids.forEach((id) => allIds.add(id)));
    const sideNodes = nodes.filter((n) => allIds.has(String(n.id)));
    const bounds = getGraphBounds(sideNodes);
    const dy = rootY - bounds.cy;
    const offsets = new Map();
    allIds.forEach((id) => offsets.set(id, dy));
    return offsets;
  };
  const leftOffsets = adjustSide(leftBranches), rightOffsets = adjustSide(rightBranches);
  return nodes.map((node) => {
    const id = String(node.id);
    const dy = leftOffsets.get(id) ?? rightOffsets.get(id) ?? 0;
    if (!dy) return node;
    return { ...node, position: { x: node.position.x, y: Math.round(node.position.y + dy) } };
  });
};

const resolveNodeOverlapsByDepth = ({ nodes, minGapX = 40, minGapY = 28 }) => {
  const groups = new Map();
  nodes.forEach((node) => {
    const depth = node.data?.depth ?? node.data?.level ?? 0;
    const box = getNodeBox(node);
    const columnKey = `${depth}-${Math.round(box.cx / 180)}`;
    if (!groups.has(columnKey)) groups.set(columnKey, []);
    groups.get(columnKey).push(node);
  });
  const offsetById = new Map();
  groups.forEach((group) => {
    const sorted = [...group].sort((a, b) => getNodeBox(a).top - getNodeBox(b).top);
    let cursorBottom = -Infinity;
    sorted.forEach((node) => {
      const box = getNodeBox(node);
      let dy = 0;
      if (box.top < cursorBottom + minGapY) dy = cursorBottom + minGapY - box.top;
      if (dy > 0) offsetById.set(String(node.id), (offsetById.get(String(node.id)) || 0) + dy);
      cursorBottom = Math.max(cursorBottom, box.bottom + dy);
    });
  });
  return nodes.map((node) => {
    const dy = offsetById.get(String(node.id)) || 0;
    if (!dy) return node;
    return { ...node, position: { x: node.position.x, y: Math.round(node.position.y + dy) } };
  });
};

const clampExtremeOutliers = ({ nodes, rootId, layoutType }) => {
  if (!nodes?.length) return nodes;
  const root = nodes.find((n) => String(n.id) === String(rootId));
  if (!root) return nodes;
  const rootBox = getNodeBox(root);
  const maxDistanceX = layoutType === "presentation-map" ? 900 : layoutType === "clean-mindmap" ? 1100 : layoutType === "compact-mindmap" ? 950 : 1200;
  const maxDistanceY = layoutType === "presentation-map" ? 650 : layoutType === "clean-mindmap" ? 850 : layoutType === "compact-mindmap" ? 760 : 1000;
  return nodes.map((node) => {
    if (String(node.id) === String(rootId)) return node;
    const box = getNodeBox(node);
    let dx = 0, dy = 0;
    const distX = box.cx - rootBox.cx, distY = box.cy - rootBox.cy;
    if (Math.abs(distX) > maxDistanceX) dx = (Math.sign(distX) * maxDistanceX) - distX;
    if (Math.abs(distY) > maxDistanceY) dy = (Math.sign(distY) * maxDistanceY) - distY;
    if (!dx && !dy) return node;
    return { ...node, position: { x: Math.round(node.position.x + dx), y: Math.round(node.position.y + dy) } };
  });
};

const stabilizeLayout = ({ nodes, rootId, layoutType, childrenMap }) => {
  let result = [...nodes];
  result = balanceBranchSides({ nodes: result, rootId, layoutType, childrenMap });
  result = normalizeBranchVerticalCenter({ nodes: result, rootId, layoutType, childrenMap });
  result = resolveNodeOverlapsByDepth({ nodes: result, minGapX: 42, minGapY: 28 });
  result = clampExtremeOutliers({ nodes: result, rootId, layoutType });
  return result;
};

// =====================
// ICON
// =====================
const getNapkinIcon = (icon, type) => {
  const map = { brain: "🧠", database: "🗄️", workflow: "🔁", target: "🎯", alert: "⚠️", check: "✅", lightbulb: "💡", clock: "🕒", sparkles: "✨", root: "🧠", concept: "💡", process: "⚙️", input: "📥", output: "📤", problem: "⚠️", solution: "✅", example: "📌", risk: "⚠️", insight: "✨", timeline: "🕒", metric: "📊" };
  return map[icon || type] || "💡";
};

// =====================
// NODE
// =====================
const NapkinNode = ({ data }) => {
  const type        = data?.type || "concept";
  const level      = data?.level ?? 1;
  const isRoot     = type === "root" || level === 0;
  const hasChildren = Boolean(data?.hasChildren);
  const isExpanded  = data?.isExpanded !== false;
  const isMobile   = data?.isMobile ?? false;
  const layoutType  = data?.layoutType || "clean-mindmap";
  const isCompact  = layoutType === "compact-mindmap" || layoutType === "tree-compact";
  const bc         = data?.branchColor;
  const hiddenCount = data?.hiddenCount ?? 0;

  const toneClass = isRoot
    ? "border-violet-300 bg-gradient-to-br from-violet-50 via-white to-fuchsia-50 shadow-lg"
    : level === 1 && bc ? `${bc.bg} ${bc.border} shadow-sm`
    : ["problem", "risk"].includes(type) ? "border-amber-200 bg-amber-50 shadow-sm"
    : ["solution", "output"].includes(type) ? "border-emerald-200 bg-emerald-50 shadow-sm"
    : ["process", "workflow"].includes(type) ? "border-sky-200 bg-sky-50 shadow-sm"
    : "border-slate-200 bg-white shadow-sm";

  const textSz   = isMobile ? (isRoot ? "text-sm" : "text-[11px]") : (isRoot ? "text-base" : "text-sm");
  const toggleSz = isMobile ? "h-7 w-7 text-base" : "h-6 w-6 text-sm";

  return (
    <div
      className={["relative rounded-2xl border px-3 py-2.5 text-slate-800 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md", isRoot ? "min-w-[180px] max-w-[280px]" : level === 1 ? "min-w-[160px] max-w-[230px]" : "min-w-[140px] max-w-[210px]", toneClass].join(" ")}
      onMouseEnter={() => data?.onHover?.(data.id)}
      onMouseLeave={() => data?.onHover?.(null)}
    >
      <Handle id="top"    type="target" position={Position.Top}    className="!opacity-0 !pointer-events-none" />
      <Handle id="right"  type="target" position={Position.Right}  className="!opacity-0 !pointer-events-none" />
      <Handle id="bottom" type="target" position={Position.Bottom} className="!opacity-0 !pointer-events-none" />
      <Handle id="left"   type="target" position={Position.Left}   className="!opacity-0 !pointer-events-none" />

      {hasChildren && (
        <button type="button" onClick={(e) => { e.stopPropagation(); e.preventDefault(); data?.onToggle?.(data.id); }}
          className={`absolute -right-2.5 -top-2.5 z-10 flex items-center justify-center rounded-full border bg-white text-slate-500 shadow-sm transition-transform hover:scale-110 hover:bg-violet-50 hover:border-violet-300 hover:text-violet-600 ${toggleSz}`}
          title={isExpanded ? "Thu gọn" : "Mở rộng"}>
          {isExpanded ? "−" : "+"}
        </button>
      )}

      <div className="flex items-start gap-2">
        <div className={["flex shrink-0 items-center justify-center rounded-xl", isMobile ? "h-7 w-7 text-sm" : "h-8 w-8 text-base", isRoot ? "bg-violet-100" : level === 1 && bc ? bc.bg : "bg-slate-50"].join(" ")}>
          {getNapkinIcon(data?.icon, type)}
        </div>
        <div className="min-w-0 flex-1">
          <div className={`whitespace-normal break-words leading-snug ${textSz} ${isRoot ? "font-bold" : level === 1 ? "font-semibold" : "font-medium"}`}>
            {data?.title}
          </div>
          {data?.subtitle && (
            <div className={`mt-1 whitespace-normal break-words text-slate-400 ${isMobile ? "text-[10px]" : "text-xs"}`}>{data.subtitle}</div>
          )}
          {!isMobile && !isCompact && !isRoot && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-400">{type}</span>
            </div>
          )}
        </div>
      </div>

      {hiddenCount > 0 && (
        <div className={`absolute -bottom-2 left-1/2 -translate-x-1/2 z-10 rounded-full border px-1.5 py-0.5 text-[9px] font-semibold ${bc?.badge || "bg-slate-100 text-slate-500 border-slate-200"}`}>
          +{hiddenCount} mục
        </div>
      )}

      <Handle id="top-source"    type="source" position={Position.Top}    className="!opacity-0 !pointer-events-none" />
      <Handle id="right-source"  type="source" position={Position.Right}  className="!opacity-0 !pointer-events-none" />
      <Handle id="bottom-source" type="source" position={Position.Bottom} className="!opacity-0 !pointer-events-none" />
      <Handle id="left-source"   type="source" position={Position.Left}   className="!opacity-0 !pointer-events-none" />
    </div>
  );
};

// =====================
// COMPONENT
// =====================
function MindMapContent({ data, onClose, initialLayoutType }) {
  const reactFlowInstance = useReactFlow();
  const isMobile = useIsMobile();

  const totalCount = data?.diagram?.nodes?.length || data?.nodes?.length || 0;
  const resolvedDisplayMode = getAutoDisplayMode(totalCount);
  const resolvedLayout = (() => {
    if (initialLayoutType && initialLayoutType !== "auto") return initialLayoutType;
    return getAutoLayout(resolvedDisplayMode, totalCount);
  })();
  const resolvedEdgeMode = getAutoEdgeMode(totalCount);

  const [displayMode, setDisplayMode] = useState(resolvedDisplayMode);
  const [focusedNodeId, setFocusedNodeId] = useState(null);
  const [layoutType, setLayoutType] = useState(resolvedLayout);
  const [edgeMode, setEdgeMode] = useState(resolvedEdgeMode);
  const [hoveredNodeId, setHoveredNodeId] = useState(null);
  const lastInteractionRef = useRef("initial");
  const viewportRef = useRef(null);

  useEffect(() => { const h = (e) => { if (e.key === "Escape") onClose(); }; window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h); }, [onClose]);
  useEffect(() => { document.body.style.overflow = "hidden"; return () => { document.body.style.overflow = ""; }; }, []);

  const diagram = useMemo(() => normalizeHierarchyFromData(data), [data]);
  const allNodes = useMemo(() => diagram.nodes || [], [diagram]);
  const childrenMap = useMemo(() => buildChildrenMap(allNodes), [allNodes]);
  const parentMap = useMemo(() => buildParentMap(allNodes), [allNodes]);
  const rootNode = useMemo(() => allNodes.find((n) => n.parent == null) || allNodes[0] || null, [allNodes]);
  const treeEdges = useMemo(() => diagram.treeEdges || [], [diagram]);
  const semanticEdges = useMemo(() => diagram.semanticEdges || [], [diagram]);

  const displayModeVisibleIds = useMemo(() => {
    if (!rootNode) return new Set(allNodes.map((n) => n.id));
    if (displayMode === "overview") return getOverviewNodeIds({ nodes: allNodes, root: rootNode, childrenMap });
    if (displayMode === "focus" && focusedNodeId) return getFocusNodeIds({ focusedNodeId, root: rootNode, parentMap, childrenMap });
    return new Set(allNodes.map((n) => n.id));
  }, [displayMode, focusedNodeId, allNodes, rootNode, childrenMap, parentMap]);

  const [expandedNodes, setExpandedNodes] = useState(() => {
    if (!allNodes.length) return new Set();
    if (allNodes.length > 40) {
      const expanded = new Set();
      allNodes.forEach((n) => { if (n.level === 0 || n.level === 1) expanded.add(n.id); });
      return expanded;
    }
    return new Set(allNodes.map((n) => n.id));
  });

  useEffect(() => {
    if (!allNodes.length) return;
    setExpandedNodes((prev) => {
      const next = new Set();
      allNodes.forEach((n) => { if (prev.has(n.id)) next.add(n.id); });
      if (rootNode && !next.has(rootNode.id)) next.add(rootNode.id);
      return next;
    });
  }, [allNodes, rootNode]);

  const expandedVisibleIds = useMemo(() => getVisibleNodeIds(allNodes, childrenMap, expandedNodes, rootNode), [allNodes, childrenMap, expandedNodes, rootNode]);

  const finalVisibleIds = useMemo(() => {
    const result = new Set();
    expandedVisibleIds.forEach((id) => { if (displayModeVisibleIds.has(id)) result.add(id); });
    return result;
  }, [expandedVisibleIds, displayModeVisibleIds]);

  const finalVisibleNodes = useMemo(() => allNodes.filter((n) => finalVisibleIds.has(n.id)), [allNodes, finalVisibleIds]);
  const coloredNodes = useMemo(() => rootNode ? assignBranchColors(finalVisibleNodes, rootNode.id, childrenMap) : finalVisibleNodes, [finalVisibleNodes, rootNode, childrenMap]);

  const hiddenCounts = useMemo(() => {
    const counts = new Map();
    if (!rootNode) return counts;
    const rootChildren = childrenMap.get(rootNode.id) || [];
    rootChildren.forEach((branchId) => {
      const branchChildren = childrenMap.get(branchId) || [];
      const hidden = branchChildren.length - branchChildren.filter((cid) => finalVisibleIds.has(cid)).length;
      if (hidden > 0) counts.set(branchId, hidden);
    });
    return counts;
  }, [rootNode, childrenMap, finalVisibleIds]);

  const visibleNodeIdsSet = useMemo(() => new Set(coloredNodes.map((n) => n.id)), [coloredNodes]);
  const visibleTreeEdges = useMemo(() => treeEdges.filter((e) => visibleNodeIdsSet.has(e.source) && visibleNodeIdsSet.has(e.target)), [treeEdges, visibleNodeIdsSet]);
  const visibleSemanticEdges = useMemo(() => semanticEdges.filter((e) => visibleNodeIdsSet.has(e.source) && visibleNodeIdsSet.has(e.target)).slice(0, 10), [semanticEdges, visibleNodeIdsSet]);
  const rawEdges = useMemo(() => [...visibleTreeEdges], [visibleTreeEdges]);

  const toggleNodeExpansion = useCallback((nodeId) => {
    try { viewportRef.current = reactFlowInstance.getViewport(); } catch (_) { viewportRef.current = null; }
    lastInteractionRef.current = "node-toggle";
    setExpandedNodes((prev) => {
      const next = new Set(prev);
      if (next.has(nodeId)) {
        const remove = new Set([nodeId]);
        const collect = (id) => (childrenMap.get(id) || []).forEach((c) => { remove.add(c); collect(c); });
        collect(nodeId);
        remove.forEach((id) => next.delete(id));
      } else {
        next.add(nodeId);
        const collect = (id) => (childrenMap.get(id) || []).forEach((c) => { next.add(c); collect(c); });
        collect(nodeId);
      }
      return next;
    });
  }, [childrenMap, reactFlowInstance]);

  const handleNodeClick = useCallback((event, node) => {
    setFocusedNodeId(node.id);
    setDisplayMode("focus");
    lastInteractionRef.current = "node-focus";
  }, []);

  const handleExpandAll = useCallback(() => { lastInteractionRef.current = "expand-all"; setExpandedNodes(new Set(allNodes.map((n) => n.id))); }, [allNodes]);
  const handleCollapseAll = useCallback(() => { lastInteractionRef.current = "collapse-all"; setExpandedNodes(new Set(rootNode ? [rootNode.id] : [])); }, [rootNode]);
  const handleLayoutChange = useCallback((value) => { lastInteractionRef.current = "layout-change"; setLayoutType(value === "auto" ? getAutoLayout(displayMode, allNodes.length) : value); }, [allNodes, displayMode]);

  const resetManualPositions = useCallback(() => {
    setInnerNodes((prev) =>
      prev.map((n) => ({
        ...n,
        data: {
          ...n.data,
          isManualPosition: false,
        },
      }))
    );
  }, []);

  const handleDisplayModeChange = useCallback((mode) => {
    lastInteractionRef.current = "display-mode-change";
    setDisplayMode(mode);
    if (mode === "full") { setFocusedNodeId(null); setExpandedNodes(new Set(allNodes.map((n) => n.id))); }
    if (mode === "overview") setFocusedNodeId(null);
    if (mode === "focus" && !focusedNodeId && rootNode) setFocusedNodeId(rootNode.id);
  }, [allNodes, rootNode, focusedNodeId]);

  const handleCenterView = useCallback(() => {
    lastInteractionRef.current = "manual-fit";
    reactFlowInstance.fitView({ padding: isMobile ? 0.12 : 0.22, duration: 300 });
  }, [reactFlowInstance, isMobile]);

  const handleNodeDragStop = useCallback((event, node) => {
    setInnerNodes((prev) => {
      const updated = prev.map((n) =>
        String(n.id) === String(node.id)
          ? {
              ...n,
              position: node.position,
              data: {
                ...n.data,
                isManualPosition: true,
              },
            }
          : n
      );

      // Re-route edges with new positions
      const positionedNodes = updated.map((n) => ({
        ...n,
        width: n.width ?? 220,
        height: n.height ?? 90,
        position: n.position,
      }));

      const updatedEdges = createProfessionalEdges({
        rawEdges: rawEdgesRef.current || [],
        positionedNodes,
        rootId: rootIdRef.current,
        childrenMap: childrenMapRef.current,
        layoutType: layoutTypeRef.current,
        edgeMode: edgeModeRef.current,
        hoveredNodeId,
        semanticEdges: semanticEdgesRef.current || [],
      });

      setInnerEdges(updatedEdges);
      return updated;
    });
  }, [hoveredNodeId]);

  const hasChildrenFn = useCallback((nodeId) => (childrenMap.get(nodeId) || []).length > 0, [childrenMap]);

  const buildNodeData = useCallback((node) => ({
    ...node, id: String(node.id),
    title: node.title || "Node", subtitle: node.subtitle || "",
    icon: node.icon || "lightbulb", type: node.type || "concept",
    level: node.level ?? 0, layoutType,
    hasChildren: hasChildrenFn(node.id),
    isExpanded: expandedNodes.has(node.id),
    onToggle: toggleNodeExpansion, isMobile,
    branchColor: node.branchColor,
    hiddenCount: hiddenCounts.get(node.id) || 0,
    onHover: setHoveredNodeId,
  }), [layoutType, expandedNodes, hasChildrenFn, toggleNodeExpansion, isMobile, hiddenCounts]);

  const [innerNodes, setInnerNodes] = useState([]);
  const [innerEdges, setInnerEdges] = useState([]);

  // Refs to avoid stale closure in drag handler
  const rawEdgesRef = useRef([]);
  const rootIdRef = useRef(null);
  const childrenMapRef = useRef(new Map());
  const layoutTypeRef = useRef("clean-mindmap");
  const edgeModeRef = useRef("full");
  const semanticEdgesRef = useRef([]);

  useEffect(() => {
    if (!coloredNodes.length || !rootNode) { setInnerNodes([]); setInnerEdges([]); return; }
    let cancelled = false;

    const applyLayout = async () => {
      try {
        const spacing = getAdaptiveSpacing({ layoutType, isMobile });
        let positioned = [];

        // STEP 1: LAYOUT
        if (layoutType === "presentation-map") {
          positioned = layoutPresentationMap(coloredNodes, spacing, rootNode.id, childrenMap);
        } else if (layoutType === "clean-mindmap") {
          positioned = layoutCleanMindmap(coloredNodes, spacing, rootNode.id, childrenMap);
        } else if (layoutType === "compact-mindmap") {
          positioned = layoutCompactMindmap(coloredNodes, spacing, rootNode.id, childrenMap);
        } else if (layoutType === "tree-compact") {
          positioned = layoutTreeCompact(coloredNodes, spacing, rootNode.id, childrenMap);
        } else {
          const elkOptions = getElkOptions(layoutType, coloredNodes.length);
          const elkGraph = {
            id: "root", layoutOptions: elkOptions,
            children: coloredNodes.map((n) => { const s = getAdaptiveNodeSize(n, isMobile); return { id: String(n.id), width: s.width, height: s.height }; }),
            edges: visibleTreeEdges.filter((e) => e.source && e.target).map((e) => ({ id: String(e.id || `e-${e.source}-${e.target}`), sources: [String(e.source)], targets: [String(e.target)] })),
          };
          try {
            const lg = await elk.layout(elkGraph);
            const posMap = new Map((lg.children || []).map((n) => [n.id, n]));
            positioned = coloredNodes.map((n) => {
              const pos = posMap.get(String(n.id)) || {};
              const s = getAdaptiveNodeSize(n, isMobile);
              return { ...n, width: s.width, height: s.height, position: { x: pos.x ?? 0, y: pos.y ?? 0 }, style: { ...(n.style || {}), width: s.width, minWidth: s.width, height: s.height, minHeight: s.height }, data: { ...n.data, depth: n.level ?? 0 } };
            });
          } catch (err) {
            console.error("ELK layout error:", err);
            positioned = coloredNodes.map((n, i) => { const s = getAdaptiveNodeSize(n, isMobile); return { ...n, width: s.width, height: s.height, position: { x: (i % 5) * 250, y: Math.floor(i / 5) * 140 }, style: { ...(n.style || {}), width: s.width, minWidth: s.width, height: s.height, minHeight: s.height }, data: { ...n.data, depth: n.level ?? 0 } }; });
          }
        }

        if (cancelled) return;

        // STEP 2: ASSIGN BRANCH META
        positioned = assignBranchMeta({ nodes: positioned, rootId: rootNode.id, childrenMap });
        if (cancelled) return;

        // STEP 3: STABILIZE + ENSURE SAFE
        let stabilized = stabilizeLayout({ nodes: positioned, rootId: rootNode.id, layoutType, childrenMap });
        if (cancelled) return;

        const safeNodes = ensureSafeLayout({
          nodes: stabilized,
          rootId: rootNode.id,
          childrenMap,
          layoutType,
          isMobile,
        });
        if (cancelled) return;

        // STEP 3.5: MERGE MANUAL POSITIONS
        const mergedNodes = mergeManualPositions(safeNodes, innerNodes);
        if (cancelled) return;

        // STEP 4: CENTER FINAL
        const centered = centerFinalLayout({ nodes: mergedNodes, layoutType, displayMode, focusedNodeId, rootId: rootNode.id });
        if (cancelled) return;

        // STEP 5: ROUTE EDGES
        const routedEdges = createProfessionalEdges({
          rawEdges: visibleTreeEdges,
          positionedNodes: centered,
          rootId: rootNode.id,
          childrenMap,
          layoutType,
          edgeMode,
          hoveredNodeId,
          semanticEdges: visibleSemanticEdges,
        });

        if (import.meta?.env?.DEV) {
          const bounds = getGraphBounds(centered);
          const overlap = countNodeOverlaps(centered, isMobile ? 12 : 20);
          const invalidEdges = (routedEdges || []).filter((e) => {
            const points = e?.data?.routePoints || [];
            return !points.length || points.some((p) => !Number.isFinite(p?.x) || !Number.isFinite(p?.y));
          });
          console.log("[MindMap overlap check]", {
            layoutType,
            displayMode,
            edgeMode,
            visible: centered.length,
            total: allNodes.length,
            overlaps: overlap.count,
            samplePairs: overlap.pairs.slice(0, 8),
            bounds,
          });
          console.log("[MindMap edge check]", {
            nodes: centered.length,
            edges: routedEdges.length,
            invalidEdges: invalidEdges.length,
          });
        }

        // STEP 6: RENDER
        setInnerEdges(routedEdges);
        setInnerNodes(centered.map((n) => ({
          id: String(n.id), type: "napkinNode",
          position: n.position, width: n.width, height: n.height,
          data: buildNodeData(n),
        })));

        // Sync refs for drag handler
        rawEdgesRef.current = visibleTreeEdges;
        rootIdRef.current = rootNode.id;
        childrenMapRef.current = childrenMap;
        layoutTypeRef.current = layoutType;
        edgeModeRef.current = edgeMode;
        semanticEdgesRef.current = visibleSemanticEdges;

        // STEP 7: FITVIEW
        const shouldFit = ["initial", "layout-change", "display-mode-change", "expand-all", "collapse-all", "manual-fit"].includes(lastInteractionRef.current);
        if (shouldFit) {
          requestAnimationFrame(() => {
            requestAnimationFrame(() => {
              try { reactFlowInstance.fitView({ padding: isMobile ? 0.12 : 0.22, duration: 320 }); } catch (_) {}
            });
          });
        } else if (viewportRef.current) {
          setTimeout(() => { try { reactFlowInstance.setViewport(viewportRef.current, { duration: 0 }); } catch (_) {} }, 60);
        }
      } catch (error) {
        console.error("Failed to layout:", error);
      }
    };

    applyLayout();
    return () => { cancelled = true; };
  }, [layoutType, displayMode, edgeMode, coloredNodes, rawEdges, rootNode, childrenMap, buildNodeData, reactFlowInstance, isMobile, focusedNodeId, allNodes, visibleTreeEdges, visibleSemanticEdges]);

  const edgeTypes = useMemo(() => ({ cleanCurve: CleanCurveEdge, smartOrthogonal: SmartOrthogonalEdge }), []);
  const nodeTypes = useMemo(() => ({ napkinNode: NapkinNode }), []);
  const getLayoutLabel = (type) => LAYOUT_OPTIONS.find((o) => o.value === type)?.label || type;
  const showLargeWarning = coloredNodes.length > 55;
  const showFullHint = displayMode === "full" && totalCount > 40 && edgeMode === "clean";

  return (
    <div className="fixed inset-0 z-[9999] flex flex-col bg-surface-base">
      {/* Header */}
      <div className="flex-shrink-0 bg-surface-sidebar border-b border-border px-3 py-2 flex flex-col gap-2 md:flex-row md:items-center md:justify-between md:gap-3 md:px-4 md:py-2.5">
        <div className="flex flex-wrap items-center gap-2 min-w-0 md:gap-3">
          <h3 className="font-bold text-text-primary truncate text-sm md:text-[14px]">🧠 {data?.title || "Sơ đồ tư duy"}</h3>

          {/* Display mode */}
          <div className="flex rounded-lg border border-slate-200 overflow-hidden bg-slate-50 text-[11px]">
            {DISPLAY_MODES.map((mode) => (
              <button key={mode.value} onClick={() => handleDisplayModeChange(mode.value)}
                className={`px-2 py-1.5 transition-colors ${displayMode === mode.value ? "bg-white font-semibold text-violet-700 shadow-sm" : "text-slate-500 hover:bg-slate-100"}`}>
                {mode.label}
              </button>
            ))}
          </div>

          {/* Edge mode */}
          <div className="flex rounded-lg border border-slate-200 overflow-hidden bg-slate-50 text-[11px]">
            {EDGE_MODES.map((mode) => (
              <button key={mode.value} onClick={() => { lastInteractionRef.current = "layout-change"; setEdgeMode(mode.value); }}
                className={`px-2 py-1.5 transition-colors ${edgeMode === mode.value ? "bg-white font-semibold text-sky-700 shadow-sm" : "text-slate-500 hover:bg-slate-100"}`}>
                {mode.label}
              </button>
            ))}
          </div>

          {/* Layout select */}
          <label className="flex items-center gap-1.5 text-text-secondary text-[11px]">
            <span className="hidden sm:inline whitespace-nowrap">Bố cục:</span>
            <select value={layoutType} onChange={(e) => handleLayoutChange(e.target.value)}
              className="input-surface !py-1.5 !px-2 text-[11px] md:!py-1.5 md:!px-3 md:text-[12px] min-w-[100px]">
              {LAYOUT_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
            </select>
          </label>

          <span className="text-[10px] md:text-[11px] text-text-muted whitespace-nowrap">{coloredNodes.length}/{allNodes.length} nút</span>
        </div>

        <div className="flex items-center gap-1.5 flex-wrap">
          <button onClick={handleCenterView} className="btn-secondary border-slate-300 text-slate-600 px-2 py-1.5 text-[11px]" title="Căn giữa sơ đồ">⛶</button>
          <button onClick={handleExpandAll} className="btn-secondary text-sky-400 border-sky-500/30 bg-sky-500/10 px-2 py-1.5 text-[11px]">＋<span className="hidden sm:inline ml-1">Mở hết</span></button>
          <button onClick={handleCollapseAll} className="btn-secondary px-2 py-1.5 text-[11px]">−<span className="hidden sm:inline ml-1">Thu hết</span></button>
          <button onClick={onClose} className="btn-secondary px-2 py-1.5 text-[11px]">✕<span className="hidden sm:inline ml-1">Đóng</span></button>
        </div>
      </div>

      {showLargeWarning && (
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-1.5 text-[11px] text-amber-700 text-center">
          ⚠️ Sơ đồ lớn ({allNodes.length} nút) — nên dùng <strong>Tổng quan</strong> hoặc <strong>Tập trung</strong>.
        </div>
      )}

      {showFullHint && (
        <div className="bg-sky-50 border-b border-sky-200 px-4 py-1.5 text-[11px] text-sky-700 text-center">
          📋 Đang xem đầy đủ node, dây đang ở chế độ <strong>Gọn</strong> để dễ đọc. Chuyển sang <strong>Đầy đủ dây</strong> để xem tất cả.
        </div>
      )}

      {displayMode === "overview" && coloredNodes.length < allNodes.length && (
        <div className="bg-violet-50 border-b border-violet-200 px-4 py-1.5 text-[11px] text-violet-700 text-center">
          📋 Tổng quan ({coloredNodes.length}/{allNodes.length} nút) — chuyển <strong>Đầy đủ</strong> để xem toàn bộ.
        </div>
      )}

      <div className="flex-1 relative overflow-hidden mindmap-flow min-h-0">
        <ReactFlow nodes={innerNodes} edges={innerEdges} fitView nodesDraggable
          nodeTypes={nodeTypes} edgeTypes={edgeTypes}
          minZoom={0.08} maxZoom={2.5} zoomOnScroll panOnScroll panOnDrag
          onNodeClick={handleNodeClick}
          onNodeDragStop={handleNodeDragStop}
          className="h-full w-full bg-surface-base"
          proOptions={{ hideAttribution: true }}>
          {!isMobile && <MiniMap zoomable pannable maskColor="rgba(13,15,20,0.5)" style={{ width: 100, height: 65 }} />}
          <Controls />
          <Background variant="dots" gap={16} size={1} color="#1e2d3d" />
        </ReactFlow>
        {innerNodes.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-text-muted flex-col gap-2">
            <div className="text-3xl">🧠</div><p className="text-sm">Đang tải sơ đồ...</p>
          </div>
        )}
      </div>

      <div className="bg-surface-sidebar border-t border-border px-3 py-1.5 text-[10px] md:text-[11px] text-text-muted text-center flex-shrink-0 hidden sm:block">
        Bấm <strong className="text-text-secondary">+</strong>/<strong className="text-text-secondary">−</strong> mở/thu · Click node để tập trung · <strong className="text-text-secondary">Esc</strong> đóng
      </div>
    </div>
  );
}

export default function MindMapModal({ data, onClose, initialLayoutType }) {
  if (typeof document === "undefined") return null;
  return createPortal(
    <ReactFlowProvider><MindMapContent data={data} onClose={onClose} initialLayoutType={initialLayoutType} /></ReactFlowProvider>,
    document.body
  );
}
