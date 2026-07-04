// Mindmap PNG export (Task 17).
// Follows the official ReactFlow v11 "download image" pattern: compute the
// bounding box of the current nodes with `getRectOfNodes`, derive a viewport
// transform that fits that box into a target canvas with `getTransformForBounds`,
// then rasterize the `.react-flow__viewport` DOM node with html-to-image's
// `toPng` using that transform + a SOLID theme background (never transparent —
// a transparent mindmap export looks broken when viewed outside the app).
import { toPng } from "html-to-image";
import { getRectOfNodes, getTransformForBounds } from "reactflow";

const EXPORT_PADDING = 48;
const MIN_CANVAS = 800;
const MAX_CANVAS = 4000;

// Vietnamese-aware slug: lowercase, "d)/Dd" -> "d" (not affected by NFD, it's an
// atomic letter, not a base+combining-mark composition), strip remaining
// combining diacritics via NFD (Unicode combining diacritical marks block
// U+0300-U+036F), collapse everything else to "-".
export function slugifyTitle(title) {
  const lowered = String(title || "").toLowerCase().replace(/đ/g, "d");
  const stripped = lowered.normalize("NFD").replace(/[̀-ͯ]/g, "");
  const slug = stripped.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return slug || "so-do-tu-duy";
}

const formatDateStamp = (date) => {
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  return `${yyyy}${mm}${dd}`;
};

export const buildMindmapFilename = (title, date = new Date()) =>
  `mindmap-${slugifyTitle(title)}-${formatDateStamp(date)}.png`;

const readThemeBackground = () => {
  if (typeof window === "undefined") return "#ffffff";
  const value = getComputedStyle(document.documentElement).getPropertyValue("--bg-base").trim();
  return value || "#ffffff";
};

// `getNodes` is the ReactFlow instance method (from `useReactFlow()`), not the
// raw node array — kept as a function so callers don't need to re-render this
// module every time nodes change.
export async function exportMindmapPng({ getNodes, title }) {
  const nodes = typeof getNodes === "function" ? getNodes() : [];
  if (!nodes || nodes.length === 0) throw new Error("mindmap-export: no nodes to export");

  const viewportEl = document.querySelector(".react-flow__viewport");
  if (!viewportEl) throw new Error("mindmap-export: viewport element not found");

  const nodesBounds = getRectOfNodes(nodes);
  const width = Math.max(MIN_CANVAS, Math.min(MAX_CANVAS, Math.round(nodesBounds.width + EXPORT_PADDING * 2)));
  const height = Math.max(MIN_CANVAS * 0.6, Math.min(MAX_CANVAS, Math.round(nodesBounds.height + EXPORT_PADDING * 2)));
  // padding là FRACTION (0-1) theo getViewportForBounds — KHÔNG truyền pixel
  const transform = getTransformForBounds(nodesBounds, width, height, 0.2, 2, 0);

  const dataUrl = await toPng(viewportEl, {
    backgroundColor: readThemeBackground(),
    width,
    height,
    style: {
      width: `${width}px`,
      height: `${height}px`,
      transform: `translate(${transform[0]}px, ${transform[1]}px) scale(${transform[2]})`,
    },
  });

  const filename = buildMindmapFilename(title);
  const link = document.createElement("a");
  link.download = filename;
  link.href = dataUrl;
  link.click();
  return filename;
}
