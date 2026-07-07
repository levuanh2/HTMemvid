// Factory lưu job đang chạy vào localStorage — resume polling sau F5
// (bài học mindmap: job nền dài phải lưu job_id ngay khi nhận được).
export const makeActiveJobStore = (storageKey) => ({
  key: storageKey,
  save: ({ jobId, sources, startedAt, extra }) => {
    try {
      localStorage.setItem(storageKey, JSON.stringify({ jobId, sources, startedAt, extra }));
    } catch {}
  },
  load: () => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || typeof data.jobId !== "string" || !data.jobId) {
        localStorage.removeItem(storageKey);
        return null;
      }
      const out = {
        jobId: data.jobId,
        sources: Array.isArray(data.sources) ? data.sources : [],
        startedAt: Number(data.startedAt) || 0,
      };
      // giữ contract cũ (toEqual không có key extra) — chỉ thêm khi thật sự có
      if (data.extra != null) out.extra = data.extra;
      return out;
    } catch {
      try {
        localStorage.removeItem(storageKey);
      } catch {}
      return null;
    }
  },
  clear: () => {
    try {
      localStorage.removeItem(storageKey);
    } catch {}
  },
});
