// Viewport math + keymap cho toolbar sơ đồ. THUẦN: không import React, không import
// mind-elixir — chạy được ở env `node` mặc định của vitest (repo chưa có jsdom).
// Pan/zoom đã do mind-elixir lo (wheel/ctrl+wheel/kéo nền/touch); file này chỉ phục vụ
// phần chrome (nút bấm, readout, phím tắt).

// Mirror hằng số mặc định của mind-elixir 5.13 — dist/MindElixir.js:
//   this.scaleMin = E ?? 0.2 ; this.scaleMax = w ?? 1.4
// CHỈ dùng làm fallback: luôn ưu tiên `mind.scaleMin`/`mind.scaleMax` của instance thật,
// vì guard trong `scale()` của thư viện đọc chính 2 field đó. Nâng version mind-elixir →
// kiểm lại 2 số này.
export const DEFAULT_SCALE_MIN = 0.2;
export const DEFAULT_SCALE_MAX = 1.4;

// Bước zoom của nút bấm — giữ nguyên 0.2 như `zoomBy` cũ (khác `scaleSensitivity: 0.1`
// mà thư viện dùng cho wheel; nút bấm nhảy bước to hơn là có chủ đích).
export const ZOOM_STEP = 0.2;

/**
 * Kẹp scale vào [min, max].
 *
 * Vì sao phải guard non-finite: `scaleFit()` của mind-elixir tính
 * `this.nodes.offsetHeight / this.container.offsetHeight` — container ẩn (display:none,
 * modal chưa layout) cho 0/0 = NaN → scaleVal = NaN → `transform: scale(NaN)` làm vỡ map
 * và readout in "NaN%". Non-finite → rơi về 1 (đã kẹp), KHÔNG bao giờ trả NaN ra ngoài.
 */
export function clampScale(value, { min = DEFAULT_SCALE_MIN, max = DEFAULT_SCALE_MAX } = {}) {
  const lo = Number.isFinite(min) ? min : DEFAULT_SCALE_MIN;
  const hi = Number.isFinite(max) ? max : DEFAULT_SCALE_MAX;
  if (!Number.isFinite(value)) return Math.min(hi, Math.max(lo, 1));
  return Math.min(hi, Math.max(lo, value));
}

/** Scale kế tiếp khi bấm +/- (hoặc phím tắt). Kẹp trong [min, max]. */
export function nextScale(current, delta, { min = DEFAULT_SCALE_MIN, max = DEFAULT_SCALE_MAX } = {}) {
  const base = Number.isFinite(current) ? current : 1;
  const step = Number.isFinite(delta) ? delta : 0;
  return clampScale(base + step, { min, max });
}

/** 1 → "100%", 0.4 → "40%". Làm tròn, không phần thập phân. */
export function formatZoom(scale) {
  const v = Number.isFinite(scale) ? scale : 1;
  return `${Math.round(v * 100)}%`;
}

/**
 * Map phím → hành động viewport. Trả null = không phải phím của mình (để yên cho
 * người khác xử lý, gồm cả Escape của viewer).
 *
 * KHÔNG nhận khi có ctrl/meta/alt — mind-elixir đã tự bind `Ctrl/Cmd + =/-/0` trên
 * container (dist/MindElixir.js, bảng keymap của `On()`); nhường hết cho thư viện để
 * không double-zoom. Bare +/-/0/f thư viện KHÔNG bind → an toàn.
 *
 * `isEditing` là guard phòng thủ: khi đang gõ text node, editor của mind-elixir đã
 * `stopPropagation()` mọi keydown nên listener ở window vốn không nhận được; guard này
 * chặn nốt các ô input khác (drawer, form) nếu sau này có.
 */
export function viewportKeyAction(event, { isEditing = false } = {}) {
  if (!event || isEditing) return null;
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  switch (event.key) {
    case "+":
    case "=":
      return "in";
    case "-":
    case "_":
      return "out";
    case "0":
      return "reset";
    case "f":
    case "F":
      return "fit";
    default:
      return null;
  }
}
