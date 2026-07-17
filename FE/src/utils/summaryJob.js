// Job tóm tắt: nhãn giai đoạn + poller + normalize record (v2/legacy).
import { createJobPoller } from "./jobPoller";

export const LENGTH_MODES = [
  { value: "short", label: "Ngắn" },
  { value: "medium", label: "Vừa" },
  { value: "detailed", label: "Chi tiết" },
];

// mode = mục đích (trực giao độ dài). standard giữ render cũ; study thêm block ôn tập.
export const SUMMARY_MODES = [
  { value: "standard", label: "Tóm tắt thường" },
  { value: "study", label: "Ôn tập" },
];

export const stageLabel = (status = {}) => {
  const node = String(status.current_node || "");
  // progress_cb của BE đẩy thẳng label Việt ("Đang tóm tắt mục i/n...") vào
  // current_node giữa các node — hiện nguyên văn thay vì rơi về default.
  if (/^Đang /.test(node)) return node;
  if (/section|collect/i.test(node)) return "Dựng mục lục…";
  if (/summarize/i.test(node)) return "Đang tóm tắt các mục…";
  if (/synthesize/i.test(node)) return "Đang tổng hợp…";
  if (/assemble|persist/i.test(node)) return "Đang lưu tóm tắt…";
  return "Đang tóm tắt…";
};

export const createSummaryPoller = (opts) =>
  createJobPoller({
    ...opts,
    messages: {
      notFound: "Không tìm thấy job trên server (có thể đã bị dọn). Hãy tạo lại tóm tắt.",
      lost: (n) => `Mất liên lạc với server sau ${n} lần thử. Hãy tạo lại tóm tắt.`,
      error: "Lỗi khi tạo tóm tắt.",
      interrupted: "Tạo tóm tắt bị gián đoạn trên server (có thể do khởi động lại). Hãy tạo lại tóm tắt.",
    },
  });

// Record v2 có sections; legacy (migrate từ summaries.json) chỉ có summary_md.
export const normalizeSummaryRecord = (record) => {
  if (!record || typeof record !== "object") return null;
  const legacyMd =
    record.summary_md || record.data?.summary || record.summary || "";
  return {
    id: record.id || "",
    title: record.title || "Tóm tắt",
    sources: Array.isArray(record.sources) ? record.sources : [],
    createdAt: record.created_at || record.createdAt || "",
    lengthMode: record.length_mode || "",
    // mode thiếu (record cũ trước Phase 3) → "standard" để render y hệt cũ.
    mode: record.mode === "study" ? "study" : "standard",
    overview: record.overview || "",
    sections: Array.isArray(record.sections) ? record.sections : [],
    entities: Array.isArray(record.entities) ? record.entities : [],
    // Block study chỉ có khi mode=study; record khác → null (SummaryModal null-safe).
    study: record.study && typeof record.study === "object" ? record.study : null,
    generator: record.generator || null,
    legacyMd: Array.isArray(record.sections) && record.sections.length ? "" : String(legacyMd || ""),
  };
};
