// Wrapper mindmap trên poller chung (utils/jobPoller.js) — giữ nguyên API cũ.
import { createJobPoller } from "./jobPoller";

export { STALL_MS, pollIntervalMs, MAX_CONSECUTIVE_FETCH_FAILURES } from "./jobPoller";

export const stageLabel = (status = {}) => {
  const node = String(status.current_node || "");
  const msg = String(status.message || "");
  if (/skeleton|collect/i.test(node)) return "Dựng khung xương…";
  if (/enrich/i.test(node)) return msg ? `Làm giàu ${msg}…` : "Làm giàu nhánh…";
  if (/relation/i.test(node)) return "Tìm quan hệ chéo…";
  if (/assemble|persist/i.test(node)) return "Đang lưu sơ đồ…";
  return "Đang tạo sơ đồ…";
};

export const createMindmapPoller = (opts) =>
  createJobPoller({
    ...opts,
    messages: {
      notFound: "Không tìm thấy job trên server (có thể đã bị dọn). Hãy tạo lại sơ đồ.",
      lost: (n) => `Mất liên lạc với server sau ${n} lần thử. Hãy tạo lại sơ đồ.`,
      error: "Lỗi khi tạo sơ đồ.",
    },
    fingerprintExtra: (s) => s?.partial?.nodes?.length ?? 0,
  });
