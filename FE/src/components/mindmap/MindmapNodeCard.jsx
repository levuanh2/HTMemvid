// Mindmap viewer — custom ReactFlow node card ("napkin" style).
// Extracted mechanically from MindMapModal.jsx (Task 14 split). No behavior change,
// except: added "section"/"detail" icons (v2 node kinds) and a keyboard focus ring
// (quality floor — visible focus for keyboard navigation).
import { Handle, Position } from "reactflow";

// =====================
// ICON
// =====================
const getNapkinIcon = (icon, type) => {
  const map = {
    brain: "🧠", database: "🗄️", workflow: "🔁", target: "🎯", alert: "⚠️", check: "✅",
    lightbulb: "💡", clock: "🕒", sparkles: "✨", root: "🧠", concept: "💡", process: "⚙️",
    input: "📥", output: "📤", problem: "⚠️", solution: "✅", example: "📌", risk: "⚠️",
    insight: "✨", timeline: "🕒", metric: "📊",
    // v2 node kinds (schema_version 2): root/section/idea/detail
    section: "🗂️", idea: "💡", detail: "📄",
  };
  return map[icon || type] || "💡";
};

// =====================
// NODE
// =====================
export const NapkinNode = ({ data }) => {
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
    ? "border-rose-300 bg-rose-50 shadow-md"
    : level === 1 && bc ? `${bc.bg} ${bc.border} shadow-sm`
    : ["problem", "risk"].includes(type) ? "border-amber-200 bg-amber-50 shadow-sm"
    : ["solution", "output"].includes(type) ? "border-emerald-200 bg-emerald-50 shadow-sm"
    : ["process", "workflow"].includes(type) ? "border-sky-200 bg-sky-50 shadow-sm"
    : "border-slate-200 bg-white shadow-sm";

  const textSz   = isMobile ? (isRoot ? "text-sm" : "text-[11px]") : (isRoot ? "text-base" : "text-sm");
  const toggleSz = isMobile ? "h-7 w-7 text-base" : "h-6 w-6 text-sm";

  return (
    <div
      className={["relative rounded-2xl border px-3 py-2.5 text-slate-800 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-md motion-reduce:transition-none motion-reduce:hover:translate-y-0", isRoot ? "min-w-[180px] max-w-[280px]" : level === 1 ? "min-w-[160px] max-w-[230px]" : "min-w-[140px] max-w-[210px]", toneClass].join(" ")}
      onMouseEnter={() => data?.onHover?.(data.id)}
      onMouseLeave={() => data?.onHover?.(null)}
    >
      <Handle id="top"    type="target" position={Position.Top}    className="!opacity-0 !pointer-events-none" />
      <Handle id="right"  type="target" position={Position.Right}  className="!opacity-0 !pointer-events-none" />
      <Handle id="bottom" type="target" position={Position.Bottom} className="!opacity-0 !pointer-events-none" />
      <Handle id="left"   type="target" position={Position.Left}   className="!opacity-0 !pointer-events-none" />

      {hasChildren && (
        <button type="button" onClick={(e) => { e.stopPropagation(); e.preventDefault(); data?.onToggle?.(data.id); }}
          className={`absolute -right-2.5 -top-2.5 z-10 flex items-center justify-center rounded-full border bg-white text-slate-500 shadow-sm transition-transform hover:scale-110 hover:bg-rose-50 hover:border-rose-300 hover:text-rose-700 ${toggleSz}`}
          title={isExpanded ? "Thu gọn" : "Mở rộng"}>
          {isExpanded ? "−" : "+"}
        </button>
      )}

      <div className="flex items-start gap-2">
        <div className={["flex shrink-0 items-center justify-center rounded-xl", isMobile ? "h-7 w-7 text-sm" : "h-8 w-8 text-base", isRoot ? "bg-rose-100" : level === 1 && bc ? bc.bg : "bg-slate-50"].join(" ")}>
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

export default NapkinNode;
