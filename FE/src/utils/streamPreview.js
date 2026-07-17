// Throttle flush của streaming preview.
//
// SSE bắn token dày đặc; mỗi setState kéo ReactMarkdown re-parse TOÀN BỘ text
// đã tích luỹ → O(n²) theo độ dài câu trả lời. Gom token ngoài state (ref),
// flush state tối đa mỗi intervalMs: preview vẫn mượt với text (~7 lần/giây),
// chi phí parse không phình theo số token.
//
// Pure + inject được setTimeout/clearTimeout qua tham số → unit test bằng fake
// timers, không cần jsdom (pattern mindmapViewport.js).

export const STREAM_PREVIEW_FLUSH_MS = 150;

export function createPreviewThrottle(flush, intervalMs = STREAM_PREVIEW_FLUSH_MS) {
  let timer = null;
  let lastFlush = 0;

  const fire = () => {
    timer = null;
    lastFlush = Date.now();
    flush();
  };

  return {
    // Gọi mỗi token. Đã có timer chờ → no-op (token tiếp theo đi cùng chuyến flush).
    schedule() {
      if (timer !== null) return;
      const wait = Math.max(0, intervalMs - (Date.now() - lastFlush));
      timer = setTimeout(fire, wait);
    },
    // Huỷ timer đang chờ (stream kết thúc/lỗi/cancel) — flush cuối do caller
    // tự quyết (done đọc thẳng ref, không cần flush thêm).
    cancel() {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    },
    pending() {
      return timer !== null;
    },
  };
}
