import { useState, useLayoutEffect, useCallback, useMemo, useEffect } from "react";
import ReactFlow, {
  MiniMap,
  Controls,
  Background,
  useReactFlow,
  ReactFlowProvider
} from "reactflow";
import "reactflow/dist/style.css";
import ELK from "elkjs/lib/elk.bundled.js";
import { Handle, Position } from "reactflow";

const elk = new ELK();

function MindMapContent({ data, onClose }) {
  const reactFlowInstance = useReactFlow();
  const [innerNodes, setInnerNodes] = useState([]);
  const [innerEdges, setInnerEdges] = useState([]);
  // Mặc định: collapse TẤT CẢ các node (chỉ hiện root)
  const [collapsed, setCollapsed] = useState({});
  const [shapeType, setShapeType] = useState("rectangle");
  const [layoutType, setLayoutType] = useState("tree-right");

  // Đóng modal khi bấm Escape
  useEffect(() => {
    const handleKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onClose]);

  // Khoá scroll body khi modal mở
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = ""; };
  }, []);

  const shapeConfig = useMemo(() => {
    switch (shapeType) {
      case "circle": return { width: 160, height: 160, innerSize: 120 };
      case "diamond": return { width: 180, height: 180, innerSize: 130 };
      default: return { width: 220, height: 110, innerSize: null };
    }
  }, [shapeType]);

  const toggleCollapse = useCallback((id) => {
    setCollapsed((prev) => {
      const newCollapsed = { ...prev, [id]: !prev[id] };
      setInnerNodes((prevNodes) =>
        prevNodes.map((node) =>
          node.id === id ? { ...node, data: { ...node.data, collapsed: newCollapsed[id] } } : node
        )
      );
      return newCollapsed;
    });
  }, []);

  // Kiểm tra visibility: node ẩn nếu bất kỳ ancestor nào đang collapsed
  const isVisible = useCallback((nodeId) => {
    // Build parent map từ innerNodes
    const parentMap = {};
    innerNodes.forEach((n) => { if (n.parent) parentMap[n.id] = n.parent; });

    let currentId = nodeId;
    while (currentId) {
      const parentId = parentMap[currentId];
      if (!parentId) break;
      if (collapsed[parentId]) return false;
      currentId = parentId;
    }
    return true;
  }, [innerNodes, collapsed]);

  const getLayoutedElements = useCallback(async (flatData, layout = 'tree-right') => {
    if (!Array.isArray(flatData) || flatData.length === 0) return { nodes: [], edges: [] };

    const nodeWidth = shapeConfig.width;
    const nodeHeight = shapeConfig.height;
    let direction = 'RIGHT';
    let elkOptions = {};

    switch (layout) {
      case 'tree-right': direction = 'RIGHT'; elkOptions = { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.layered.spacing.nodeNodeBetweenLayers': '120', 'elk.spacing.nodeNode': '80', 'elk.spacing.edgeNode': '40', 'elk.spacing.edgeEdge': '50', 'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP', 'elk.alignment': 'CENTER', 'elk.hierarchyHandling': 'SEPARATE' }; break;
      case 'tree-down': direction = 'DOWN'; elkOptions = { 'elk.algorithm': 'layered', 'elk.direction': 'DOWN', 'elk.layered.spacing.nodeNodeBetweenLayers': '120', 'elk.spacing.nodeNode': '80', 'elk.spacing.edgeNode': '40', 'elk.spacing.edgeEdge': '50', 'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP', 'elk.alignment': 'CENTER', 'elk.hierarchyHandling': 'SEPARATE' }; break;
      case 'tree-up': direction = 'UP'; elkOptions = { 'elk.algorithm': 'layered', 'elk.direction': 'UP', 'elk.layered.spacing.nodeNodeBetweenLayers': '120', 'elk.spacing.nodeNode': '80', 'elk.spacing.edgeNode': '40', 'elk.spacing.edgeEdge': '50', 'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP', 'elk.alignment': 'CENTER', 'elk.hierarchyHandling': 'SEPARATE' }; break;
      case 'tree-left': direction = 'LEFT'; elkOptions = { 'elk.algorithm': 'layered', 'elk.direction': 'LEFT', 'elk.layered.spacing.nodeNodeBetweenLayers': '120', 'elk.spacing.nodeNode': '80', 'elk.spacing.edgeNode': '40', 'elk.spacing.edgeEdge': '50', 'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP', 'elk.alignment': 'CENTER', 'elk.hierarchyHandling': 'SEPARATE' }; break;
      case 'radial': elkOptions = { 'elk.algorithm': 'radial', 'elk.spacing.nodeNode': '100', 'elk.spacing.edgeNode': '40' }; break;
      case 'force': elkOptions = { 'elk.algorithm': 'force', 'elk.spacing.nodeNode': '100', 'elk.force.iterations': '200' }; break;
      case 'stress': elkOptions = { 'elk.algorithm': 'stress', 'elk.spacing.nodeNode': '100' }; break;
      default: direction = 'RIGHT'; elkOptions = { 'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.layered.spacing.nodeNodeBetweenLayers': '120', 'elk.spacing.nodeNode': '80' };
    }

    const getTargetPosition = () => {
      if (layout === 'radial' || layout === 'force' || layout === 'stress') return Position.Top;
      if (direction === 'RIGHT') return Position.Left;
      if (direction === 'LEFT') return Position.Right;
      if (direction === 'DOWN') return Position.Top;
      if (direction === 'UP') return Position.Bottom;
      return Position.Left;
    };
    const getSourcePosition = () => {
      if (layout === 'radial' || layout === 'force' || layout === 'stress') return Position.Bottom;
      if (direction === 'RIGHT') return Position.Right;
      if (direction === 'LEFT') return Position.Left;
      if (direction === 'DOWN') return Position.Bottom;
      if (direction === 'UP') return Position.Top;
      return Position.Right;
    };

    const nodeListWithData = flatData.map((node) => ({
      id: node.id || `node-${Math.random().toString(36).substr(2, 9)}`,
      parent: node.parent,
      title: node.title,
      width: nodeWidth,
      height: nodeHeight,
      style: { width: nodeWidth, height: nodeHeight, display: 'flex', alignItems: 'center', justifyContent: 'center' },
      data: {
        label: node.title,
        hasChildren: flatData.some((child) => child.parent === node.id),
        // Mặc định: tất cả node có children đều collapsed (trừ root nếu muốn)
        collapsed: collapsed[node.id] !== undefined ? collapsed[node.id] : flatData.some((child) => child.parent === node.id),
        onToggle: () => toggleCollapse(node.id),
        isRoot: !node.parent,
        shapeType,
      },
      type: 'custom',
      targetPosition: getTargetPosition(),
      sourcePosition: getSourcePosition(),
    }));

    const edgeList = [];
    flatData.forEach((node) => {
      if (node.parent) {
        edgeList.push({
          id: `e-${node.parent}-${node.id}`,
          source: node.parent,
          target: node.id || '',
          type: 'smoothstep',
          style: { stroke: '#4f46e5', strokeWidth: 2, opacity: 0.7 },
          animated: false,
        });
      }
    });

    const elkGraph = {
      id: 'root',
      layoutOptions: elkOptions,
      children: nodeListWithData.map((n) => ({ id: n.id, width: n.width, height: n.height })),
      edges: edgeList.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
    };

    try {
      const layoutedGraph = await elk.layout(elkGraph);
      const positionedNodes = nodeListWithData.map((node) => {
        const layoutedNode = layoutedGraph.children?.find((n) => n.id === node.id);
        return { ...node, position: { x: layoutedNode?.x ?? 0, y: layoutedNode?.y ?? 0 } };
      });
      return { nodes: positionedNodes, edges: edgeList };
    } catch (err) {
      console.error('ELK layout error:', err);
      const fallbackNodes = nodeListWithData.map((n, i) => ({ ...n, position: { x: (i % 5) * 250, y: Math.floor(i / 5) * 140 } }));
      return { nodes: fallbackNodes, edges: edgeList };
    }
  }, [shapeConfig, collapsed, toggleCollapse]);

  // Khi data load lần đầu: set collapsed = true cho tất cả node có children
  useLayoutEffect(() => {
    if (!data?.nodes) return;
    const flatData = Array.isArray(data.nodes) ? data.nodes : [];

    // Khởi tạo collapsed: tất cả node có con đều collapsed
    const initialCollapsed = {};
    flatData.forEach((node) => {
      const hasChildren = flatData.some((child) => child.parent === node.id);
      if (hasChildren) {
        initialCollapsed[node.id] = true;
      }
    });
    setCollapsed(initialCollapsed);

    getLayoutedElements(flatData, layoutType).then(({ nodes, edges }) => {
      setInnerNodes(nodes);
      setInnerEdges(edges);
      setTimeout(() => { try { reactFlowInstance.fitView({ padding: 0.15, duration: 400 }); } catch (e) {} }, 200);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // Re-layout khi layoutType hoặc shapeConfig thay đổi (không reset collapsed)
  useLayoutEffect(() => {
    if (!data?.nodes) return;
    const flatData = Array.isArray(data.nodes) ? data.nodes : [];
    getLayoutedElements(flatData, layoutType).then(({ nodes, edges }) => {
      setInnerNodes(nodes);
      setInnerEdges(edges);
      setTimeout(() => { try { reactFlowInstance.fitView({ padding: 0.15, duration: 400 }); } catch (e) {} }, 200);
    });
  }, [layoutType, shapeConfig, getLayoutedElements]);

  const visibleNodes = useMemo(() => innerNodes.filter((n) => isVisible(n.id)), [innerNodes, isVisible]);
  const filteredEdges = useMemo(() => innerEdges.filter((e) => {
    const srcVisible = innerNodes.find((n) => n.id === e.source);
    const tgtVisible = innerNodes.find((n) => n.id === e.target);
    if (!srcVisible || !tgtVisible) return false;
    return isVisible(e.source) && isVisible(e.target);
  }), [innerEdges, isVisible, innerNodes]);

  const memoizedNodes = useMemo(() => visibleNodes, [visibleNodes]);
  const memoizedEdges = useMemo(() => filteredEdges, [filteredEdges]);

  const nodeTypes = useMemo(() => {
    const palette = ['#22c55e', '#38bdf8', '#f97316', '#a855f7', '#facc15', '#ec4899', '#14b8a6'];
    const getColorIndex = (nodeId) => {
      if (!nodeId) return 0;
      const hashValue = Array.from(nodeId).reduce((acc, char, idx) => acc + char.charCodeAt(0) * (idx + 1), 0);
      return Math.abs(hashValue) % palette.length;
    };
    return {
      custom: ({ data, id }) => {
        const { label, hasChildren, collapsed, onToggle, isRoot } = data;
        const nodeColor = isRoot ? '#4f46e5' : palette[getColorIndex(id)];
        const wrapperStyle = {
          width: shapeConfig.width, height: shapeConfig.height,
          position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center',
          padding: shapeType === 'rectangle' ? '0' : '6px', overflow: 'visible',
        };
        const baseTextStyle = { color: '#fff', fontWeight: 600, textAlign: 'center', wordBreak: 'break-word', lineHeight: 1.35, fontSize: '0.9rem' };

        let renderedShape;
        if (shapeType === 'circle') {
          const circleSize = Math.min(shapeConfig.innerSize ?? shapeConfig.width - 20, shapeConfig.width - 20);
          renderedShape = (
            <div style={{ width: circleSize, height: circleSize, borderRadius: '50%', background: nodeColor, border: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
              <span style={baseTextStyle}>{label}</span>
            </div>
          );
        } else if (shapeType === 'diamond') {
          const diamondSize = shapeConfig.innerSize ?? shapeConfig.width - 30;
          renderedShape = (
            <div style={{ width: diamondSize, height: diamondSize, transform: 'rotate(45deg)', background: nodeColor, borderRadius: 18, border: '2px solid rgba(255,255,255,0.15)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span style={{ ...baseTextStyle, transform: 'rotate(-45deg)', width: '70%', display: 'inline-block' }}>{label}</span>
            </div>
          );
        } else {
          renderedShape = (
            <div style={{ width: '100%', height: '100%', background: nodeColor, borderRadius: 16, padding: '16px 20px', border: '2px solid rgba(255,255,255,0.12)', boxShadow: '0 8px 24px rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span style={baseTextStyle}>{label}</span>
            </div>
          );
        }

        return (
          <div style={wrapperStyle}>
            <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
            {renderedShape}
            {hasChildren && (
              <button
                onClick={(e) => { e.stopPropagation(); onToggle(); }}
                aria-label={collapsed ? 'Mở rộng nhánh con' : 'Thu gọn nhánh con'}
                title={collapsed ? `Mở rộng (${label})` : `Thu gọn (${label})`}
                style={{
                  position: 'absolute', top: '50%',
                  right: shapeType === 'rectangle' ? -20 : -26,
                  transform: 'translateY(-50%)',
                  border: '1px solid rgba(255,255,255,0.2)', borderRadius: '9999px',
                  width: 32, height: 32, background: collapsed ? '#4f46e5' : '#1f2937',
                  cursor: 'pointer', fontSize: 14, fontWeight: 700,
                  color: collapsed ? '#fff' : '#e5e7eb',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
                  transition: 'all 0.15s',
                }}
              >
                {collapsed ? '+' : '−'}
              </button>
            )}
            <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
          </div>
        );
      },
    };
  }, [shapeConfig, shapeType]);

  const selectStyle = {
    background: "#1f2937", border: "1px solid #374151", borderRadius: 8,
    padding: "6px 10px", fontSize: "0.78rem", color: "#e5e7eb", outline: "none", cursor: "pointer",
  };

  // Expand all / Collapse all helpers
  const handleExpandAll = () => {
    const flatData = Array.isArray(data?.nodes) ? data.nodes : [];
    const allExpanded = {};
    flatData.forEach((node) => {
      const hasChildren = flatData.some((child) => child.parent === node.id);
      if (hasChildren) allExpanded[node.id] = false;
    });
    setCollapsed(allExpanded);
    setInnerNodes((prev) =>
      prev.map((node) => ({
        ...node,
        data: { ...node.data, collapsed: false },
      }))
    );
  };

  const handleCollapseAll = () => {
    const flatData = Array.isArray(data?.nodes) ? data.nodes : [];
    const allCollapsed = {};
    flatData.forEach((node) => {
      const hasChildren = flatData.some((child) => child.parent === node.id);
      if (hasChildren) allCollapsed[node.id] = true;
    });
    setCollapsed(allCollapsed);
    setInnerNodes((prev) =>
      prev.map((node) => ({
        ...node,
        data: { ...node.data, collapsed: true },
      }))
    );
  };

  return (
    // Portal-style fullscreen: fixed, inset 0, z-index cao để phủ toàn bộ màn hình kể cả sidebar
    <div style={{
      position: "fixed", inset: 0,
      background: "#0f172a",
      display: "flex", flexDirection: "column",
      zIndex: 9999,       // cao hơn sidebar (z-40) và mọi thứ khác
      height: "100dvh",   // dynamic viewport height — đúng trên mobile
      width: "100dvw",
    }}>
      {/* Header */}
      <div style={{
        display: "flex", flexWrap: "wrap", alignItems: "center",
        justifyContent: "space-between", gap: 10,
        background: "#111827", padding: "10px 14px",
        borderBottom: "1px solid #1e2d3d", flexShrink: 0,
      }}>
        {/* Left: title + dropdowns */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", flex: 1, minWidth: 0 }}>
          <h3 style={{ fontSize: "0.95rem", fontWeight: 700, color: "#e5e7eb", margin: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            🧠 {data?.title || "Mind Map"}
          </h3>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.78rem", color: "#9ca3af" }}>
            <span>Cấu trúc:</span>
            <select value={layoutType} onChange={(e) => setLayoutType(e.target.value)} style={selectStyle}>
              <option value="tree-right">Cây (Trái → Phải)</option>
              <option value="tree-down">Cây (Trên → Dưới)</option>
              <option value="tree-up">Cây (Dưới → Trên)</option>
              <option value="tree-left">Cây (Phải → Trái)</option>
              <option value="radial">Tỏa tròn</option>
              <option value="force">Lực (Force)</option>
              <option value="stress">Stress</option>
            </select>
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.78rem", color: "#9ca3af" }}>
            <span>Kiểu nút:</span>
            <select value={shapeType} onChange={(e) => setShapeType(e.target.value)} style={selectStyle}>
              <option value="rectangle">Hình chữ nhật</option>
              <option value="circle">Hình tròn</option>
              <option value="diamond">Hình thoi</option>
            </select>
          </label>
        </div>

        {/* Right: expand/collapse all + close */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
          <button
            onClick={handleExpandAll}
            style={{ background: "#1e3a5f", border: "1px solid #1d4ed8", borderRadius: 8, padding: "6px 12px", color: "#60a5fa", cursor: "pointer", fontSize: "0.78rem", fontWeight: 600 }}
            title="Mở rộng tất cả"
          >
            ＋ Mở hết
          </button>
          <button
            onClick={handleCollapseAll}
            style={{ background: "#1f2937", border: "1px solid #374151", borderRadius: 8, padding: "6px 12px", color: "#9ca3af", cursor: "pointer", fontSize: "0.78rem", fontWeight: 600 }}
            title="Thu gọn tất cả"
          >
            − Thu hết
          </button>
          <button
            onClick={onClose}
            style={{ background: "#374151", border: "none", borderRadius: 8, padding: "7px 16px", color: "#e5e7eb", cursor: "pointer", fontSize: "0.8rem", fontWeight: 600, transition: "all 0.15s", display: "flex", alignItems: "center", gap: 5 }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "#4b5563"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "#374151"; }}
            title="Đóng (Esc)"
          >
            ✕ Đóng
          </button>
        </div>
      </div>

      {/* ReactFlow canvas */}
      <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>
        <ReactFlow
          nodes={memoizedNodes}
          edges={memoizedEdges}
          fitView={false}
          nodesDraggable={false}
          nodeTypes={nodeTypes}
          minZoom={0.05}
          maxZoom={2}
          zoomOnScroll
          panOnScroll
          style={{ height: "100%", width: "100%", background: "#0f172a" }}
        >
          <MiniMap
            zoomable
            pannable
            style={{ background: "#1f2937", border: "1px solid #374151", borderRadius: 10 }}
            maskColor="rgba(15,23,42,0.5)"
          />
          <Controls style={{ background: "#1f2937", border: "1px solid #374151", borderRadius: 10 }} />
          <Background variant="dots" gap={16} size={1} color="#1e2d3d" />
        </ReactFlow>
        {memoizedNodes.length === 0 && (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", color: "#6b7280", flexDirection: "column", gap: 10 }}>
            <div style={{ fontSize: "2rem" }}>🧠</div>
            <p style={{ fontSize: "0.85rem" }}>Không có dữ liệu — Kiểm tra console</p>
          </div>
        )}
      </div>

      {/* Hint bar dưới cùng */}
      <div style={{
        background: "#111827", borderTop: "1px solid #1e2d3d",
        padding: "6px 16px", fontSize: "0.7rem", color: "#4b5563",
        textAlign: "center", flexShrink: 0,
      }}>
        Bấm <strong style={{ color: "#6b7280" }}>+</strong> / <strong style={{ color: "#6b7280" }}>−</strong> trên node để mở/thu gọn · Nhấn <strong style={{ color: "#6b7280" }}>Esc</strong> để đóng
      </div>
    </div>
  );
}

export default function MindMapModal({ data, onClose }) {
  return (
    <ReactFlowProvider>
      <MindMapContent data={data} onClose={onClose} />
    </ReactFlowProvider>
  );
}