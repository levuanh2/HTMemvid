// Viewer mind-elixir — thay ReactFlow/ELK. Overlay fullscreen giữ từ v2.
import { useEffect, useRef, useState, useCallback } from "react";
import MindElixir from "mind-elixir";
import { recordToMindElixir } from "../../utils/mindElixirAdapter";
import EvidenceDrawer from "./EvidenceDrawer";
import { Icon } from "../ui/Icon";
import "./mindmap.css";

// Palette nhánh: archival inks (edge hexes từ constants.js::BRANCH_COLORS)
const PALETTE = ["#5C6B7A", "#3E6B57", "#B5821F", "#B23A2E", "#4A5A8A", "#8A7A66"];

const THEME = {
  name: "PhongDoc",
  palette: PALETTE,
  cssVar: {
    "--main-color": "var(--text-primary)",
    "--main-bgcolor": "var(--bg-base)",
    "--color": "var(--text-secondary)",
    "--bgcolor": "var(--bg-base)",
  },
};

export default function MindElixirView({ data, onClose, onRegenerate, regenerating }) {
  const containerRef = useRef(null);
  const mindRef = useRef(null);
  const sidecarRef = useRef(new Map());
  const [selected, setSelected] = useState(null);   // node cho EvidenceDrawer
  const [showRelations, setShowRelations] = useState(true);
  const [dirty, setDirty] = useState(false);

  const degraded = Boolean(data?.generator?.degraded);
  const missing = data?.generator?.missing || [];

  // (re)init khi đổi record
  useEffect(() => {
    if (!containerRef.current || !data) return;
    const { mindData, sidecar } = recordToMindElixir(data);
    sidecarRef.current = sidecar;
    const mind = new MindElixir({
      el: containerRef.current,
      direction: MindElixir.SIDE,
      editable: true,
      draggable: true,
      contextMenu: true,
      toolBar: false,       // toolbar riêng của mình
      keypress: true,
      allowUndo: true,
      theme: THEME,
    });
    mind.init(mindData);
    mindRef.current = mind;

    mind.bus.addListener("selectNodes", (nodes) => {
      const n = nodes?.[0];
      if (!n) return;
      const side = sidecarRef.current.get(n.id);
      setSelected({ id: n.id, title: n.topic, note: side?.note || "", chunkRefs: side?.chunkRefs || [] });
    });
    mind.bus.addListener("operation", () => setDirty(true));

    return () => {
      // mind-elixir's own destroy() unregisters the bus listeners above AND the
      // container keydown handler it wires internally (init() -> On()); without
      // it, re-init on a data.id change (e.g. regenerate while the modal stays
      // open) would leave the old instance's listeners attached to the same
      // container DOM node, stacking duplicate handlers on every re-init.
      mindRef.current?.destroy?.();
      mindRef.current = null;
      containerRef.current && (containerRef.current.innerHTML = "");
    };
  }, [data?.id]);

  // Esc đóng (confirm khi dirty — Task 8 nối)
  const requestClose = useCallback(() => {
    if (dirty && !window.confirm("Có thay đổi chưa lưu. Đóng và bỏ thay đổi?")) return;
    onClose?.();
  }, [dirty, onClose]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") requestClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [requestClose]);

  return (
    <div className="fixed inset-0 z-[1000] flex flex-col" style={{ background: "var(--bg-base)" }}>
      {/* Toolbar mỏng */}
      <div className="flex items-center gap-2 px-3 py-2 border-b flex-shrink-0"
        style={{ borderColor: "var(--border-color)", background: "var(--bg-sidebar)" }}>
        <span className="font-display text-[14px] font-semibold truncate text-text-primary">{data?.title || "Sơ đồ tư duy"}</span>
        {dirty && <span className="text-[11px] px-1.5 rounded" style={{ color: "var(--warn)" }}>● chưa lưu</span>}
        <div className="flex-1" />
        <label className="flex items-center gap-1 text-[12px] text-text-secondary cursor-pointer">
          <input type="checkbox" checked={showRelations} onChange={(e) => setShowRelations(e.target.checked)} />
          Quan hệ
        </label>
        {/* Nút Lưu (Task 8), Export PNG (Task 9) gắn thêm tại đây */}
        <button onClick={requestClose} aria-label="Đóng" className="p-1.5 rounded hover:bg-[var(--bg-hover)]">
          <Icon name="X" size={16} />
        </button>
      </div>
      {/* Degraded banner giữ từ v2 */}
      {degraded && (
        <div className="px-3 py-1.5 text-[12px] flex items-center gap-2 border-b"
          style={{ color: "var(--warn)", borderColor: "var(--border-color)", background: "var(--bg-elevated)" }}>
          <span>Bản đồ chưa đầy đủ{missing.length ? ` (thiếu: ${missing.join(", ")})` : ""}.</span>
          <button onClick={onRegenerate} disabled={regenerating} className="underline">
            {regenerating ? "Đang tạo lại…" : "Tạo lại"}
          </button>
        </div>
      )}
      {/* Map */}
      <div ref={containerRef} className={`flex-1 min-h-0 me-container${showRelations ? "" : " me-hide-arrows"}`} />
      {/* Evidence drawer giữ nguyên component */}
      {selected && (
        <EvidenceDrawer node={selected} onClose={() => setSelected(null)}
          generating={Boolean(data?.generating)} onAskAbout={data?.onAskAbout} />
      )}
    </div>
  );
}
