// Mindmap viewer — container + ReactFlow canvas.
// Extracted mechanically from MindMapModal.jsx (Task 14 split): this is the former
// `MindMapContent` component, unchanged in its ELK/geometry layout behavior.
//
// New in Task 14 (additive, on top of the mechanical move):
//   - Uses `normalizeMindmapRecord` (Task 13) instead of the old internal
//     `normalizeHierarchyFromData`. The new normalizer returns semantic `relations`
//     SEPARATELY from `nodes[].parent` — tree edges are derived here from `parent`
//     (mirroring what the old normalizer used to synthesize internally).
//   - Relations render as their own edge set (`type: "relation"`, dashed, seal
//     accent color, small label) via <RelationEdge/>, toggled by "Quan hệ" in the
//     toolbar (default ON). They are computed independently of the tree-edge
//     ELK/routing pipeline — no change to that pipeline's behavior.
//   - Degraded banner (v2 records where `generator.degraded`) with "Tạo lại"
//     (regenerate with force:true), wired via `onRegenerate`/`regenerating` props
//     that the shell (MindMapModal) forwards from SidebarRight.
import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import ReactFlow, {
  MiniMap, Controls, Background, useReactFlow, BaseEdge,
} from "reactflow";
import { Icon } from "../ui/Icon";
import Spinner from "../ui/Spinner";
import { normalizeMindmapRecord } from "../../utils/mindmapNormalize";
import {
  getAutoLayout, getAutoDisplayMode, getAutoEdgeMode, useIsMobile, relationTypeLabel,
} from "./constants";
import {
  elk, assignBranchColors, assignBranchMeta, buildChildrenMap, buildParentMap,
  centerFinalLayout, cleanRoutePoints, countNodeOverlaps, createProfessionalEdges,
  ensureSafeLayout, getAdaptiveNodeSize, getAdaptiveSpacing, getElkOptions, getFocusNodeIds,
  getGraphBounds, getNaturalHandlePair, getOverviewNodeIds, getSideVector, getSourceHandleId,
  getTargetHandleId, getVisibleNodeIds, layoutCleanMindmap, layoutCompactMindmap,
  layoutPresentationMap, layoutTreeCompact, mergeManualPositions, roundedPolylinePath,
  stabilizeLayout,
} from "./useElkLayout";
import { NapkinNode } from "./MindmapNodeCard";
import RelationEdge from "./RelationEdge";
import MindmapToolbar from "./MindmapToolbar";
import EvidenceDrawer from "./EvidenceDrawer";
import { exportMindmapPng } from "./exportPng";

// =====================
// PROFESSIONAL CLEAN CURVE (fixed endpoints) — tree edge renderers
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
// MODEL → DIAGRAM ADAPTER
// =====================
// `normalizeMindmapRecord` (Task 13) gives us a shape-agnostic model:
// { title, nodes:[{id,parent,title,note,kind,chunkRefs,order}], relations, degraded, missing }.
// The layout/rendering pipeline below still expects the old "unified node" shape
// (id/parent/title/subtitle/type/group/level/icon/order) and an explicit tree-edge
// list — this adapter mirrors exactly what normalizeHierarchyFromData used to do
// for that half of the job (depth walk + parent→edge synthesis + star fallback).
const KIND_TO_TYPE = { root: "root", section: "process", idea: "concept", detail: "concept" };
const KIND_TO_ICON = { root: "brain", section: "workflow", idea: "lightbulb", detail: "lightbulb" };

const buildDiagramFromModel = (model) => {
  const rawNodes = model?.nodes || [];
  if (!rawNodes.length) {
    return { title: model?.title || "Sơ đồ tư duy", nodes: [], treeEdges: [] };
  }

  let unifiedNodes = rawNodes.map((n, index) => ({
    id: n.id,
    parent: n.parent,
    title: n.title || `Node ${index + 1}`,
    subtitle: n.note || "",
    type: KIND_TO_TYPE[n.kind] || (n.parent == null ? "root" : "concept"),
    group: "other",
    level: 0,
    icon: KIND_TO_ICON[n.kind] || (n.parent == null ? "brain" : "lightbulb"),
    order: Number.isFinite(Number(n.order)) ? Number(n.order) : index,
    kind: n.kind,
    note: n.note || "",
    chunkRefs: n.chunkRefs || [],
  }));

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

  return { title: model?.title || "Sơ đồ tư duy", nodes: unifiedNodes, treeEdges };
};

const MISSING_LABELS = { enrich: "làm giàu nội dung", relations: "quan hệ" };
const formatMissing = (missing) => (missing || []).map((m) => MISSING_LABELS[m] || m);

// =====================
// COMPONENT
// =====================
export default function MindmapView({ data, onClose, initialLayoutType, onRegenerate, regenerating }) {
  const reactFlowInstance = useReactFlow();
  const isMobile = useIsMobile();

  // Task 16 — skeleton preview + cancel + evidence drawer. `generating`/`progress`/
  // `onCancel`/`onAskAbout` ride in on `data` itself (not as separate props): the
  // shell (MindMapModal.jsx) forwards `data` verbatim already, so this avoids
  // widening that shell's prop surface just to plumb four extra callbacks through.
  const generating = Boolean(data?.generating);
  const genProgress = data?.progress == null
    ? null
    : (Number.isFinite(Number(data.progress)) ? Number(data.progress) : null);
  const onCancel = typeof data?.onCancel === "function" ? data.onCancel : null;
  const onAskAbout = typeof data?.onAskAbout === "function" ? data.onAskAbout : null;
  const [selectedNodeId, setSelectedNodeId] = useState(null);

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
  const [relationsVisible, setRelationsVisible] = useState(true);
  const lastInteractionRef = useRef("initial");
  const viewportRef = useRef(null);

  // EvidenceDrawer owns its own (capture-phase) Escape listener that stops
  // propagation while open, so this bubble-phase listener only ever fires for
  // "close the whole viewer" when the drawer isn't showing.
  useEffect(() => { const h = (e) => { if (e.key === "Escape") onClose(); }; window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h); }, [onClose]);
  useEffect(() => { document.body.style.overflow = "hidden"; return () => { document.body.style.overflow = ""; }; }, []);

  const model = useMemo(() => normalizeMindmapRecord(data), [data]);
  const diagram = useMemo(() => buildDiagramFromModel(model), [model]);
  const allNodes = useMemo(() => diagram.nodes || [], [diagram]);
  const childrenMap = useMemo(() => buildChildrenMap(allNodes), [allNodes]);
  const parentMap = useMemo(() => buildParentMap(allNodes), [allNodes]);
  const rootNode = useMemo(() => allNodes.find((n) => n.parent == null) || allNodes[0] || null, [allNodes]);
  const treeEdges = useMemo(() => diagram.treeEdges || [], [diagram]);

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
    setSelectedNodeId(node.id);
  }, []);

  const handlePaneClick = useCallback(() => { setSelectedNodeId(null); }, []);

  // Stable identity across renders (canvas hover/poll ticks) so EvidenceDrawer's
  // Escape-listener effect (deps [onClose]) doesn't re-subscribe every render —
  // and, more importantly, so it stays a reliable "did anything actually change"
  // signal for the drawer's other effects (see EvidenceDrawer.jsx Fix 1).
  const handleDrawerClose = useCallback(() => setSelectedNodeId(null), []);

  const selectedDrawerNode = useMemo(
    () => (selectedNodeId ? allNodes.find((n) => n.id === selectedNodeId) || null : null),
    [selectedNodeId, allNodes]
  );

  const handleExpandAll = useCallback(() => { lastInteractionRef.current = "expand-all"; setExpandedNodes(new Set(allNodes.map((n) => n.id))); }, [allNodes]);
  const handleCollapseAll = useCallback(() => { lastInteractionRef.current = "collapse-all"; setExpandedNodes(new Set(rootNode ? [rootNode.id] : [])); }, [rootNode]);
  const handleLayoutChange = useCallback((value) => { lastInteractionRef.current = "layout-change"; setLayoutType(value === "auto" ? getAutoLayout(displayMode, allNodes.length) : value); }, [allNodes, displayMode]);

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

  const [exportingPng, setExportingPng] = useState(false);
  const [exportError, setExportError] = useState(null);
  const exportTitle = model.title || data?.title;
  const handleExportPng = useCallback(async () => {
    if (exportingPng) return;
    setExportError(null);
    setExportingPng(true);
    try {
      await exportMindmapPng({ getNodes: reactFlowInstance.getNodes, title: exportTitle });
    } catch (err) {
      console.error("Xuất PNG thất bại:", err);
      setExportError("Xuất PNG thất bại — thử lại.");
      const timerId = setTimeout(() => setExportError(null), 5000);
      return () => clearTimeout(timerId);
    } finally {
      setExportingPng(false);
    }
  }, [reactFlowInstance, exportTitle, exportingPng]);

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
        semanticEdges: [],
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
          semanticEdges: [],
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
  }, [layoutType, displayMode, edgeMode, coloredNodes, rawEdges, rootNode, childrenMap, buildNodeData, reactFlowInstance, isMobile, focusedNodeId, allNodes, visibleTreeEdges]);

  // =====================
  // RELATIONS (v2 semantic edges) — additive, independent of the tree layout above.
  // Computed straight off the post-layout node positions; ReactFlow supplies the
  // actual sourceX/Y to <RelationEdge/> once sourceHandle/targetHandle resolve.
  // =====================
  const relationEdges = useMemo(() => {
    if (!relationsVisible || !model.relations?.length || !innerNodes.length) return [];
    const nodeMap = new Map(innerNodes.map((n) => [String(n.id), n]));
    const seen = new Set();
    return model.relations
      .filter((r) => nodeMap.has(r.source) && nodeMap.has(r.target) && r.source !== r.target)
      .filter((r) => {
        const key = `${r.source}->${r.target}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .slice(0, 24)
      .map((r, index) => {
        const sourceNode = nodeMap.get(r.source);
        const targetNode = nodeMap.get(r.target);
        const { sourceSide, targetSide } = getNaturalHandlePair(sourceNode, targetNode);
        return {
          id: `relation-${index}-${r.source}-${r.target}`,
          source: r.source,
          target: r.target,
          sourceHandle: getSourceHandleId(sourceSide),
          targetHandle: getTargetHandleId(targetSide),
          type: "relation",
          data: { label: r.label || relationTypeLabel(r.type) },
        };
      });
  }, [relationsVisible, model.relations, innerNodes]);

  const combinedEdges = useMemo(() => [...innerEdges, ...relationEdges], [innerEdges, relationEdges]);

  const edgeTypes = useMemo(() => ({ cleanCurve: CleanCurveEdge, smartOrthogonal: SmartOrthogonalEdge, relation: RelationEdge }), []);
  const nodeTypes = useMemo(() => ({ napkinNode: NapkinNode }), []);
  const showLargeWarning = coloredNodes.length > 55;
  const showFullHint = displayMode === "full" && totalCount > 40 && edgeMode === "clean";

  return (
    <div className="fixed inset-0 z-[9999] flex flex-col bg-surface-base">
      {/* Quality floor: visible keyboard focus ring on node cards (ReactFlow makes
          .react-flow__node the tab stop) + respect prefers-reduced-motion. Scoped
          here rather than in the shared index.css since this component owns the
          .mindmap-flow surface. */}
      <style>{`
        .mindmap-flow .react-flow__node:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 16px; }
        @media (prefers-reduced-motion: reduce) {
          .mindmap-flow .react-flow__node, .mindmap-flow .react-flow__edge-path { transition: none !important; animation: none !important; }
        }
        /* Skeleton preview — nodes "breathe" while the map is still generating
           (Task 16). Scoped to the flow surface's own generating state rather
           than touching MindmapNodeCard.jsx. */
        @keyframes mmBreathe { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .mindmap-flow.mm-generating .react-flow__node { animation: mmBreathe 1.7s ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) {
          .mindmap-flow.mm-generating .react-flow__node { animation: none !important; }
        }
      `}</style>
      <MindmapToolbar
        title={model.title || data?.title}
        displayMode={displayMode} onDisplayModeChange={handleDisplayModeChange}
        edgeMode={edgeMode} onEdgeModeChange={(mode) => { lastInteractionRef.current = "layout-change"; setEdgeMode(mode); }}
        layoutType={layoutType} onLayoutChange={handleLayoutChange}
        visibleCount={coloredNodes.length} totalCount={allNodes.length}
        onCenterView={handleCenterView} onExpandAll={handleExpandAll} onCollapseAll={handleCollapseAll} onClose={onClose}
        showLargeWarning={showLargeWarning} showFullHint={showFullHint}
        showOverviewHint={displayMode === "overview" && coloredNodes.length < allNodes.length}
        hasRelations={(model.relations?.length || 0) > 0}
        relationsVisible={relationsVisible}
        onToggleRelations={() => setRelationsVisible((v) => !v)}
        degraded={model.degraded}
        missing={formatMissing(model.missing)}
        onRegenerate={onRegenerate}
        regenerating={regenerating}
        onExportPng={handleExportPng}
        exportingPng={exportingPng}
        exportError={exportError}
      />

      {generating && (
        <div
          className="flex-shrink-0 border-b border-border px-4 py-2 flex items-center gap-2.5 text-[12px]"
          style={{ background: "color-mix(in srgb, var(--accent) 6%, transparent)" }}
        >
          <Spinner size={13} className="text-brand" />
          <span className="text-text-secondary">
            Đang dựng khung sơ đồ{genProgress != null ? ` — ${genProgress}%` : "…"}
          </span>
          {onCancel && (
            <button
              onClick={onCancel}
              className="ml-auto px-2.5 py-1 rounded-[6px] border border-border text-[11px] text-text-muted hover:text-[var(--err)] hover:border-[var(--err)]/40 transition-colors"
            >
              Huỷ
            </button>
          )}
        </div>
      )}

      <div className={`flex-1 relative overflow-hidden mindmap-flow min-h-0 ${generating ? "mm-generating" : ""}`}>
        <ReactFlow nodes={innerNodes} edges={combinedEdges} fitView nodesDraggable
          nodeTypes={nodeTypes} edgeTypes={edgeTypes}
          minZoom={0.08} maxZoom={2.5} zoomOnScroll panOnScroll panOnDrag
          onNodeClick={handleNodeClick}
          onPaneClick={handlePaneClick}
          onNodeDragStop={handleNodeDragStop}
          className="h-full w-full bg-surface-base"
          proOptions={{ hideAttribution: true }}>
          {!isMobile && <MiniMap zoomable pannable maskColor="rgba(27,42,65,0.18)" style={{ width: 100, height: 65 }} />}
          <Controls />
          <Background variant="dots" gap={16} size={1} color="rgba(92,107,122,0.3)" />
        </ReactFlow>
        {innerNodes.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-text-muted flex-col gap-2">
            <Icon name="Network" size={28} /><p className="text-sm">Đang tải sơ đồ…</p>
          </div>
        )}
        <EvidenceDrawer
          node={selectedDrawerNode}
          onClose={handleDrawerClose}
          generating={generating}
          onAskAbout={onAskAbout}
        />
      </div>

      <div className="bg-surface-sidebar border-t border-border px-3 py-1.5 text-[10px] md:text-[11px] text-text-muted text-center flex-shrink-0 hidden sm:block">
        Bấm <strong className="text-text-secondary">+</strong>/<strong className="text-text-secondary">−</strong> mở/thu · Click node để tập trung · <strong className="text-text-secondary">Esc</strong> đóng
      </div>
    </div>
  );
}
