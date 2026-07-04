// Poller mindmap thuần — KHÔNG hard-timeout (bài học: job thật chạy vài phút,
// FE 180s+10s cũ bỏ cuộc giữa chừng → user tưởng lỗi, phải F5 mới thấy map).
export const STALL_MS = 5 * 60 * 1000;

export const pollIntervalMs = (elapsedMs) =>
  elapsedMs < 30_000 ? 2000 : elapsedMs < 120_000 ? 5000 : 10_000;

export const stageLabel = (status = {}) => {
  const node = String(status.current_node || "");
  const msg = String(status.message || "");
  if (/skeleton|collect/i.test(node)) return "Dựng khung xương…";
  if (/enrich/i.test(node)) return msg ? `Làm giàu ${msg}…` : "Làm giàu nhánh…";
  if (/relation/i.test(node)) return "Tìm quan hệ chéo…";
  if (/assemble|persist/i.test(node)) return "Đang lưu sơ đồ…";
  return "Đang tạo sơ đồ…";
};

export function createMindmapPoller({
  fetchStatus, onTick, onDone, onError, onCancelled,
  setTimeoutFn = setTimeout, clearTimeoutFn = clearTimeout, now = Date.now,
}) {
  let timer = null;
  let stopped = true;
  let startTs = 0;
  let lastFingerprint = "";
  let lastChangeTs = 0;

  const fingerprint = (s) =>
    JSON.stringify([s?.progress ?? null, s?.current_node ?? null, s?.partial?.nodes?.length ?? 0]);

  const schedule = (jobId) => {
    if (stopped) return;
    timer = setTimeoutFn(() => tick(jobId), pollIntervalMs(now() - startTs));
  };

  const tick = async (jobId) => {
    if (stopped) return;
    let status;
    try {
      status = await fetchStatus(jobId);
    } catch (err) {
      console.warn(`[MindmapPoller] job=${jobId} fetch lỗi, thử lại:`, err);
      schedule(jobId);
      return;
    }
    if (stopped) return;
    const fp = fingerprint(status);
    if (fp !== lastFingerprint) { lastFingerprint = fp; lastChangeTs = now(); }
    const stalled = now() - lastChangeTs > STALL_MS;
    onTick?.(status, { stalled });
    if (status.status === "done") { stopped = true; onDone?.(status.result); return; }
    if (status.status === "error" || status.status === "timeout") {
      stopped = true; onError?.(new Error(status.error || "Lỗi khi tạo sơ đồ.")); return;
    }
    if (status.status === "cancelled") { stopped = true; onCancelled?.(); return; }
    schedule(jobId);
  };

  return {
    start(jobId) {
      stopped = false;
      startTs = now();
      lastChangeTs = now();
      lastFingerprint = "";
      tick(jobId);
    },
    stop() {
      stopped = true;
      if (timer != null) { clearTimeoutFn(timer); timer = null; }
    },
  };
}
