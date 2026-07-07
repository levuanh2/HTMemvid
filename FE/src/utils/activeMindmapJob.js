// Re-export trên factory chung (utils/activeJob.js) — giữ nguyên API cũ.
import { makeActiveJobStore } from "./activeJob";

export const ACTIVE_JOB_KEY = "mindmap_active_job";

const store = makeActiveJobStore(ACTIVE_JOB_KEY);

export const saveActiveMindmapJob = store.save;
export const loadActiveMindmapJob = store.load;
export const clearActiveMindmapJob = store.clear;
