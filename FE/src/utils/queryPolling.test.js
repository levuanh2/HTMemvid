import { describe, it, expect, vi } from "vitest";
import { pollQueryStatus, shouldPollFallback } from "./queryPolling";
import { apiUrl } from "./api";

const noSleep = () => Promise.resolve();

function fetcher(responses) {
  const paths = [];
  let i = 0;
  const apiFetch = vi.fn(async (path) => {
    paths.push(path);
    const r = responses[Math.min(i, responses.length - 1)];
    i++;
    return r;
  });
  return { apiFetch, paths };
}

const running = { ok: true, status: 200, json: async () => ({ status: "running", progress: 40, current_node: "RetrieveFAISS" }) };
const done = (answer = "câu trả lời") => ({ ok: true, status: 200, json: async () => ({ status: "done", result: { payload: { answer }, status: 200 } }) });

describe("shouldPollFallback", () => {
  it("polls only on a lost SSE connection, not cancel/real errors", () => {
    expect(shouldPollFallback({ sseConnectionLost: true }, { cancelled: false })).toBe(true);
    expect(shouldPollFallback({ sseConnectionLost: true }, { cancelled: true })).toBe(false); // user cancelled
    expect(shouldPollFallback(new Error("job error"), {})).toBe(false);                        // real job error
    expect(shouldPollFallback(new Error("timeout"), {})).toBe(false);
    expect(shouldPollFallback(undefined, {})).toBe(false);
  });
});

describe("pollQueryStatus", () => {
  it("polls /query-status/<jobId> via apiFetch and returns the final result", async () => {
    const { apiFetch, paths } = fetcher([running, done("hi")]);
    const onStatus = vi.fn();
    const result = await pollQueryStatus("job-1", { apiFetch, sleep: noSleep, onStatus });
    expect(result).toEqual({ payload: { answer: "hi" }, status: 200 });
    expect(paths[0]).toBe("/query-status/job-1");         // authed via apiFetch
    expect(onStatus).toHaveBeenCalled();                  // progress surfaced
  });

  it("throws with status 401 (unauthorized flow handled by apiFetch/Phase F)", async () => {
    const { apiFetch } = fetcher([{ ok: false, status: 401, json: async () => ({ error: "unauthorized" }) }]);
    await expect(pollQueryStatus("j", { apiFetch, sleep: noSleep })).rejects.toMatchObject({ status: 401 });
  });

  it("throws with status 403/404 for a foreign/owner-mismatched job", async () => {
    const { apiFetch: f403 } = fetcher([{ ok: false, status: 403, json: async () => ({ error: "forbidden" }) }]);
    await expect(pollQueryStatus("j", { apiFetch: f403, sleep: noSleep })).rejects.toMatchObject({ status: 403 });
    const { apiFetch: f404 } = fetcher([{ ok: false, status: 404, json: async () => ({ error: "Job not found" }) }]);
    await expect(pollQueryStatus("j", { apiFetch: f404, sleep: noSleep })).rejects.toMatchObject({ status: 404 });
  });

  it("propagates a real job error message", async () => {
    const { apiFetch } = fetcher([{ ok: true, status: 200, json: async () => ({ status: "error", error: "boom" }) }]);
    await expect(pollQueryStatus("j", { apiFetch, sleep: noSleep })).rejects.toThrow("boom");
  });

  it("stops when cancelled (no further polling)", async () => {
    let n = 0;
    const isCancelled = () => { n += 1; return n > 1; };  // false first check, true next
    const { apiFetch } = fetcher([running]);
    await expect(pollQueryStatus("j", { apiFetch, sleep: noSleep, isCancelled })).rejects.toThrow("CANCELLED");
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });

  it("treats a cancelled/interrupted job as CANCELLED", async () => {
    const { apiFetch } = fetcher([{ ok: true, status: 200, json: async () => ({ status: "cancelled" }) }]);
    await expect(pollQueryStatus("j", { apiFetch, sleep: noSleep })).rejects.toThrow("CANCELLED");
  });

  it("times out after maxAttempts (no infinite loop)", async () => {
    const { apiFetch } = fetcher([running]);
    await expect(pollQueryStatus("j", { apiFetch, sleep: noSleep, maxAttempts: 2 })).rejects.toThrow(/thời gian chờ/i);
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });
});

describe("no token in stream URL", () => {
  it("apiUrl for /query-stream carries no token/credential", () => {
    const url = apiUrl("/query-stream/job-abc");
    expect(url).toContain("/query-stream/job-abc");
    expect(url.toLowerCase()).not.toContain("token");
    expect(url).not.toContain("?");
  });
});
