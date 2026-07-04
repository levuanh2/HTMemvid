// Mindmap viewer — header toolbar (display/edge/layout controls, action buttons,
// contextual warnings) + the v2 degraded banner.
// Header controls are a mechanical extraction from MindMapModal.jsx (Task 14 split).
// New in Task 14: "Quan hệ" (relations) toggle + degraded banner with "Tạo lại".
import { Icon } from "../ui/Icon";
import { DISPLAY_MODES, EDGE_MODES, LAYOUT_OPTIONS } from "./constants";

export default function MindmapToolbar({
  title,
  displayMode, onDisplayModeChange,
  edgeMode, onEdgeModeChange,
  layoutType, onLayoutChange,
  visibleCount, totalCount,
  onCenterView, onExpandAll, onCollapseAll, onClose,
  showLargeWarning, showFullHint, showOverviewHint,
  hasRelations, relationsVisible, onToggleRelations,
  degraded, missing, onRegenerate, regenerating,
  onExportPng, exportingPng,
}) {
  return (
    <>
      <div className="flex-shrink-0 bg-surface-sidebar border-b border-border px-3 py-2 flex flex-col gap-2 md:flex-row md:items-center md:justify-between md:gap-3 md:px-4 md:py-2.5">
        <div className="flex flex-wrap items-center gap-2 min-w-0 md:gap-3">
          <h3 className="font-display font-semibold text-text-primary truncate text-sm md:text-[15px] flex items-center gap-1.5">
            <Icon name="Network" size={15} className="text-brand flex-shrink-0" /> {title || "Sơ đồ tư duy"}
          </h3>

          {/* Display mode */}
          <div className="flex rounded-[7px] border border-border overflow-hidden bg-surface-elevated text-[11px]">
            {DISPLAY_MODES.map((mode) => (
              <button key={mode.value} onClick={() => onDisplayModeChange(mode.value)}
                className={`px-2 py-1.5 transition-colors ${displayMode === mode.value ? "bg-surface-card font-semibold text-brand shadow-card" : "text-text-muted hover:bg-surface-hover"}`}>
                {mode.label}
              </button>
            ))}
          </div>

          {/* Edge mode */}
          <div className="flex rounded-[7px] border border-border overflow-hidden bg-surface-elevated text-[11px]">
            {EDGE_MODES.map((mode) => (
              <button key={mode.value} onClick={() => onEdgeModeChange(mode.value)}
                className={`px-2 py-1.5 transition-colors ${edgeMode === mode.value ? "bg-surface-card font-semibold text-brand shadow-card" : "text-text-muted hover:bg-surface-hover"}`}>
                {mode.label}
              </button>
            ))}
          </div>

          {/* Relations toggle (v2 semantic edges) */}
          {hasRelations && (
            <button
              onClick={onToggleRelations}
              aria-pressed={relationsVisible}
              title={relationsVisible ? "Ẩn các quan hệ phụ" : "Hiện các quan hệ phụ"}
              className={`flex items-center gap-1 rounded-[7px] border px-2 py-1.5 text-[11px] transition-colors ${relationsVisible ? "border-border bg-surface-card font-semibold shadow-card" : "border-border bg-surface-elevated text-text-muted hover:bg-surface-hover"}`}
              style={relationsVisible ? { color: "var(--accent)" } : undefined}
            >
              <Icon name="GitBranch" size={12} /> Quan hệ
            </button>
          )}

          {/* Layout select */}
          <label className="flex items-center gap-1.5 text-text-secondary text-[11px]">
            <span className="hidden sm:inline whitespace-nowrap">Bố cục:</span>
            <select value={layoutType} onChange={(e) => onLayoutChange(e.target.value)}
              className="input-surface !py-1.5 !px-2 text-[11px] md:!py-1.5 md:!px-3 md:text-[12px] min-w-[100px]">
              {LAYOUT_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
            </select>
          </label>

          <span className="text-[10px] md:text-[11px] text-text-muted whitespace-nowrap">{visibleCount}/{totalCount} nút</span>
        </div>

        <div className="flex items-center gap-1.5 flex-wrap">
          <button onClick={onCenterView} className="btn-secondary px-2 py-1.5 text-[11px] gap-1" title="Căn giữa sơ đồ"><Icon name="Maximize2" size={13} /></button>
          <button onClick={onExpandAll} className="btn-secondary px-2 py-1.5 text-[11px] gap-1"><Icon name="Plus" size={13} /><span className="hidden sm:inline">Mở hết</span></button>
          <button onClick={onCollapseAll} className="btn-secondary px-2 py-1.5 text-[11px] gap-1"><Icon name="Minus" size={13} /><span className="hidden sm:inline">Thu hết</span></button>
          {onExportPng && (
            <button
              onClick={onExportPng}
              disabled={exportingPng}
              className="btn-secondary px-2 py-1.5 text-[11px] gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
              title="Xuất sơ đồ ra ảnh PNG"
            >
              <Icon name="Download" size={13} /><span className="hidden sm:inline">{exportingPng ? "Đang xuất…" : "Xuất PNG"}</span>
            </button>
          )}
          <button onClick={onClose} className="btn-secondary px-2 py-1.5 text-[11px] gap-1"><Icon name="X" size={13} /><span className="hidden sm:inline">Đóng</span></button>
        </div>
      </div>

      {degraded && (
        <div className="border-b px-4 py-1.5 text-[11px] text-center flex items-center justify-center gap-2 flex-wrap"
          style={{ background: "color-mix(in srgb, var(--accent) 10%, transparent)", borderColor: "color-mix(in srgb, var(--accent) 30%, transparent)", color: "var(--accent)" }}>
          <span>
            Bản đồ chưa đầy đủ{missing?.length ? ` (thiếu: ${missing.join(", ")})` : ""}
          </span>
          {onRegenerate && (
            <button
              onClick={onRegenerate}
              disabled={regenerating}
              className="underline font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {regenerating ? "Đang tạo lại…" : "Tạo lại"}
            </button>
          )}
        </div>
      )}

      {showLargeWarning && (
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-1.5 text-[11px] text-amber-700 text-center">
          ⚠️ Sơ đồ lớn ({totalCount} nút) — nên dùng <strong>Tổng quan</strong> hoặc <strong>Tập trung</strong>.
        </div>
      )}

      {showFullHint && (
        <div className="bg-sky-50 border-b border-sky-200 px-4 py-1.5 text-[11px] text-sky-700 text-center">
          📋 Đang xem đầy đủ node, dây đang ở chế độ <strong>Gọn</strong> để dễ đọc. Chuyển sang <strong>Đầy đủ dây</strong> để xem tất cả.
        </div>
      )}

      {showOverviewHint && (
        <div className="bg-violet-50 border-b border-violet-200 px-4 py-1.5 text-[11px] text-violet-700 text-center">
          📋 Tổng quan ({visibleCount}/{totalCount} nút) — chuyển <strong>Đầy đủ</strong> để xem toàn bộ.
        </div>
      )}
    </>
  );
}
