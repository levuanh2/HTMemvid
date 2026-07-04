// Mindmap modal — thin shell.
// Task 7 swap: viewer is now MindElixirView (mind-elixir), replacing the
// old ReactFlow/ELK view (MindmapView.jsx — removed in Task 9, along with
// MindmapNodeCard.jsx/RelationEdge.jsx/useElkLayout.js/exportPng.js).
// This file keeps its historical export surface (default export `MindMapModal`)
// so SidebarRight.jsx does not need to change how it imports this component.
import { createPortal } from "react-dom";
import { Icon } from "../ui/Icon";
import MindElixirView from "../mindmap/MindElixirView";

export default function MindMapModal({ data, onClose, initialLayoutType, onRegenerate, regenerating }) {
  if (typeof document === "undefined") return null;
  // Empty-state: tránh hiển thị khung trống khi không có node nào.
  const hasNodes =
    (Array.isArray(data?.nodes) && data.nodes.length > 0) ||
    (Array.isArray(data?.diagram?.nodes) && data.diagram.nodes.length > 0);
  if (!hasNodes) {
    return createPortal(
      <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/45 backdrop-blur-sm" onClick={onClose}>
        <div className="rounded-[10px] p-6 max-w-sm text-center border" style={{ background: "var(--bg-card)", borderColor: "var(--border-strong)", boxShadow: "var(--shadow-card-hover)" }} onClick={(e) => e.stopPropagation()}>
          <Icon name="Network" size={26} className="mx-auto mb-2 text-text-muted" />
          <p className="font-display text-[15px] font-semibold text-text-primary mb-1">Sơ đồ trống</p>
          <p className="text-[12px] text-text-secondary mb-4">Không có nội dung để hiển thị. Hãy thử tạo lại với tài liệu khác.</p>
          <button onClick={onClose} className="btn-primary text-[13px]">Đóng</button>
        </div>
      </div>,
      document.body
    );
  }
  return createPortal(
    <MindElixirView data={data} onClose={onClose} onRegenerate={onRegenerate} regenerating={regenerating} />,
    document.body
  );
}
