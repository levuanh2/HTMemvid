export const ACTIVE_JOB_KEY = "mindmap_active_job";

export const saveActiveMindmapJob = ({ jobId, sources, startedAt }) => {
  try {
    localStorage.setItem(
      ACTIVE_JOB_KEY,
      JSON.stringify({ jobId, sources, startedAt }),
    );
  } catch {}
};

export const loadActiveMindmapJob = () => {
  try {
    const raw = localStorage.getItem(ACTIVE_JOB_KEY);
    if (!raw) return null;

    const data = JSON.parse(raw);
    if (!data || typeof data.jobId !== "string" || !data.jobId) {
      localStorage.removeItem(ACTIVE_JOB_KEY);
      return null;
    }

    return {
      jobId: data.jobId,
      sources: Array.isArray(data.sources) ? data.sources : [],
      startedAt: Number(data.startedAt) || 0,
    };
  } catch {
    try {
      localStorage.removeItem(ACTIVE_JOB_KEY);
    } catch {}
    return null;
  }
};

export const clearActiveMindmapJob = () => {
  try {
    localStorage.removeItem(ACTIVE_JOB_KEY);
  } catch {}
};
