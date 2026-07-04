// Mindmap viewer — shared constants + tiny selector helpers.
// Extracted mechanically from MindMapModal.jsx (Task 14 split). No behavior change.
import { useState, useEffect } from "react";

// =====================
// BRANCH COLORS — muted archival inks (paper-friendly, not candy rainbow)
// =====================
export const BRANCH_COLORS = [
  { bg: "bg-slate-50",   border: "border-slate-300",   text: "text-slate-800",   edge: "#5C6B7A", badge: "bg-slate-100 text-slate-600" },
  { bg: "bg-teal-50",    border: "border-teal-300",    text: "text-teal-900",    edge: "#3E6B57", badge: "bg-teal-100 text-teal-700" },
  { bg: "bg-amber-50",   border: "border-amber-300",   text: "text-amber-900",   edge: "#B5821F", badge: "bg-amber-100 text-amber-700" },
  { bg: "bg-rose-50",    border: "border-rose-300",    text: "text-rose-900",    edge: "#B23A2E", badge: "bg-rose-100 text-rose-700" },
  { bg: "bg-indigo-50",  border: "border-indigo-300",  text: "text-indigo-900",  edge: "#4A5A8A", badge: "bg-indigo-100 text-indigo-700" },
  { bg: "bg-stone-50",   border: "border-stone-300",   text: "text-stone-800",   edge: "#8A7A66", badge: "bg-stone-100 text-stone-600" },
  { bg: "bg-cyan-50",    border: "border-cyan-300",    text: "text-cyan-900",    edge: "#3F7E8C", badge: "bg-cyan-100 text-cyan-700" },
  { bg: "bg-orange-50",  border: "border-orange-300",  text: "text-orange-900",  edge: "#B5651F", badge: "bg-orange-100 text-orange-700" },
];

export const LAYOUT_OPTIONS = [
  { value: "auto",              label: "Tự động",         description: "Tự chọn bố cục" },
  { value: "presentation-map",   label: "Trình bày",      description: "Gọn đẹp, nhiều node" },
  { value: "clean-mindmap",    label: "Mindmap sạch",   description: "Giống Miro / Whimsical" },
  { value: "compact-mindmap",   label: "Mindmap gọn",   description: "Nhiều node, tiết kiệm" },
  { value: "tree-compact",      label: "Cây gọn",        description: "Root trên, con dưới" },
  { value: "visual-center",    label: "Sơ đồ trung tâm", description: "Root giữa, nhánh hai bên" },
];

export const DISPLAY_MODES = [
  { value: "overview", label: "Tổng quan" },
  { value: "focus",    label: "Tập trung" },
  { value: "full",     label: "Đầy đủ" },
];

export const EDGE_MODES = [
  { value: "clean",    label: "Gọn" },
  { value: "minimal",  label: "Tối giản" },
  { value: "full",    label: "Đầy đủ dây" },
];

// =====================
// AUTO SELECTORS
// =====================
export const getAutoLayout = (displayMode, nodeCount) => {
  if (displayMode === "overview") return "presentation-map";
  if (nodeCount > 45) return "compact-mindmap";
  return "clean-mindmap";
};

export const getAutoDisplayMode = (nodeCount) => {
  if (nodeCount > 35) return "overview";
  return "full";
};

export const getAutoEdgeMode = (nodeCount) => {
  return nodeCount > 35 ? "clean" : "full";
};

// =====================
// CUSTOM HOOKS
// =====================
export const useIsMobile = (breakpoint = 768) => {
  const [isMobile, setIsMobile] = useState(() => typeof window === "undefined" ? false : window.innerWidth < breakpoint);
  useEffect(() => {
    const handler = () => setIsMobile(window.innerWidth < breakpoint);
    window.addEventListener("resize", handler, { passive: true });
    return () => window.removeEventListener("resize", handler);
  }, [breakpoint]);
  return isMobile;
};

// =====================
// RELATION LABELS (v2 semantic relations — see BE services/mindmap/pipeline/schema.py REL_TYPES)
// =====================
const RELATION_TYPE_LABELS = {
  relates_to: "liên quan",
  leads_to: "dẫn đến",
  causes: "gây ra",
  supports: "hỗ trợ",
  contrasts: "đối lập",
  contains: "bao gồm",
};

export const relationTypeLabel = (type) => RELATION_TYPE_LABELS[type] || "";
