# Mindmap UX v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sinh mindmap chạy nền (chip tiến độ, không hard-timeout, tự mở khi xong, sống sót reload) + thay viewer ReactFlow/ELK bằng mind-elixir (theme Phòng đọc, arrows quan hệ, evidence drawer, chỉnh sửa tay + nút Lưu qua `PUT /mindmaps/<id>`).

**Architecture:** FE tách poller thuần (`mindmapJob.js`) + adapter 2 chiều record-v2 ↔ mind-elixir (`mindElixirAdapter.js`, sidecar map giữ note/chunk_refs/kind) — cả hai pure, vitest được. `MindElixirView.jsx` thay `MindmapView.jsx`. BE chỉ thêm `store.get_record` + `PUT /mindmaps/<id>` (validate bằng schema pipeline sẵn có). Pipeline sinh KHÔNG đụng.

**Tech Stack:** React 19, `mind-elixir` v5 (v5.13.0, zero-dep), `@zumer/snapdom` (export PNG — đường chính thức của mind-elixir), vitest, Flask + sqlite (store sẵn có).

**Spec:** `docs/superpowers/specs/2026-07-04-mindmap-ux-v3-design.md`

## Global Constraints

- KHÔNG đụng BE pipeline/generate/status/cancel/delete — chỉ THÊM `PUT /mindmaps/<id>` + `store.get_record`.
- Pytest chạy bằng **global `python`** từ `BE/`: `python -m pytest tests/<file> -v`. FE: `cd FE && npm test` (vitest) + `npm run build`.
- FE poll KHÔNG có hard-timeout — chỉ dừng ở terminal status (`done`/`error`/`timeout`/`cancelled`) hoặc user Huỷ. Stall (>5 phút không đổi) chỉ CẢNH BÁO, không dừng.
- Màu/token: dùng CSS var Phòng đọc trong `FE/src/index.css` (`--accent` = son #B23A2E, `--bg-base`, `--text-primary`, `--border-color`…) + bảng archival inks `constants.js::BRANCH_COLORS` (edge hexes). KHÔNG hardcode hex mới.
- Toast chỉ cho đường mindmap — KHÔNG refactor `alert()` toàn app.
- mind-elixir API đã verify (repo v5.13.0): data `{nodeData, arrows?, direction?, theme?}`; `Arrow = {id, label, from, to, delta1, delta2, bidirectional?, style?: {stroke, strokeWidth, strokeDasharray, opacity, labelColor}}`; arrow render thành svg group `id="a-"+arrow.id`; events `mind.bus.addListener('operation'|'selectNodes'|'expandNode', cb)`; methods `init/getData/refresh/install`; options `{el, direction, editable, draggable, contextMenu, toolBar, keypress, allowUndo, theme}`; theme `{name, palette: string[], cssVar}`. Export PNG: `snapdom(mind.nodes)` (`mind.exportSvg` deprecated).
- Record v2 (BE `services/mindmap/pipeline/schema.py`): nodes flat `{id, parent, kind, title, note, chunk_refs, order}`, `relations {source, target, type, label}`, `generator {pipeline, model, degraded, missing}`. FE normalize: `FE/src/utils/mindmapNormalize.js::normalizeMindmapRecord`.
- Sau khi xong: cập nhật `.playbook/` (mandatory rule repo).
- Branch: làm thẳng trên `main` (tree sạch), commit theo task.

## Codex dispatch

Task đánh dấu **[CODEX]** giao codex CLI:

```bash
codex exec -C E:/memvid_NCKH/MemVid_New -s workspace-write --skip-git-repo-check "<dán nguyên văn task>"
```

Sau mỗi task codex: Claude review diff + chạy test trước khi tick. Codex KHÔNG sửa file ngoài `Files` của task.

## File Structure

```
FE/src/utils/mindmapJob.js               # MỚI — poller thuần: interval giãn, stall guard, stage label
FE/src/utils/activeMindmapJob.js         # MỚI [CODEX] — localStorage active job helpers
FE/src/components/ui/Toaster.jsx         # MỚI — toast nhẹ (emitter module + component)
FE/src/utils/mindElixirAdapter.js        # MỚI — record v2 ↔ mind-elixir + sidecar
FE/src/components/mindmap/MindElixirView.jsx  # MỚI — viewer thay MindmapView
FE/src/components/mindmap/mindmap.css    # MỚI — theme override + toggle arrows + focus ring
FE/src/components/Layout/MindMapModal.jsx     # SỬA — shell trỏ sang MindElixirView
FE/src/components/Layout/SidebarRight.jsx     # SỬA — nền + chip + resume + auto-open
FE/src/components/Layout/MainLayout.jsx       # SỬA — mount <Toaster/>
FE/src/utils/api.js                      # SỬA — updateMindmap
BE/app/domains/mindmap/store.py          # SỬA [CODEX] — get_record
BE/app/main.py                           # SỬA [CODEX] — PUT /mindmaps/<id>
XOÁ (Task 9): MindmapView.jsx, MindmapNodeCard.jsx, RelationEdge.jsx, useElkLayout.js
```

---

## Phase A — Sinh nền

### Task 1: Poller thuần `mindmapJob.js`

**Files:**
- Create: `FE/src/utils/mindmapJob.js`
- Test: `FE/src/utils/mindmapJob.test.js`

**Interfaces:**
- Produces (Task 4 dùng):
  - `pollIntervalMs(elapsedMs) -> number` — 2000 (<30s), 5000 (<120s), 10000 (còn lại)
  - `STALL_MS = 5 * 60 * 1000`
  - `stageLabel(status) -> string` — map `current_node`/`progress` sang label tiếng Việt
  - `createMindmapPoller({ fetchStatus, onTick, onDone, onError, onCancelled, setTimeoutFn?, clearTimeoutFn?, now? }) -> { start(jobId), stop() }`
    - `fetchStatus(jobId) -> Promise<statusJson>` (caller inject — Task 4 dùng `apiFetch`)
    - `onTick(status, { stalled })` mỗi lần poll OK; `stalled=true` khi fingerprint (`progress`+`current_node`+`partial?.nodes?.length`) không đổi > `STALL_MS`
    - terminal: `done` → `onDone(status.result)`; `error`/`timeout` → `onError(Error)`; `cancelled` → `onCancelled()`
    - fetch lỗi mạng → KHÔNG dừng, thử lại tick sau (log console)
    - `stop()` idempotent, huỷ timer đang chờ

- [ ] **Step 1: Viết test fail**

```js
// FE/src/utils/mindmapJob.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { pollIntervalMs, stageLabel, createMindmapPoller, STALL_MS } from "./mindmapJob";

describe("pollIntervalMs", () => {
  it("giãn 2s → 5s → 10s", () => {
    expect(pollIntervalMs(0)).toBe(2000);
    expect(pollIntervalMs(29_000)).toBe(2000);
    expect(pollIntervalMs(31_000)).toBe(5000);
    expect(pollIntervalMs(121_000)).toBe(10_000);
  });
});

describe("stageLabel", () => {
  it("map node pipeline sang label", () => {
    expect(stageLabel({ current_node: "Skeleton" })).toBe("Dựng khung xương…");
    expect(stageLabel({ current_node: "Enrich", message: "nhánh 2/5" })).toContain("nhánh 2/5");
    expect(stageLabel({ current_node: "Relations" })).toBe("Tìm quan hệ chéo…");
    expect(stageLabel({})).toBe("Đang tạo sơ đồ…");
  });
});

describe("createMindmapPoller", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const mk = (statuses, cbs = {}) => {
    let i = 0;
    const fetchStatus = vi.fn(async () => statuses[Math.min(i++, statuses.length - 1)]);
    const events = { ticks: [], done: [], errors: [], cancelled: 0 };
    const poller = createMindmapPoller({
      fetchStatus,
      onTick: (s, meta) => events.ticks.push([s, meta]),
      onDone: (r) => events.done.push(r),
      onError: (e) => events.errors.push(e),
      onCancelled: () => events.cancelled++,
      ...cbs,
    });
    return { poller, events, fetchStatus };
  };

  it("poll tới done, KHÔNG có hard timeout (chạy quá 190s vẫn sống)", async () => {
    const running = { status: "running", progress: 50, current_node: "Enrich" };
    const statuses = Array(60).fill(running).concat([{ status: "done", result: { id: "r1" } }]);
    const { poller, events } = mk(statuses);
    poller.start("j1");
    await vi.advanceTimersByTimeAsync(600_000); // 10 phút
    expect(events.done).toEqual([{ id: "r1" }]);
    expect(events.errors).toHaveLength(0);
  });

  it("stalled=true khi fingerprint đứng yên quá STALL_MS", async () => {
    const frozen = { status: "running", progress: 40, current_node: "Enrich" };
    const { poller, events } = mk(Array(200).fill(frozen));
    poller.start("j1");
    await vi.advanceTimersByTimeAsync(STALL_MS + 60_000);
    const lastMeta = events.ticks.at(-1)[1];
    expect(lastMeta.stalled).toBe(true);
    poller.stop();
  });

  it("fetch lỗi mạng không dừng poll", async () => {
    let calls = 0;
    const fetchStatus = vi.fn(async () => {
      calls++;
      if (calls < 3) throw new Error("mạng rớt");
      return { status: "done", result: { id: "ok" } };
    });
    const { poller, events } = mk([], { fetchStatus });
    poller.start("j1");
    await vi.advanceTimersByTimeAsync(30_000);
    expect(events.done).toEqual([{ id: "ok" }]);
  });

  it("cancelled → onCancelled; stop() chặn tick sau", async () => {
    const { poller, events } = mk([{ status: "cancelled" }]);
    poller.start("j1");
    await vi.advanceTimersByTimeAsync(5000);
    expect(events.cancelled).toBe(1);
    poller.stop(); // idempotent, không ném
  });
});
```

- [ ] **Step 2: Chạy fail** — `cd FE && npx vitest run src/utils/mindmapJob.test.js` → FAIL (module chưa có).

- [ ] **Step 3: Implement `mindmapJob.js`**

```js
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
```

- [ ] **Step 4: Chạy pass** — `npx vitest run src/utils/mindmapJob.test.js` → PASS.
- [ ] **Step 5: Commit** — `git add FE/src/utils/mindmapJob.js FE/src/utils/mindmapJob.test.js && git commit -m "feat(fe): mindmap poller — no hard timeout, adaptive interval, stall guard"`

### Task 2 [CODEX]: localStorage active-job helpers

**Files:**
- Create: `FE/src/utils/activeMindmapJob.js`
- Test: `FE/src/utils/activeMindmapJob.test.js`

**Interfaces:**
- Produces (Task 4 dùng):
  - `ACTIVE_JOB_KEY = "mindmap_active_job"`
  - `saveActiveMindmapJob({ jobId, sources, startedAt }) -> void`
  - `loadActiveMindmapJob() -> { jobId: string, sources: string[], startedAt: number } | null` — JSON hỏng/thiếu `jobId` → null (và xoá key rác)
  - `clearActiveMindmapJob() -> void`
  - Mọi hàm bọc try/catch (localStorage có thể bị chặn) — lỗi → no-op/null.

- [ ] **Step 1: Viết test fail**

```js
// FE/src/utils/activeMindmapJob.test.js
import { describe, it, expect, beforeEach } from "vitest";
import { ACTIVE_JOB_KEY, saveActiveMindmapJob, loadActiveMindmapJob, clearActiveMindmapJob } from "./activeMindmapJob";

describe("activeMindmapJob", () => {
  beforeEach(() => localStorage.clear());

  it("save/load roundtrip", () => {
    saveActiveMindmapJob({ jobId: "j1", sources: ["a_docx"], startedAt: 123 });
    expect(loadActiveMindmapJob()).toEqual({ jobId: "j1", sources: ["a_docx"], startedAt: 123 });
  });

  it("clear xoá key", () => {
    saveActiveMindmapJob({ jobId: "j1", sources: [], startedAt: 1 });
    clearActiveMindmapJob();
    expect(loadActiveMindmapJob()).toBeNull();
    expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
  });

  it("JSON rác → null + dọn key", () => {
    localStorage.setItem(ACTIVE_JOB_KEY, "{không phải json");
    expect(loadActiveMindmapJob()).toBeNull();
    expect(localStorage.getItem(ACTIVE_JOB_KEY)).toBeNull();
  });

  it("thiếu jobId → null", () => {
    localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify({ sources: [] }));
    expect(loadActiveMindmapJob()).toBeNull();
  });
});
```

Lưu ý vitest environment: file test này cần `// @vitest-environment jsdom` ở dòng đầu NẾU config mặc định là node (kiểm `FE/vite.config.js`/`vitest.config`; các test hiện có `mindmapNormalize.test.js` chạy môi trường gì thì theo đó — nếu node, thêm devDep `jsdom` đã có sẵn qua vitest? KHÔNG cài thêm gì nếu test hiện tại đã chạy jsdom).

- [ ] **Step 2: Chạy fail** — `cd FE && npx vitest run src/utils/activeMindmapJob.test.js` → FAIL.

- [ ] **Step 3: Implement**

```js
// Ghi nhớ job mindmap đang chạy để F5/đóng-mở tab poll tiếp được.
export const ACTIVE_JOB_KEY = "mindmap_active_job";

export const saveActiveMindmapJob = ({ jobId, sources, startedAt }) => {
  try {
    localStorage.setItem(ACTIVE_JOB_KEY, JSON.stringify({ jobId, sources, startedAt }));
  } catch { /* localStorage bị chặn → thôi, chỉ mất resume */ }
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
    try { localStorage.removeItem(ACTIVE_JOB_KEY); } catch { /* noop */ }
    return null;
  }
};

export const clearActiveMindmapJob = () => {
  try { localStorage.removeItem(ACTIVE_JOB_KEY); } catch { /* noop */ }
};
```

- [ ] **Step 4: Chạy pass** — `npx vitest run src/utils/activeMindmapJob.test.js` → PASS.
- [ ] **Step 5: Commit** — `git add FE/src/utils/activeMindmapJob.js FE/src/utils/activeMindmapJob.test.js && git commit -m "feat(fe): persist active mindmap job to localStorage for resume-after-reload"`

### Task 3: Toast nhẹ

**Files:**
- Create: `FE/src/components/ui/Toaster.jsx`
- Modify: `FE/src/components/Layout/MainLayout.jsx` (mount `<Toaster/>` một lần, cạnh cây layout gốc)
- Test: `FE/src/components/ui/toastStore.test.js`

**Interfaces:**
- Produces: `toast(message, { type = "info", duration = 5000 })` — type ∈ `info|success|error`; export thêm `subscribeToasts(cb)`/`dismissToast(id)` cho component + test. Toast stack góc dưới-phải, style token Phòng đọc (`--bg-card`, `--border-strong`, accent son cho error, `--ok` cho success), tự biến mất sau `duration`, click để đóng, `role="status"` (info/success) — error dùng `role="alert"`.

- [ ] **Step 1: Viết test fail** (logic store tách khỏi React — test không cần render)

```js
// FE/src/components/ui/toastStore.test.js
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { toast, subscribeToasts, dismissToast, _resetToasts } from "./Toaster";

describe("toast store", () => {
  beforeEach(() => { vi.useFakeTimers(); _resetToasts(); });
  afterEach(() => vi.useRealTimers());

  it("push + auto-expire theo duration", () => {
    const seen = [];
    subscribeToasts((list) => seen.push(list.map((t) => t.message)));
    toast("Sơ đồ sẵn sàng", { type: "success", duration: 3000 });
    expect(seen.at(-1)).toEqual(["Sơ đồ sẵn sàng"]);
    vi.advanceTimersByTime(3100);
    expect(seen.at(-1)).toEqual([]);
  });

  it("dismiss thủ công", () => {
    let latest = [];
    subscribeToasts((l) => { latest = l; });
    toast("x", { duration: 60_000 });
    dismissToast(latest[0].id);
    expect(latest).toEqual([]);
  });
});
```

- [ ] **Step 2: Chạy fail** → FAIL.

- [ ] **Step 3: Implement `Toaster.jsx`** — store module-level + component:

```jsx
// Toast nhẹ cho đường mindmap (KHÔNG thay alert() toàn app).
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

let _toasts = [];
let _nextId = 1;
const _subs = new Set();
const _timers = new Map();

const _emit = () => _subs.forEach((cb) => cb(_toasts));

export const subscribeToasts = (cb) => { _subs.add(cb); cb(_toasts); return () => _subs.delete(cb); };

export const dismissToast = (id) => {
  const t = _timers.get(id);
  if (t) { clearTimeout(t); _timers.delete(id); }
  _toasts = _toasts.filter((x) => x.id !== id);
  _emit();
};

export const toast = (message, { type = "info", duration = 5000 } = {}) => {
  const id = _nextId++;
  _toasts = [..._toasts, { id, message, type }];
  _emit();
  _timers.set(id, setTimeout(() => dismissToast(id), duration));
  return id;
};

export const _resetToasts = () => { // test-only
  _timers.forEach(clearTimeout); _timers.clear(); _toasts = []; _emit();
};

const COLORS = { info: "var(--slate)", success: "var(--ok)", error: "var(--err)" };

export default function Toaster() {
  const [items, setItems] = useState([]);
  useEffect(() => subscribeToasts(setItems), []);
  if (typeof document === "undefined" || !items.length) return null;
  return createPortal(
    <div className="fixed bottom-4 right-4 z-[1200] flex flex-col gap-2 max-w-xs">
      {items.map((t) => (
        <div key={t.id} role={t.type === "error" ? "alert" : "status"}
          onClick={() => dismissToast(t.id)}
          className="cursor-pointer rounded-[8px] border px-3 py-2 text-[13px]"
          style={{
            background: "var(--bg-card)", borderColor: "var(--border-strong)",
            boxShadow: "var(--shadow-card-hover)", color: "var(--text-primary)",
            borderLeft: `3px solid ${COLORS[t.type] || COLORS.info}`,
          }}>
          {t.message}
        </div>
      ))}
    </div>,
    document.body
  );
}
```

Mount trong `MainLayout.jsx`: import default `Toaster` và render `<Toaster />` một lần cuối JSX gốc (cạnh các modal/overlay hiện có).

- [ ] **Step 4: Chạy pass** — `npx vitest run src/components/ui/toastStore.test.js` → PASS; `npm run build` xanh.
- [ ] **Step 5: Commit** — `git add FE/src/components/ui/Toaster.jsx FE/src/components/ui/toastStore.test.js FE/src/components/Layout/MainLayout.jsx && git commit -m "feat(fe): lightweight toast stack (mindmap flow)"`

### Task 4: SidebarRight — sinh nền + chip + resume + auto-open

**ĐIỀU KIỆN:** Task 1, 2, 3 xong.

**Files:**
- Modify: `FE/src/components/Layout/SidebarRight.jsx`

**Interfaces:**
- Consumes: `createMindmapPoller/stageLabel` (Task 1), `save/load/clearActiveMindmapJob` (Task 2), `toast` (Task 3), `generateMindmap/cancelMindmap` (api.js sẵn có).
- Hành vi đích (spec §3):
  1. `runMindmapGeneration`: sau khi có `job_id` → `saveActiveMindmapJob({jobId, sources, startedAt: Date.now()})`, KHÔNG `setShowModalMap` với partial (bỏ skeleton-preview overlay), KHÔNG dùng `startPolling` cũ — dùng poller Task 1 với `fetchStatus = (id) => apiFetch(`/mindmap-status/${encodeURIComponent(id)}`).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })`.
  2. State chip mới: `mindmapJobUi = { running: bool, label: string, progress: number|null, stalled: bool }`. `onTick` → set label = `stageLabel(status)`, progress, stalled.
  3. `onDone(result)`: `clearActiveMindmapJob()`; giữ nguyên khối build `record` hiện có (spread `data` trước — bài học field-drop); `fetchMindMaps()`; toast "Sơ đồ sẵn sàng"; **nếu KHÔNG phải resume** → `setShowModalMap(record)` (tự mở); nếu resume → chỉ toast "Sơ đồ đã xong trong lúc bạn vắng mặt — mở từ danh sách".
  4. `onError` → toast error (message BE), clear key. `onCancelled` → giữ `showCancelNotice()` hiện có, clear key.
  5. Cache-hit path (`status==="done"` ngay) giữ nguyên nhưng vẫn tự mở + toast.
  6. Resume: `useEffect` mount — `loadActiveMindmapJob()` → nếu có, set chip running + start poller với cờ `resumed=true`. Job đã terminal từ lâu → BE trả status tương ứng, các callback trên tự xử lý.
  7. Xoá: `startPolling`/`stopPolling`/`pollingIntervalRef`/`pollingTimeoutRef`/`maxElapsedMs`/`computeInterval` (poller cũ), skeleton-preview block trong `onTick`, `mindmapGenerating` chỉ còn phục vụ nút "Tạo lại" trong viewer (giữ nếu còn dùng, bỏ nếu không).
  8. Chip JSX (đặt trong tab Sơ đồ, trên list): spinner + label + `%` nếu có + nút Huỷ; stalled → viền `--warn` + text "Có vẻ kẹt — vẫn đang chờ BE. Huỷ?".

```jsx
{mindmapJobUi.running && (
  <div className="mx-3 mb-2 flex items-center gap-2 rounded-[8px] border px-2.5 py-2 text-[12px]"
    style={{ borderColor: mindmapJobUi.stalled ? "var(--warn)" : "var(--border-strong)", background: "var(--bg-elevated)" }}>
    <span className="animate-spin inline-block w-3.5 h-3.5 rounded-full border-2 border-t-transparent"
      style={{ borderColor: "var(--accent)", borderTopColor: "transparent" }} aria-hidden />
    <span className="flex-1 truncate text-text-secondary">
      {mindmapJobUi.stalled ? "Có vẻ kẹt — vẫn đang chờ máy chủ…" : mindmapJobUi.label}
      {typeof mindmapJobUi.progress === "number" ? ` (${mindmapJobUi.progress}%)` : ""}
    </span>
    <button onClick={handleCancelMindMap} className="text-[12px] underline text-text-muted hover:text-accent">Huỷ</button>
  </div>
)}
```

(`animate-spin` tôn trọng reduced-motion nếu app đã cấu hình; nếu chưa, thêm vào `mindmap.css` Task 7: `@media (prefers-reduced-motion: reduce) { .animate-spin { animation: none } }`.)

- [ ] **Step 1:** Rewire theo 1-8. `handleCancelMindMap` đổi sang `poller.stop()` + `cancelMindmap(jobId)` + clear key + chip tắt (logic notice giữ).
- [ ] **Step 2:** `npm run build` xanh; `npx vitest run` toàn FE xanh.
- [ ] **Step 3: Manual smoke A** — BE chạy, tạo sơ đồ doc nhỏ: chip hiện label stage, KHÔNG overlay lúc sinh, xong → toast + overlay tự mở + list có record (KHÔNG F5).
- [ ] **Step 4: Manual smoke B** — tạo sơ đồ, F5 giữa chừng → chip tự hiện lại (resume), xong → toast, KHÔNG tự mở.
- [ ] **Step 5: Commit** — `git add FE/src/components/Layout/SidebarRight.jsx && git commit -m "feat(fe): background mindmap generation — progress chip, no FE timeout, auto-open, resume after reload"`

---

## Phase B — Viewer mind-elixir

### Task 5: Adapter record v2 ↔ mind-elixir

**Files:**
- Create: `FE/src/utils/mindElixirAdapter.js`
- Modify: `FE/package.json` (dep `mind-elixir`)
- Test: `FE/src/utils/mindElixirAdapter.test.js`

**Interfaces:**
- Consumes: `normalizeMindmapRecord` (`FE/src/utils/mindmapNormalize.js` — trả `{title, nodes: [{id, parent, title, note, kind, chunkRefs, order}], relations, degraded, missing}`).
- Produces (Task 7/8 dùng):
  - `recordToMindElixir(record) -> { mindData: {nodeData, arrows, direction: 2}, sidecar: Map<string, {note, chunkRefs, kind}> }`
    - `nodeData` = tree lồng (`{id, topic, children}`), root từ node kind `root`; con sort theo `order`.
    - `arrows` = relations → `{id: "rel-<i>", label: label || REL_LABELS[type], from: source, to: target, delta1: {x: 80, y: -60}, delta2: {x: -80, y: -60}, style: {stroke: "var(--accent)", strokeWidth: 2, strokeDasharray: "6 4", labelColor: "var(--accent)", opacity: 0.9}}`.
    - `REL_LABELS = { relates_to: "liên quan", leads_to: "dẫn tới", causes: "gây ra", supports: "bổ trợ", contrasts: "đối lập", contains: "bao hàm" }`.
  - `mindElixirToRecord(mindData, sidecar, baseRecord) -> record v2` — walk `nodeData` DFS: node `{id, parent, kind, title: topic, note, chunk_refs, order}`; `kind` = sidecar trước, node mới → depth 0 `root` / depth 1 `section` / sâu hơn `idea`; `note`/`chunk_refs` từ sidecar, node mới → `""`/`[]`. Arrows → relations: arrow trùng `from→to` với relation gốc trong `baseRecord` → giữ `type` gốc; arrow mới → `type: "relates_to"`; `label` lấy từ arrow. Trả `{...baseRecord, title: rootTopic, nodes, relations}` (KHÔNG đổi id/hash/created_at — server tự bảo vệ thêm).

- [ ] **Step 1:** `cd FE && npm i mind-elixir` (v5.x). `npm run build` vẫn xanh (chưa import đâu cả).

- [ ] **Step 2: Viết test fail**

```js
// FE/src/utils/mindElixirAdapter.test.js
import { describe, it, expect } from "vitest";
import { recordToMindElixir, mindElixirToRecord } from "./mindElixirAdapter";

const REC = {
  schema_version: 2, id: "m1", title: "Tài liệu X", content_hash: "h".repeat(64),
  created_at: "2026-07-04T00:00:00Z", sources: ["x_docx"],
  nodes: [
    { id: "n0", parent: null, kind: "root", title: "Tài liệu X", note: "", chunk_refs: [], order: 0 },
    { id: "n1", parent: "n0", kind: "section", title: "1. Mở đầu", note: "tóm ý", chunk_refs: ["3"], order: 0 },
    { id: "n2", parent: "n0", kind: "section", title: "2. Phương pháp", note: "", chunk_refs: ["4"], order: 1 },
    { id: "n3", parent: "n1", kind: "idea", title: "Bối cảnh", note: "n3", chunk_refs: ["3"], order: 0 },
  ],
  relations: [{ source: "n1", target: "n2", type: "leads_to", label: "dẫn tới" }],
  generator: { pipeline: "skeleton_v1", degraded: false, missing: [] },
};

describe("recordToMindElixir", () => {
  it("dựng tree lồng đúng thứ tự + sidecar giữ note/chunkRefs/kind", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    expect(mindData.nodeData.id).toBe("n0");
    expect(mindData.nodeData.topic).toBe("Tài liệu X");
    expect(mindData.nodeData.children.map((c) => c.topic)).toEqual(["1. Mở đầu", "2. Phương pháp"]);
    expect(mindData.nodeData.children[0].children[0].id).toBe("n3");
    expect(sidecar.get("n1")).toEqual({ note: "tóm ý", chunkRefs: ["3"], kind: "section" });
  });

  it("relations → arrows nét đứt màu son có label", () => {
    const { mindData } = recordToMindElixir(REC);
    expect(mindData.arrows).toHaveLength(1);
    const a = mindData.arrows[0];
    expect(a.from).toBe("n1");
    expect(a.to).toBe("n2");
    expect(a.label).toBe("dẫn tới");
    expect(a.style.strokeDasharray).toBe("6 4");
    expect(a.delta1).toBeTruthy(); // arrows inject qua data cần delta
  });

  it("v1 legacy đi qua normalize không vỡ", () => {
    const legacy = { title: "L", nodes: [{ id: "root", parent: null, title: "L" }] };
    const { mindData } = recordToMindElixir(legacy);
    expect(mindData.nodeData.topic).toBe("L");
    expect(mindData.arrows).toEqual([]);
  });
});

describe("mindElixirToRecord", () => {
  it("round-trip bảo toàn cây + note/chunk_refs qua sidecar", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    const out = mindElixirToRecord(mindData, sidecar, REC);
    expect(out.id).toBe("m1");
    expect(out.nodes).toHaveLength(4);
    const n1 = out.nodes.find((n) => n.id === "n1");
    expect(n1).toMatchObject({ parent: "n0", kind: "section", note: "tóm ý", chunk_refs: ["3"] });
    expect(out.relations).toEqual([{ source: "n1", target: "n2", type: "leads_to", label: "dẫn tới" }]);
  });

  it("node user thêm → kind theo depth, refs rỗng; node xoá không rò sidecar", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    mindData.nodeData.children[0].children.push({ id: "me-new-1", topic: "Ý mới", children: [] });
    mindData.nodeData.children.splice(1, 1); // xoá nhánh n2
    const out = mindElixirToRecord(mindData, sidecar, REC);
    const added = out.nodes.find((n) => n.id === "me-new-1");
    expect(added).toMatchObject({ kind: "idea", note: "", chunk_refs: [], parent: "n1" });
    expect(out.nodes.find((n) => n.id === "n2")).toBeUndefined();
    // relation trỏ tới node đã xoá vẫn được trả — BE validate_relations sẽ lọc (không lọc 2 lần ở FE)
  });

  it("arrow mới → relates_to + label", () => {
    const { mindData, sidecar } = recordToMindElixir(REC);
    mindData.arrows.push({ id: "a9", label: "ghi chú", from: "n3", to: "n2", delta1: { x: 0, y: 0 }, delta2: { x: 0, y: 0 } });
    const out = mindElixirToRecord(mindData, sidecar, REC);
    expect(out.relations).toContainEqual({ source: "n3", target: "n2", type: "relates_to", label: "ghi chú" });
  });
});
```

- [ ] **Step 3: Chạy fail** → FAIL. Implement:

```js
// Adapter record v2 ↔ mind-elixir. Pure — không import mind-elixir (chỉ shape data).
// Sidecar: mind-elixir KHÔNG cam kết bảo toàn field lạ qua operations → note/chunk_refs/kind
// sống ở Map riêng, merge lại lúc save.
import { normalizeMindmapRecord } from "./mindmapNormalize";

export const REL_LABELS = {
  relates_to: "liên quan", leads_to: "dẫn tới", causes: "gây ra",
  supports: "bổ trợ", contrasts: "đối lập", contains: "bao hàm",
};

const ARROW_STYLE = {
  stroke: "var(--accent)", strokeWidth: 2, strokeDasharray: "6 4",
  labelColor: "var(--accent)", opacity: 0.9,
};

export function recordToMindElixir(record) {
  const norm = normalizeMindmapRecord(record);
  const sidecar = new Map();
  const byParent = new Map();
  let root = null;
  for (const n of norm.nodes) {
    sidecar.set(n.id, { note: n.note || "", chunkRefs: n.chunkRefs || [], kind: n.kind });
    if (n.kind === "root" || n.parent == null) { root = root || n; continue; }
    if (!byParent.has(n.parent)) byParent.set(n.parent, []);
    byParent.get(n.parent).push(n);
  }
  const toTree = (n) => ({
    id: n.id, topic: n.title,
    children: (byParent.get(n.id) || [])
      .slice().sort((a, b) => (a.order ?? 0) - (b.order ?? 0)).map(toTree),
  });
  const nodeData = root
    ? toTree(root)
    : { id: "n0", topic: norm.title || "Sơ đồ tư duy", children: [] };
  const arrows = (norm.relations || []).map((r, i) => ({
    id: `rel-${i}`, label: r.label || REL_LABELS[r.type] || "liên quan",
    from: r.source, to: r.target,
    delta1: { x: 80, y: -60 }, delta2: { x: -80, y: -60 },
    style: { ...ARROW_STYLE },
  }));
  return { mindData: { nodeData, arrows, direction: 2 /* MindElixir.SIDE */ }, sidecar };
}

export function mindElixirToRecord(mindData, sidecar, baseRecord) {
  const nodes = [];
  const walk = (node, parent, depth, order) => {
    const side = sidecar.get(node.id);
    nodes.push({
      id: node.id, parent,
      kind: side?.kind || (depth === 0 ? "root" : depth === 1 ? "section" : "idea"),
      title: node.topic || "", note: side?.note || "",
      chunk_refs: side?.chunkRefs || [], order,
    });
    (node.children || []).forEach((c, i) => walk(c, node.id, depth + 1, i));
  };
  walk(mindData.nodeData, null, 0, 0);

  const baseType = new Map(
    (baseRecord.relations || []).map((r) => [`${r.source}→${r.target}`, r.type])
  );
  const relations = (mindData.arrows || []).map((a) => ({
    source: a.from, target: a.to,
    type: baseType.get(`${a.from}→${a.to}`) || "relates_to",
    label: a.label || "",
  }));

  return { ...baseRecord, title: mindData.nodeData.topic || baseRecord.title, nodes, relations };
}
```

- [ ] **Step 4: Chạy pass** — `npx vitest run src/utils/mindElixirAdapter.test.js` → PASS. LƯU Ý: nếu `normalizeMindmapRecord` với record v2 KHÔNG trả field `order` (kiểm code thật) → sort fallback giữ nguyên thứ tự mảng là đúng (v2 nodes đã theo thứ tự tài liệu).
- [ ] **Step 5: Commit** — `git add FE/package.json FE/package-lock.json FE/src/utils/mindElixirAdapter.js FE/src/utils/mindElixirAdapter.test.js && git commit -m "feat(fe): mind-elixir adapter — record v2 <-> nodeData/arrows with provenance sidecar"`

### Task 6 [CODEX]: BE `store.get_record` + `PUT /mindmaps/<id>`

**Files:**
- Modify: `BE/app/domains/mindmap/store.py` (thêm `get_record`)
- Modify: `BE/app/main.py` (route PUT, đặt cạnh `list_mindmaps`/`delete_mindmap` ~dòng 1786-1794)
- Test: `BE/tests/test_mindmap_update.py`

**Interfaces:**
- Produces:
  - `store.get_record(mindmap_id: str) -> Optional[dict]` — SELECT `record_json` theo id, mirror style `delete_record`/`_decode_record` trong file.
  - `PUT /mindmaps/<id>`: body JSON `{title?, nodes, relations?}` (record v2 shape). 404 id lạ; 400 nếu `sanitize_nodes(body["nodes"])` trả rỗng. Server: bảo vệ `id/content_hash/created_at/sources` từ record gốc (body KHÔNG đè được); `relations` qua `validate_relations` (id lạ/self-loop/trùng cạnh cây bị lọc — node đã xoá kéo relation chết theo tại đây); set `updated_at` ISO Z + `generator.edited = True`; ghi `store.save_record` (INSERT OR REPLACE sẵn có); trả record đã lưu.
- Consumes: `services.mindmap.pipeline.schema.sanitize_nodes/validate_relations` (đã tồn tại).

- [ ] **Step 1: Viết test fail** (mirror fixture pattern `tests/test_mindmap_routes.py` — client Flask + `MINDMAPS_DB_PATH` env như `tests/test_mindmap_store.py`):

```python
# BE/tests/test_mindmap_update.py
import json
from app.domains.mindmap import store


def _rec(i="m1"):
    return {"id": i, "schema_version": 2, "title": "Gốc", "sources": ["a_docx"],
            "content_hash": "h" * 64, "created_at": "2026-07-04T00:00:00Z",
            "nodes": [
                {"id": "n0", "parent": None, "kind": "root", "title": "Gốc", "note": "", "chunk_refs": [], "order": 0},
                {"id": "n1", "parent": "n0", "kind": "section", "title": "A", "note": "x", "chunk_refs": ["1"], "order": 0},
            ],
            "relations": [],
            "generator": {"pipeline": "skeleton_v1", "degraded": False, "missing": []}}


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mm.sqlite"))
    from app import main as be_main
    return be_main.app.test_client()


def test_get_record_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDMAPS_DB_PATH", str(tmp_path / "mm.sqlite"))
    store.save_record(_rec())
    assert store.get_record("m1")["title"] == "Gốc"
    assert store.get_record("khong_co") is None


def test_put_updates_and_protects_fields(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    store.save_record(_rec())
    body = _rec()
    body["id"] = "HACK"; body["content_hash"] = "x" * 64; body["sources"] = ["khac"]
    body["title"] = "Đã sửa"
    body["nodes"].append({"id": "n2", "parent": "n1", "kind": "idea", "title": "Ý mới", "note": "", "chunk_refs": [], "order": 0})
    body["relations"] = [{"source": "n1", "target": "n2", "type": "leads_to", "label": "dẫn"},   # trùng cạnh cây → lọc
                         {"source": "n2", "target": "XX", "type": "relates_to", "label": ""}]     # id lạ → lọc
    r = client.put("/mindmaps/m1", data=json.dumps(body), content_type="application/json")
    assert r.status_code == 200
    saved = store.get_record("m1")
    assert saved["title"] == "Đã sửa"
    assert saved["content_hash"] == "h" * 64          # body không đè được
    assert saved["sources"] == ["a_docx"]
    assert saved["created_at"] == "2026-07-04T00:00:00Z"
    assert any(n["id"] == "n2" for n in saved["nodes"])
    assert saved["relations"] == []                    # cả 2 relation rác bị lọc
    assert saved["generator"]["edited"] is True
    assert saved["updated_at"].endswith("Z")


def test_put_404_unknown_id(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.put("/mindmaps/khong_co", data=json.dumps(_rec()), content_type="application/json")
    assert r.status_code == 404


def test_put_400_empty_nodes(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    store.save_record(_rec())
    r = client.put("/mindmaps/m1", data=json.dumps({"nodes": []}), content_type="application/json")
    assert r.status_code == 400
```

- [ ] **Step 2: Chạy fail** — `cd BE && python -m pytest tests/test_mindmap_update.py -v` → FAIL.

- [ ] **Step 3: Implement.** `store.py`:

```python
def get_record(mindmap_id: str) -> Optional[dict]:
    init_db()
    with _lock:
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT record_json FROM mindmaps WHERE id = ?", (str(mindmap_id),)
            ).fetchone()
        finally:
            conn.close()
    return _decode_record(row[0]) if row else None
```

(MIRROR đúng cách `delete_record`/`list_records` trong file quản lý connection — nếu chúng dùng context manager thì theo y hệt, đừng tự chế.)

`main.py` (cạnh `delete_mindmap`):

```python
@app.route("/mindmaps/<mindmap_id>", methods=["PUT"])
def update_mindmap(mindmap_id: str):
    """Lưu bản chỉnh sửa tay từ viewer. Bảo vệ id/hash/created_at/sources gốc."""
    base = mindmap_store.get_record(mindmap_id)
    if not base:
        return jsonify({"error": "Mind map not found"}), 404
    body = request.get_json(silent=True) or {}
    from services.mindmap.pipeline.schema import sanitize_nodes, validate_relations
    nodes = sanitize_nodes(body.get("nodes") or [])
    if not nodes:
        return jsonify({"error": "nodes trống hoặc không hợp lệ"}), 400
    relations = validate_relations(body.get("relations") or [], nodes)
    record = {**base, "title": (str(body.get("title") or "").strip() or base.get("title") or ""),
              "nodes": nodes, "relations": relations}
    # Field bất biến — body không đè được
    for k in ("id", "content_hash", "created_at", "sources", "schema_version"):
        record[k] = base.get(k)
    from datetime import datetime, timezone
    record["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    generator = dict(base.get("generator") or {})
    generator["edited"] = True
    record["generator"] = generator
    mindmap_store.save_record(record)
    return jsonify(record)
```

- [ ] **Step 4: Chạy pass** — `python -m pytest tests/test_mindmap_update.py tests/test_mindmap_store.py tests/test_mindmap_routes.py -v` → PASS; `python -c "import app.main"` OK.
- [ ] **Step 5: Commit** — `git add BE/app/domains/mindmap/store.py BE/app/main.py BE/tests/test_mindmap_update.py && git commit -m "feat(be): PUT /mindmaps/<id> — save manual edits, validated via pipeline schema"`

### Task 7: `MindElixirView.jsx` — viewer mới + theme + drawer

**ĐIỀU KIỆN:** Task 5 xong.

**Files:**
- Create: `FE/src/components/mindmap/MindElixirView.jsx`, `FE/src/components/mindmap/mindmap.css`
- Modify: `FE/src/components/Layout/MindMapModal.jsx` (shell trỏ sang view mới, bỏ ReactFlowProvider)

**Interfaces:**
- Consumes: `recordToMindElixir` (Task 5), `EvidenceDrawer` (props `{node, onClose, generating, onAskAbout}` — `node` shape `{id, title, note, chunkRefs}`), `toast` (Task 3).
- Produces: `MindElixirView({ data, onClose, onRegenerate, regenerating })` — `data` = record (+ field bơm từ SidebarRight: `onAskAbout`, `generating`, `onCancel`). Expose nội bộ cho Task 8: giữ `mindRef` (instance), `sidecarRef`, state `dirty`.
- Props shell giữ nguyên → SidebarRight KHÔNG đổi ở task này.

- [ ] **Step 1: Implement `MindElixirView.jsx`**

```jsx
// Viewer mind-elixir — thay ReactFlow/ELK. Overlay fullscreen giữ từ v2.
import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import MindElixir from "mind-elixir";
import { recordToMindElixir } from "../../utils/mindElixirAdapter";
import EvidenceDrawer from "./EvidenceDrawer";
import { Icon } from "../ui/Icon";
import "./mindmap.css";

// Palette nhánh: archival inks (edge hexes từ constants.js::BRANCH_COLORS)
const PALETTE = ["#5C6B7A", "#3E6B57", "#B5821F", "#B23A2E", "#4A5A8A", "#8A7A66"];

const THEME = {
  name: "PhongDoc",
  palette: PALETTE,
  cssVar: {
    "--main-color": "var(--text-primary)",
    "--main-bgcolor": "var(--bg-base)",
    "--color": "var(--text-secondary)",
    "--bgcolor": "var(--bg-base)",
  },
};

export default function MindElixirView({ data, onClose, onRegenerate, regenerating }) {
  const containerRef = useRef(null);
  const mindRef = useRef(null);
  const sidecarRef = useRef(new Map());
  const [selected, setSelected] = useState(null);   // node cho EvidenceDrawer
  const [showRelations, setShowRelations] = useState(true);
  const [dirty, setDirty] = useState(false);

  const degraded = Boolean(data?.generator?.degraded);
  const missing = data?.generator?.missing || [];

  // (re)init khi đổi record
  useEffect(() => {
    if (!containerRef.current || !data) return;
    const { mindData, sidecar } = recordToMindElixir(data);
    sidecarRef.current = sidecar;
    const mind = new MindElixir({
      el: containerRef.current,
      direction: MindElixir.SIDE,
      editable: true,
      draggable: true,
      contextMenu: true,
      toolBar: false,       // toolbar riêng của mình
      keypress: true,
      allowUndo: true,
      theme: THEME,
    });
    mind.init(mindData);
    mindRef.current = mind;

    mind.bus.addListener("selectNodes", (nodes) => {
      const n = nodes?.[0];
      if (!n) return;
      const side = sidecarRef.current.get(n.id);
      setSelected({ id: n.id, title: n.topic, note: side?.note || "", chunkRefs: side?.chunkRefs || [] });
    });
    mind.bus.addListener("operation", () => setDirty(true));

    return () => { mindRef.current = null; containerRef.current && (containerRef.current.innerHTML = ""); };
  }, [data?.id]);

  // Esc đóng (confirm khi dirty — Task 8 nối)
  const requestClose = useCallback(() => {
    if (dirty && !window.confirm("Có thay đổi chưa lưu. Đóng và bỏ thay đổi?")) return;
    onClose?.();
  }, [dirty, onClose]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") requestClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [requestClose]);

  return (
    <div className="fixed inset-0 z-[1000] flex flex-col" style={{ background: "var(--bg-base)" }}>
      {/* Toolbar mỏng */}
      <div className="flex items-center gap-2 px-3 py-2 border-b flex-shrink-0"
        style={{ borderColor: "var(--border-color)", background: "var(--bg-sidebar)" }}>
        <span className="font-display text-[14px] font-semibold truncate text-text-primary">{data?.title || "Sơ đồ tư duy"}</span>
        {dirty && <span className="text-[11px] px-1.5 rounded" style={{ color: "var(--warn)" }}>● chưa lưu</span>}
        <div className="flex-1" />
        <label className="flex items-center gap-1 text-[12px] text-text-secondary cursor-pointer">
          <input type="checkbox" checked={showRelations} onChange={(e) => setShowRelations(e.target.checked)} />
          Quan hệ
        </label>
        {/* Nút Lưu (Task 8), Export PNG (Task 9) gắn thêm tại đây */}
        <button onClick={requestClose} aria-label="Đóng" className="p-1.5 rounded hover:bg-[var(--bg-hover)]">
          <Icon name="X" size={16} />
        </button>
      </div>
      {/* Degraded banner giữ từ v2 */}
      {degraded && (
        <div className="px-3 py-1.5 text-[12px] flex items-center gap-2 border-b"
          style={{ color: "var(--warn)", borderColor: "var(--border-color)", background: "var(--bg-elevated)" }}>
          <span>Bản đồ chưa đầy đủ{missing.length ? ` (thiếu: ${missing.join(", ")})` : ""}.</span>
          <button onClick={onRegenerate} disabled={regenerating} className="underline">
            {regenerating ? "Đang tạo lại…" : "Tạo lại"}
          </button>
        </div>
      )}
      {/* Map */}
      <div ref={containerRef} className={`flex-1 min-h-0 me-container${showRelations ? "" : " me-hide-arrows"}`} />
      {/* Evidence drawer giữ nguyên component */}
      {selected && (
        <EvidenceDrawer node={selected} onClose={() => setSelected(null)}
          generating={Boolean(data?.generating)} onAskAbout={data?.onAskAbout} />
      )}
    </div>
  );
}
```

- [ ] **Step 2: `mindmap.css`** — toggle arrows + quality floor:

```css
/* Ẩn layer arrows khi tắt toggle "Quan hệ".
   mind-elixir render mỗi arrow thành svg group id="a-<id>" — VERIFY selector
   bằng DOM thật lúc smoke; nếu group không nhận CSS từ class container,
   fallback: đổi sang mind.refresh(dataKhôngArrows). */
.me-hide-arrows g[id^="a-"] { display: none; }

.me-container { outline: none; }
.me-container :focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

@media (prefers-reduced-motion: reduce) {
  .me-container * { transition: none !important; animation: none !important; }
  .animate-spin { animation: none !important; }
}
```

- [ ] **Step 3: Shell swap** — `MindMapModal.jsx`: bỏ import `reactflow`/`ReactFlowProvider`/`MindmapView`/`LAYOUT_OPTIONS`; render `<MindElixirView data={data} onClose={onClose} onRegenerate={onRegenerate} regenerating={regenerating} />` trong `createPortal`. Empty-state giữ nguyên. Grep `LAYOUT_OPTIONS` call-site (`SidebarRight` import?) — nếu còn ai import từ modal thì giữ re-export từ `../mindmap/constants`.
- [ ] **Step 4:** `npm run build` xanh + `npx vitest run` xanh.
- [ ] **Step 5: Manual smoke** — BE chạy: mở map v2 có relations → nhánh cong màu archival, arrows nét đứt son + label; toggle Quan hệ ẩn/hiện (VERIFY selector `g[id^="a-"]`); click node → drawer trích đoạn thật; node tự thêm (contextMenu) → drawer "Chưa có bằng chứng"; map v1 legacy mở không vỡ; Esc đóng; dark mode nhìn được.
- [ ] **Step 6: Commit** — `git add FE/src/components/mindmap/MindElixirView.jsx FE/src/components/mindmap/mindmap.css FE/src/components/Layout/MindMapModal.jsx && git commit -m "feat(fe): mind-elixir viewer — Phong Doc theme, relation arrows, evidence drawer rewire"`

### Task 8: Edit → nút Lưu → PUT

**ĐIỀU KIỆN:** Task 6 + 7 xong.

**Files:**
- Modify: `FE/src/utils/api.js` (thêm `updateMindmap`), `FE/src/components/mindmap/MindElixirView.jsx` (nút Lưu), `FE/src/components/Layout/SidebarRight.jsx` (refresh list sau save)

**Interfaces:**
- Produces: `api.updateMindmap(id, record) -> Promise<savedRecord>`:

```js
export const updateMindmap = async (id, record) => {
  const res = await apiFetch(`/mindmaps/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(record),
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.error || `HTTP ${res.status}`);
  }
  return res.json();
};
```

- Nút **Lưu** trong toolbar `MindElixirView` (cạnh toggle Quan hệ), disabled khi `!dirty || saving`:

```jsx
const [saving, setSaving] = useState(false);
const handleSave = async () => {
  const mind = mindRef.current;
  if (!mind || !dirty) return;
  setSaving(true);
  try {
    const record = mindElixirToRecord(mind.getData(), sidecarRef.current, data);
    const saved = await updateMindmap(data.id, record);
    setDirty(false);
    toast("Đã lưu sơ đồ", { type: "success" });
    data.onSaved?.(saved);   // SidebarRight bơm callback để cập nhật list + showModalMap
  } catch (err) {
    toast(`Không lưu được: ${err.message}`, { type: "error" });
  } finally { setSaving(false); }
};
```

```jsx
<button onClick={handleSave} disabled={!dirty || saving}
  className="btn-primary text-[12px] disabled:opacity-40">
  {saving ? "Đang lưu…" : "Lưu"}
</button>
```

- `SidebarRight`: trong `modalMapData` bơm thêm `onSaved: (saved) => { setMindMaps(prev => prev.map(m => m.id === saved.id ? saved : m)); setShowModalMap(prev => prev ? { ...prev, ...saved } : prev); }`. LƯU Ý: map `id === "preview"` / đang generating → nút Lưu ẩn (record chưa có trong sqlite, PUT sẽ 404): render nút chỉ khi `data.id && data.id !== "preview" && !data.generating`.

- [ ] **Step 1:** Implement 3 file trên.
- [ ] **Step 2:** `npm run build` + `npx vitest run` xanh.
- [ ] **Step 3: Manual smoke** — mở map, đổi tên node (double-click), thêm node con, kéo node, vẽ arrow (context menu) → chấm "chưa lưu" hiện; Lưu → toast "Đã lưu sơ đồ"; F5 → mở lại từ list: mọi thay đổi còn nguyên (kể cả arrow mới thành relation `relates_to`); node đã xoá kéo relation chết biến mất; đóng khi dirty → confirm.
- [ ] **Step 4:** Kiểm cache-hit trả bản edit: bấm "Tạo sơ đồ" lại cùng nguồn (không force) → nhận bản ĐÃ SỬA (content_hash giữ — chủ ý spec §5).
- [ ] **Step 5: Commit** — `git add FE/src/utils/api.js FE/src/components/mindmap/MindElixirView.jsx FE/src/components/Layout/SidebarRight.jsx && git commit -m "feat(fe): manual mindmap editing with explicit save via PUT /mindmaps/<id>"`

### Task 9 [CODEX]: Export PNG (snapdom) + dọn ReactFlow

**ĐIỀU KIỆN:** Task 7 + 8 xong, smoke OK.

**Files:**
- Modify: `FE/package.json` (thêm `@zumer/snapdom`; GỠ `reactflow`, `elkjs`, `html-to-image` NẾU grep không còn nơi nào khác dùng), `FE/src/components/mindmap/MindElixirView.jsx` (nút Xuất PNG)
- Delete: `FE/src/components/mindmap/MindmapView.jsx`, `FE/src/components/mindmap/MindmapNodeCard.jsx`, `FE/src/components/mindmap/RelationEdge.jsx`, `FE/src/components/mindmap/useElkLayout.js`, `FE/src/components/mindmap/exportPng.js`
- `FE/src/components/mindmap/constants.js`: giữ NẾU còn export được dùng (grep `BRANCH_COLORS|LAYOUT_OPTIONS|DISPLAY_MODES|EDGE_MODES` toàn FE/src) — phần chỉ ReactFlow dùng thì xoá.

**Nội dung:**

- Export PNG (đường chính thức mind-elixir — `mind.exportSvg` deprecated):

```jsx
import { snapdom } from "@zumer/snapdom";

const handleExportPng = async () => {
  const mind = mindRef.current;
  if (!mind) return;
  try {
    const result = await snapdom(mind.nodes, { backgroundColor: getComputedStyle(document.documentElement).getPropertyValue("--bg-base").trim() || "#ECE7DB" });
    const date = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    const safeTitle = String(data?.title || "mindmap").replace(/[\\/:*?"<>|]+/g, "_").slice(0, 60);
    await result.download({ format: "png", filename: `mindmap-${safeTitle}-${date}` });
  } catch (err) {
    toast(`Không xuất được PNG: ${err.message}`, { type: "error" });
  }
};
```

Nút trong toolbar cạnh Lưu: `<button onClick={handleExportPng} className="text-[12px] underline text-text-secondary">Xuất PNG</button>`. VERIFY `snapdom(...)` option key (`backgroundColor` vs `background`) theo README @zumer/snapdom lúc implement; nền PHẢI đặc (không transparent).

- [ ] **Step 1:** `cd FE && npm i @zumer/snapdom`; implement nút export.
- [ ] **Step 2:** Grep `reactflow|elkjs|html-to-image|MindmapView|RelationEdge|MindmapNodeCard|useElkLayout|exportPng` toàn `FE/src` — xoá file chết + import chết; chỉ `npm uninstall` dep khi 0 call-site còn lại.
- [ ] **Step 3:** `npm run build` xanh; `npx vitest run` xanh.
- [ ] **Step 4: Manual** — Xuất PNG → file mở được, nền đặc, đủ node.
- [ ] **Step 5: Commit** — `git add -A FE && git commit -m "feat(fe): png export via snapdom; remove dead ReactFlow mindmap code"`

### Task 10: Docs + playbook (đóng dự án)

**Files:**
- Modify: `docs/MINDMAP_WORKFLOW.md` (phần FE viewer + luồng generate: nền, chip, resume, edit/save), `.playbook/lessons-learned.md` (mục mới: "FE hard-timeout poll giết UX job dài" — root cause, fix poll-until-terminal + stall guard; "mind-elixir sidecar" — vì sao không tin field lạ), `.playbook/known-issues.md` (annotate mục cache-hit/job_id nếu hành vi đổi).

- [ ] **Step 1:** Viết docs + playbook.
- [ ] **Step 2:** Full suite: `cd BE && python -m pytest tests/ --ignore=tests/test_crag_graph.py --ignore=tests/test_hitl_graph.py --ignore=tests/test_nli_graph.py --ignore=tests/test_rerank_graph.py --ignore=tests/test_supervisor_graph.py` xanh (5 file ignore theo known-issue env trôi); `cd FE && npx vitest run && npm run build` xanh.
- [ ] **Step 3: Commit** — `git add docs .playbook && git commit -m "docs(mindmap): ux v3 — background generation + mind-elixir viewer + edit"`

---

## Thứ tự & song song hoá

```
Task 1 ─┐
Task 2 [CODEX] ─┼→ Task 4 (nền)
Task 3 ─┘
Task 5 ─→ Task 7 ─┐
Task 6 [CODEX] ───┼→ Task 8 → Task 9 [CODEX] → Task 10
(6 // với 5/7)    ┘
```

Codex chạy song song: Task 2 ngay từ đầu; Task 6 ngay sau khi plan chốt (contract cố định trong plan); Task 9 sau smoke Task 8.

## Self-review đã chạy

- Spec coverage: §3→T1/T2/T3/T4, §4.1→T5, §4.2→T7/T8/T9, §5→T6, §6.1→T1/T2/T5 tests, §6.2→T6 tests, §6.3 manual→T4/T7/T8/T9 steps, §7→codex tags, §8 ngoài phạm vi tôn trọng (không refactor alert, không SSE).
- Placeholder: các điểm "VERIFY lúc implement" (selector `g[id^="a-"]`, option `snapdom` background, env vitest jsdom) là verification có chủ đích trên API bên thứ ba — kèm fallback cụ thể, không phải TBD.
- Type consistency: `sidecar` Map value `{note, chunkRefs, kind}` thống nhất T5/T7/T8 (camelCase `chunkRefs` phía FE, snake_case `chunk_refs` chỉ trong record v2); poller callbacks T1↔T4; `updateMindmap` T8 khớp route T6; `onSaved` bơm qua `data` khớp pattern `modalMapData` hiện có.
