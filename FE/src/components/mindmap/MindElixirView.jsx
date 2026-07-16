// Viewer mind-elixir — thay ReactFlow/ELK. Overlay fullscreen giữ từ v2.
import { useEffect, useRef, useState, useCallback } from "react";
import MindElixir from "mind-elixir";
// BẮT BUỘC: toàn bộ layout của mind-elixir (me-nodes flex, me-tpc block, gaps)
// nằm trong CSS này — thiếu nó mọi node rơi về display:inline và sơ đồ vỡ hoàn toàn.
import "mind-elixir/style";
import { snapdom } from "@zumer/snapdom";
import { recordToMindElixir, mindElixirToRecord } from "../../utils/mindElixirAdapter";
import { nextScale, formatZoom, viewportKeyAction, ZOOM_STEP } from "../../utils/mindmapViewport";
import { updateMindmap } from "../../utils/api";
import { toast } from "../ui/Toaster";
import EvidenceDrawer from "./EvidenceDrawer";
import { Icon } from "../ui/Icon";
import Spinner from "../ui/Spinner";
import "./mindmap.css";

// Palette nhánh: archival ink hexes (Phòng đọc theme) — trước đây sống ở
// constants.js::BRANCH_COLORS (file đã xoá cùng ReactFlow view ở Task 9).
const PALETTE = ["#5C6B7A", "#3E6B57", "#B5821F", "#B23A2E", "#4A5A8A", "#8A7A66"];

// MindElixir.css tiêu thụ đủ bộ var dưới đây KHÔNG có fallback — thiếu var nào
// là declaration đó invalid và spacing/màu sụp đổ. Phải set đủ (guard bằng test
// THEME_REQUIRED_VARS). Màu để dạng var(--token) → tự flip light/dark theo html.dark.
export const THEME = {
  name: "PhongDoc",
  palette: PALETTE,
  cssVar: {
    // hình học — nhịp lề giấy Phòng đọc, card chứ không pill
    "--map-padding": "60px 100px",
    "--main-gap-x": "72px",
    "--main-gap-y": "36px",
    "--node-gap-x": "32px",
    "--node-gap-y": "8px",
    "--root-radius": "8px",
    "--main-radius": "6px",
    "--topic-padding": "4px",
    // root = khối mực (ink), chữ màu giấy
    "--root-color": "var(--bg-base)",
    "--root-bgcolor": "var(--text-primary)",
    "--root-border-color": "transparent",
    // section = thẻ giấy nổi, viền đậm
    "--main-color": "var(--text-primary)",
    "--main-bgcolor": "var(--bg-card)",
    "--main-border": "1px solid var(--border-strong)",
    "--main-bgcolor-transparent": "transparent",
    // idea/detail = chữ trần trên nền
    "--color": "var(--text-secondary)",
    "--bgcolor": "transparent",
    // selection/active — seal đỏ hợp lệ (active state, không decorative)
    "--selected": "var(--accent)",
    "--accent-color": "var(--accent)",
    // context-menu panel
    "--panel-color": "var(--text-primary)",
    "--panel-bgcolor": "var(--bg-card)",
    "--panel-border-color": "var(--border-color)",
  },
};

export default function MindElixirView({ data, onClose, onRegenerate, regenerating }) {
  const containerRef = useRef(null);
  const mindRef = useRef(null);
  const sidecarRef = useRef(new Map());
  const [selected, setSelected] = useState(null);   // node cho EvidenceDrawer
  const [showRelations, setShowRelations] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [zoom, setZoom] = useState(1);              // readout — nguồn sự thật là bus "scale"
  // Lỗi Lưu/Xuất PNG. Trước đây CHỈ có toast — toast tự tắt sau vài giây, mà lỗi lưu thì
  // map vẫn đang dirty: user quay đi quay lại là mất luôn lý do hỏng, tưởng đã lưu xong.
  // Banner ở lại tới khi tự đóng hoặc tới lần thao tác sau.
  const [errorMsg, setErrorMsg] = useState(null);

  const degraded = Boolean(data?.generator?.degraded);
  const missing = data?.generator?.missing || [];
  // "Tạo lại" đang chạy nền (SidebarRight bơm generating/progress/onCancel vào
  // data). Overlay này che luôn progress chip của sidebar → phải có banner +
  // nút Huỷ NGAY TRONG viewer, giữ parity với MindmapView cũ.
  const generating = Boolean(data?.generating);

  // (re)init khi đổi record
  useEffect(() => {
    if (!containerRef.current || !data) return;
    // Record mới (vd tạo lại xong) → xoá sạch state phiên cũ, nếu không badge
    // "chưa lưu" và EvidenceDrawer trỏ node cũ sống sót qua re-init (codex #8).
    setDirty(false);
    setSaving(false);
    setSelected(null);
    setZoom(1);
    setErrorMsg(null);   // record mới → lỗi của phiên cũ không được sống sót qua re-init
    const { mindData, sidecar } = recordToMindElixir(data);
    sidecarRef.current = sidecar;
    const mind = new MindElixir({
      el: containerRef.current,
      direction: MindElixir.SIDE,
      editable: true,       // gate kéo node (re-parent/đổi thứ tự) — `draggable` đã deprecated
      contextMenu: true,
      toolBar: false,       // toolbar riêng của mình
      keypress: true,
      allowUndo: true,
      // 2 = chuột PHẢI box-select → kéo-TRÁI trên nền = pan canvas (trực quan hơn
      // mặc định bắt Space+kéo).
      mouseSelectionButton: 2,
      theme: THEME,
    });
    mind.init(mindData);
    mindRef.current = mind;
    setZoom(mind.scaleVal || 1);

    // Readout thu phóng. Thư viện fire "scale" (number) ở MỌI đường đổi scale —
    // nút bấm, ctrl+wheel, VÀ scaleFit() (verified dist/MindElixir.js: `fn` kết thúc
    // bằng `this.bus.fire("scale", n)`) — nên chỉ cần nghe một chỗ này.
    // KHÔNG kẹp giá trị ở đây: scaleFit() không đọc scaleMin nên map rất lớn có thể
    // xuống dưới 0.2; kẹp readout sẽ hiện số SAI so với map đang vẽ.
    mind.bus.addListener("scale", (v) => setZoom(v));
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

  // Task 8: explicit Save → PUT /mindmaps/<id>. Only reachable when the button
  // is rendered (data.id real + not "preview" + not generating — see JSX
  // below), so `data.id` is safe to PUT here.
  const handleSave = async () => {
    const mind = mindRef.current;
    // `saving` in the guard: the button's disabled attr alone isn't a guarantee
    // against double invocation (e.g. a queued second click before re-render).
    if (!mind || !dirty || saving) return;
    setSaving(true);
    setErrorMsg(null);   // thử lại → bỏ lỗi lần trước, đừng để banner cũ gây hiểu nhầm
    try {
      const record = mindElixirToRecord(mind.getData(), sidecarRef.current, data);
      const saved = await updateMindmap(data.id, record);
      setDirty(false);
      toast("Đã lưu sơ đồ", { type: "success" });
      data.onSaved?.(saved); // SidebarRight bơm callback để cập nhật list + showModalMap
    } catch (err) {
      // Banner (không phải toast): map còn dirty, lý do hỏng phải ở lại trước mắt user.
      setErrorMsg(`Không lưu được: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  // Đọc trần/sàn từ CHÍNH instance (mind-elixir 5.13 set `this.scaleMin = 0.2`,
  // `this.scaleMax = 1.4` trong constructor) thay vì hardcode: guard trong `scale()` của
  // thư viện là REJECT chứ không phải clamp —
  //   `if (e < this.scaleMin && e < this.scaleVal || e > this.scaleMax && e > this.scaleVal) return`
  // — nên clamp cũ 0.4–2 vượt trần thật 1.4: nút "Phóng to" im lặng chết ở 1.4 mà không
  // báo gì. Kẹp theo số của instance thì scale() luôn nhận và readout luôn khớp map.
  const zoomBy = useCallback((delta) => {
    const mind = mindRef.current;
    if (!mind) return;
    mind.scale(nextScale(mind.scaleVal, delta, { min: mind.scaleMin, max: mind.scaleMax }));
  }, []);

  // scaleFit() tự căn theo bounding box của nodes (dist: `Ce(this, !0)` — tham số `true`
  // ÉP nhánh căn-theo-nodes bất kể option `alignment`), nên KHÔNG cần truyền
  // `alignment: "nodes"` vào constructor và không đụng gì tới `toCenter()` mặc định.
  // Nó chỉ thu nhỏ, không phóng to quá 100% (`1 / Math.max(1, ...)`).
  // `?.()` phòng version drift: thiếu method thì no-op, không ném trong onClick.
  const fitView = useCallback(() => { mindRef.current?.scaleFit?.(); }, []);

  // toCenter() GIỮ NGUYÊN scaleVal (dist: `pn` vẽ lại transform với `scale(${this.scaleVal})`)
  // → "về khung nhìn gốc" phải gọi CẢ HAI, scale trước rồi mới căn giữa.
  const resetView = useCallback(() => {
    const mind = mindRef.current;
    if (!mind) return;
    mind.scale(1);
    mind.toCenter();
  }, []);

  // Click vào readout = chỉ trả thu phóng về 100%, giữ nguyên vị trí đang xem.
  const resetZoom = useCallback(() => { mindRef.current?.scale(1); }, []);

  const handleExportPng = async () => {
    const mind = mindRef.current;
    // Chụp mind.map (.map-canvas) chứ KHÔNG phải mind.nodes: rule layout then chốt
    // là descendant selector `.map-canvas me-nodes{display:flex}` — clone me-nodes
    // tách khỏi .map-canvas sẽ không match và PNG vỡ (text dồn 1 dòng).
    const target = mind?.map;
    if (!target) return;

    setErrorMsg(null);
    try {
      const backgroundColor =
        getComputedStyle(document.documentElement).getPropertyValue("--bg-base").trim() || "#ECE7DB";
      const result = await snapdom(target, { backgroundColor, scale: 2 });
      const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      const safeTitle = String(data?.title || "mindmap").replace(/[\\/:*?"<>|]+/g, "_").slice(0, 60);
      await result.download({ format: "png", filename: `mindmap-${safeTitle}-${date}` });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setErrorMsg(`Không xuất được PNG: ${message}`);
    }
  };

  // Esc đóng (confirm khi dirty — Task 8 nối)
  const requestClose = useCallback(() => {
    if (dirty && !window.confirm("Có thay đổi chưa lưu. Đóng và bỏ thay đổi?")) return;
    onClose?.();
  }, [dirty, onClose]);
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") { requestClose(); return; }
      // Đang gõ tên node thì editor của mind-elixir đã stopPropagation() mọi keydown
      // (verified dist) nên listener window này vốn không nhận được — guard vẫn giữ để
      // chặn các ô nhập khác (drawer/form) và không phụ thuộc chi tiết nội bộ thư viện.
      const ae = document.activeElement;
      const isEditing = Boolean(
        ae && (ae.isContentEditable || ae.closest?.("me-tpc") || /^(INPUT|TEXTAREA|SELECT)$/.test(ae.tagName))
      );
      const action = viewportKeyAction(e, { isEditing });
      if (!action) return;   // gồm cả Ctrl/Cmd +/-/0 — nhường keymap sẵn có của mind-elixir
      e.preventDefault();
      if (action === "in") zoomBy(ZOOM_STEP);
      else if (action === "out") zoomBy(-ZOOM_STEP);
      else if (action === "reset") resetView();
      else if (action === "fit") fitView();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [requestClose, zoomBy, resetView, fitView]);

  return (
    <div className="fixed inset-0 z-[1000] flex flex-col" style={{ background: "var(--bg-base)" }}>
      {/* Toolbar — chrome Phòng đọc: kicker mono + tiêu đề Spectral, control là icon-btn */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 border-b flex-shrink-0"
        style={{ borderColor: "var(--border-color)", background: "var(--bg-sidebar)" }}>
        <div className="min-w-0">
          <div className="font-mono text-[10px] tracking-[0.14em] uppercase" style={{ color: "var(--text-secondary)" }}>
            Sơ đồ tư duy
          </div>
          <div className="font-display text-[14px] font-semibold truncate text-text-primary">
            {data?.title || "Sơ đồ tư duy"}
          </div>
        </div>
        {dirty && <span className="text-[11px] px-1.5 rounded" style={{ color: "var(--warn)" }}>● chưa lưu</span>}
        <div className="flex-1" />
        <button onClick={() => zoomBy(-ZOOM_STEP)} aria-label="Thu nhỏ" title="Thu nhỏ (−)"
          className="p-1.5 rounded hover:bg-[var(--bg-hover)] text-text-secondary">
          <Icon name="ZoomOut" size={16} />
        </button>
        {/* Readout — vừa là mức thu phóng hiện tại, vừa là affordance dạy user rằng
            canvas là một khung nhìn di chuyển được (không phải ảnh tĩnh). */}
        <button onClick={resetZoom} aria-label="Đặt lại thu phóng" title="Đặt lại thu phóng (100%)"
          className="px-1.5 py-1 rounded hover:bg-[var(--bg-hover)] font-mono text-[11px] tabular-nums text-text-secondary min-w-[46px]">
          {formatZoom(zoom)}
        </button>
        <button onClick={() => zoomBy(+ZOOM_STEP)} aria-label="Phóng to" title="Phóng to (+)"
          className="p-1.5 rounded hover:bg-[var(--bg-hover)] text-text-secondary">
          <Icon name="ZoomIn" size={16} />
        </button>
        <button onClick={fitView} aria-label="Vừa khung" title="Vừa khung (F)"
          className="flex items-center gap-1 px-2 py-1.5 rounded hover:bg-[var(--bg-hover)] text-[12px] text-text-secondary">
          <Icon name="Scan" size={14} /> Vừa khung
        </button>
        <button onClick={resetView} aria-label="Đặt lại khung nhìn" title="Đặt lại khung nhìn (0)"
          className="flex items-center gap-1 px-2 py-1.5 rounded hover:bg-[var(--bg-hover)] text-[12px] text-text-secondary">
          <Icon name="RotateCcw" size={14} /> Đặt lại
        </button>
        <button onClick={() => mindRef.current?.toCenter()} aria-label="Căn giữa" title="Căn giữa"
          className="p-1.5 rounded hover:bg-[var(--bg-hover)] text-text-secondary">
          <Icon name="Maximize" size={16} />
        </button>
        <button onClick={() => setShowRelations((v) => !v)} aria-pressed={showRelations}
          aria-label="Bật/tắt quan hệ" title="Quan hệ"
          className="p-1.5 rounded hover:bg-[var(--bg-hover)]"
          style={{ color: showRelations ? "var(--text-primary)" : "var(--text-secondary)", opacity: showRelations ? 1 : 0.5 }}>
          <Icon name="Spline" size={16} />
        </button>
        <button onClick={handleExportPng} aria-label="Xuất PNG" title="Xuất PNG"
          className="flex items-center gap-1 px-2 py-1.5 rounded hover:bg-[var(--bg-hover)] text-[12px] text-text-secondary">
          <Icon name="Download" size={14} /> Xuất PNG
        </button>
        {/* Nút Lưu — chỉ hiện khi record đã có id thật trong sqlite (không phải
            "preview" transient) và không đang generating, tránh PUT 404. */}
        {data?.id && data.id !== "preview" && !data.generating && (
          <button onClick={handleSave} disabled={!dirty || saving} aria-label="Lưu sơ đồ"
            className="btn-primary text-[12px] disabled:opacity-40">
            {saving ? "Đang lưu…" : "Lưu"}
          </button>
        )}
        <button onClick={requestClose} aria-label="Đóng" className="p-1.5 rounded hover:bg-[var(--bg-hover)]">
          <Icon name="X" size={16} />
        </button>
      </div>
      {/* Error banner — Lưu/Xuất PNG hỏng. role="alert" để screen reader đọc ngay, không
          phải chờ user mò tới. Đứng trên banner generating/degraded vì đây là thứ user
          vừa bấm và đang chờ kết quả. */}
      {errorMsg && (
        <div role="alert" className="px-3 py-1.5 text-[12px] flex items-center gap-2 border-b flex-shrink-0"
          style={{ color: "var(--err)", borderColor: "var(--border-color)", background: "var(--bg-elevated)" }}>
          <Icon name="TriangleAlert" size={14} />
          <span className="min-w-0 flex-1">{errorMsg}</span>
          <button onClick={() => setErrorMsg(null)} aria-label="Đóng thông báo lỗi"
            className="p-0.5 rounded hover:bg-[var(--bg-hover)]">
            <Icon name="X" size={13} />
          </button>
        </div>
      )}
      {/* Generating banner — overlay che chip tiến độ ở sidebar nên Huỷ phải ở đây */}
      {generating && (
        <div className="px-3 py-1.5 text-[12px] flex items-center gap-2 border-b"
          style={{ color: "var(--text-secondary)", borderColor: "var(--border-color)", background: "var(--bg-elevated)" }}>
          <Spinner size={12} />
          <span>
            Đang tạo lại sơ đồ…{typeof data?.progress === "number" ? ` (${data.progress}%)` : ""}
            {/* Honest mitigation: when the regenerate finishes, SidebarRight swaps
                the record → this viewer re-inits and dirty edits are discarded.
                Full prevention needs dirty-state plumbing to the parent (tracked
                as a known issue) — for now, at least say so. */}
            {dirty ? " — thay đổi chưa lưu sẽ bị thay thế khi bản mới sẵn sàng." : ""}
          </span>
          {typeof data?.onCancel === "function" && (
            <button onClick={data.onCancel} className="underline" style={{ color: "var(--accent)" }}>
              Huỷ
            </button>
          )}
        </div>
      )}
      {/* Degraded banner giữ từ v2 — ẩn nút Tạo lại khi đang generate (tránh double-trigger) */}
      {degraded && (
        <div className="px-3 py-1.5 text-[12px] flex items-center gap-2 border-b"
          style={{ color: "var(--warn)", borderColor: "var(--border-color)", background: "var(--bg-elevated)" }}>
          <span>Bản đồ chưa đầy đủ{missing.length ? ` (thiếu: ${missing.join(", ")})` : ""}.</span>
          {!generating && (
            <button onClick={onRegenerate} disabled={regenerating} className="underline">
              {regenerating ? "Đang tạo lại…" : "Tạo lại"}
            </button>
          )}
        </div>
      )}
      {/* Map + legend (legend là sibling — cleanup xoá innerHTML của container
          nên không được đặt con React bên trong div ref) */}
      <div className="relative flex-1 min-h-0 overflow-hidden">
        {/* Ref target owns h/w-full (normal flow) — mind-elixir sets el.style.position
            = "relative" inline (verified dist), which defeats `absolute inset-0` (inline
            beats class) and collapses the container to content height, breaking scaleFit
            and leaving dead pan area. w/h-full fills the sized wrapper instead. */}
        <div ref={containerRef} className={`h-full w-full min-h-0 me-container${showRelations ? "" : " me-hide-arrows"}`} />
        <div className="mm-legend" aria-hidden="true">
          <span><span className="swatch" style={{ background: "var(--text-primary)" }} />chủ đề</span>
          <span><span className="swatch" style={{ background: "var(--bg-card)", border: "1px solid var(--border-strong)" }} />mục</span>
          <span><span className="font-mono" style={{ color: "var(--accent)" }}>※</span> có trích đoạn</span>
          <span><span className="dash" />quan hệ</span>
          <span className="mm-legend-hint">kéo node → chuyển nhánh · kéo nền → di chuyển</span>
        </div>
      </div>
      {/* Evidence drawer giữ nguyên component */}
      {selected && (
        <EvidenceDrawer node={selected} onClose={() => setSelected(null)}
          generating={generating} onAskAbout={data?.onAskAbout} />
      )}
    </div>
  );
}
