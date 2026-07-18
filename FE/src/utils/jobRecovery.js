// PR#8 UX recovery — logic THUẦN tách khỏi SidebarRight để test được ở env node
// (repo chưa có jsdom; component không render trong test — pattern mindmapViewport).

import { STALL_MS } from "./jobPoller";

// Guard "Tạo lại" khi sơ đồ đang có chỉnh sửa chưa lưu: dirty → hỏi trước,
// user từ chối → KHÔNG regenerate. Không dirty → đi thẳng như cũ.
export function confirmRegenerateIfDirty(dirty, confirmFn) {
  if (!dirty) return true;
  return Boolean(confirmFn(
    "Sơ đồ có thay đổi chưa lưu — Tạo lại sẽ thay thế chúng. Lưu trước hoặc bấm OK để bỏ thay đổi."
  ));
}

// Stall banner có "Chờ tiếp": bấm = snooze thêm một cửa sổ STALL_MS nữa rồi mới
// nhắc lại (poller vẫn poll bình thường — đây chỉ là hiển thị, KHÔNG auto-cancel,
// KHÔNG hard-timeout).
export function stallBannerVisible(stalled, dismissedAt, nowMs, snoozeMs = STALL_MS) {
  if (!stalled) return false;
  if (!dismissedAt) return true;
  return nowMs - dismissedAt > snoozeMs;
}

// Retry context: chỉ đưa nút "Thử lại" khi còn đủ ngữ cảnh chạy lại job
// (sources của lần chạy trước). Thiếu → UI rơi về nút Tạo bình thường.
export function canRetry(ctx) {
  return Boolean(ctx && Array.isArray(ctx.sources) && ctx.sources.length > 0);
}
