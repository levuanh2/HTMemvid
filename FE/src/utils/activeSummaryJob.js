import { makeActiveJobStore } from "./activeJob";

export const ACTIVE_SUMMARY_JOB_KEY = "summary_active_job";

const store = makeActiveJobStore(ACTIVE_SUMMARY_JOB_KEY);

export const saveActiveSummaryJob = store.save;
export const loadActiveSummaryJob = store.load;
export const clearActiveSummaryJob = store.clear;
