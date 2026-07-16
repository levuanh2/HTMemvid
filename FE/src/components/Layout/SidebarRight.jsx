import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import MindMapModal from "./MindMapModal";
import SummaryModal from "./SummaryModal";
import { apiFetch, generateMindmap, cancelMindmap, generateSummary, cancelSummary, isUnauthorizedError, isNotFoundOrForbiddenError, getUserFriendlyApiError } from "../../utils/api";

// Permission-safe toast text: 401/403/404 → friendly line (no raw error/id); else
// keep the existing detail message.
function _errText(err, prefix, fallback) {
  if (isUnauthorizedError(err) || isNotFoundOrForbiddenError(err)) return getUserFriendlyApiError(err);
  return err?.message ? `${prefix}: ${err.message}` : fallback;
}
import { createMindmapPoller, stageLabel } from "../../utils/mindmapJob";
import { createSummaryPoller, stageLabel as summaryStageLabel, LENGTH_MODES, SUMMARY_MODES } from "../../utils/summaryJob";
import { saveActiveMindmapJob, loadActiveMindmapJob, clearActiveMindmapJob } from "../../utils/activeMindmapJob";
import { saveActiveSummaryJob, loadActiveSummaryJob, clearActiveSummaryJob } from "../../utils/activeSummaryJob";
import { toast } from "../ui/Toaster";
import { Icon } from "../ui/Icon";
import Spinner from "../ui/Spinner";
import { normStem } from "../../utils/evidence";
import MdSnippet from "../ui/Markdown";

const IDLE_JOB_UI = { running: false, label: "", progress: null, stalled: false };

// ── Helpers ──────────────────────────────────────────
const formatTimeAgo = (isoDate) => {
  if (!isoDate) return "Không xác định";
  const diff = (Date.now() - new Date(isoDate).getTime()) / 1000;
  if (isNaN(diff)) return "Không xác định";
  if (diff < 60) return `${Math.floor(diff)}s trước`;
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`;
  return `${Math.floor(diff / 86400)} ngày trước`;
};

// ── Saved-artifact card ───────────────────────────────
const ListCard = ({ title, meta, icon, onOpen, onDelete, deleteLabel = "Xóa" }) => (
  <div
    onClick={onOpen}
    className="flex items-center gap-3 px-3 py-2.5 rounded-[7px] border border-border hover:border-brand/30 cursor-pointer transition-all group transition-theme"
    style={{ background: "var(--bg-card)" }}
  >
    <div className="w-8 h-8 rounded-[6px] flex items-center justify-center flex-shrink-0 text-brand" style={{ background: "color-mix(in srgb, var(--accent) 10%, transparent)" }}>
      <Icon name={icon} size={15} />
    </div>
    <div className="flex-1 min-w-0">
      <div className="text-[13px] font-semibold text-text-primary truncate">{title}</div>
      <div className="text-[11px] text-text-muted flex items-center gap-1 mt-0.5 font-mono">
        <Icon name="Clock" size={10} />{meta}
      </div>
    </div>
    <button
      onClick={(e) => { e.stopPropagation(); onDelete(); }}
      title={deleteLabel}
      aria-label={deleteLabel}
      className="w-7 h-7 rounded-[6px] inline-flex items-center justify-center text-text-muted opacity-0 group-hover:opacity-100 hover:text-[var(--err)] transition-all"
    >
      <Icon name="Trash2" size={13} />
    </button>
  </div>
);

// ── Artifact selector chips ───────────────────────────
const ARTIFACTS = [
  { key: "mindmap", label: "Sơ đồ", icon: "Network" },
  { key: "summary", label: "Tóm tắt", icon: "ScrollText" },
];

// ── Main component ────────────────────────────────────
export default function SidebarRight({ selectedSources, evidence, highlight, onHighlight, onClose, onAskAbout }) {
  const [artifactTab, setArtifactTab] = useState("mindmap");
  const [mindMaps, setMindMaps]           = useState([]);
  const [showModalMap, setShowModalMap]   = useState(null);
  const [showSummaryModal, setShowSummaryModal] = useState(null);
  // Task 4 — background generation: chip state driven by the Task 1 poller
  // (no FE hard-timeout; onTick reports stage label / progress / stalled).
  const [mindmapJobUi, setMindmapJobUi] = useState(IDLE_JOB_UI);
  const [summaryJobUi, setSummaryJobUi] = useState(IDLE_JOB_UI);
  const [loading, setLoading]             = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false); // POST round-trip của /generate-summary
  const [summaryLength, setSummaryLength] = useState("medium");
  const [summaryMode, setSummaryMode] = useState("standard");  // Phase 3: standard | study
  const [summaryCancelNotice, setSummaryCancelNotice] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [summaries, setSummaries]         = useState([]);
  // `mindmapGenerating` now only drives the viewer's in-overlay "generating"
  // banner during a "Tạo lại" (regenerate) run — plain "Tạo sơ đồ" no longer
  // opens the overlay while the job runs, so it stays false for that path.
  const [mindmapGenerating, setMindmapGenerating] = useState(false);
  const [mindmapCancelNotice, setMindmapCancelNotice] = useState(false);

  const frameRefs = useRef(new Map());

  // ── Mindmap job refs ───────────────────────────────
  const pollerRef = useRef(null); // fresh createMindmapPoller() instance per run
  const currentMindmapJobIdRef = useRef(null);
  // ── Summary job refs (mirror mindmap — poller riêng, không chia sẻ ref) ──
  const summaryPollerRef = useRef(null);
  const currentSummaryJobIdRef = useRef(null);
  const summaryCancelRequestedRef = useRef(false);
  // Fix Round 1 (Fix 3): guards the "Đã huỷ tạo sơ đồ" notice so it fires exactly
  // once per generation regardless of which path notices the cancel first — the
  // optimistic click path (handleCancelMindMap, fires immediately) or the
  // authoritative status-driven path (poll tick observing status "cancelled").
  const cancelNoticeShownRef = useRef(false);
  // true từ lúc user bấm Huỷ tới khi job đạt terminal — giữ label "Đang huỷ…"
  // không bị onTick ghi đè bằng stage label thường.
  const cancelRequestedRef = useRef(false);
  const showCancelNotice = useCallback(() => {
    if (cancelNoticeShownRef.current) return;
    cancelNoticeShownRef.current = true;
    setMindmapCancelNotice(true);
  }, []);

  // ── Fetchers (logic unchanged) ────────────────────
  const fetchMindMaps = useCallback(async () => {
    try {
      const res = await apiFetch(`/mindmaps`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setMindMaps(Array.isArray(data?.mindmaps) ? data.mindmaps : []);
    } catch (err) { console.error("Mind map fetch error:", err); setMindMaps([]); }
    finally { setInitialLoading(false); }
  }, []);

  const fetchSummaries = useCallback(async () => {
    try {
      const res = await apiFetch(`/summaries`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSummaries(Array.isArray(data?.summaries) ? data.summaries : []);
    } catch (err) { console.error("Summary fetch error:", err); setSummaries([]); }
  }, []);

  useEffect(() => { fetchMindMaps(); fetchSummaries(); }, [fetchMindMaps, fetchSummaries]);

  // ── Scroll the highlighted source frame into view ──
  useEffect(() => {
    if (!highlight) return;
    const key = `${normStem(highlight.stem)}::${String(highlight.chunkId ?? "")}`;
    const el = frameRefs.current.get(key);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [highlight]);

  // ── Mindmap job completion handlers (shared by fresh generate, resume,
  // and cache-hit) ────────────────────────────────────
  // `isRegenerate` only matters for the "Tạo lại" (force=true) path — that's
  // the one case where `mindmapGenerating` was turned on (viewer overlay
  // stays open showing the old map with a banner), so it's the one case that
  // needs it turned back off on completion.
  const handleMindmapDone = useCallback(async (data, sourceList, { resumed = false, isRegenerate = false } = {}) => {
    clearActiveMindmapJob();
    if (isRegenerate) setMindmapGenerating(false);
    const hasNodes = (Array.isArray(data?.nodes) && data.nodes.length > 0) ||
      (Array.isArray(data?.diagram?.nodes) && data.diagram.nodes.length > 0);
    if (!hasNodes) {
      toast("Không tạo được sơ đồ từ tài liệu đã chọn (nội dung quá ngắn hoặc không trích được ý chính). Thử chọn tài liệu khác.", { type: "error" });
      return;
    }
    // Spread `data` first so v2 fields (schema_version/relations/generator) that
    // the mindmap viewer needs for relations + the degraded banner survive —
    // the explicit keys below only backfill defaults, they don't drop anything.
    const record = {
      ...data,
      id: data.id || Date.now().toString(),
      title: data.title || "Sơ đồ tư duy",
      nodes: Array.isArray(data.nodes) ? data.nodes : [],
      diagram: data.diagram || null,
      sources: Array.isArray(data.sources) ? data.sources : sourceList,
      createdAt: data.createdAt || data.created_at || new Date().toISOString(),
      strategy: data.strategy || "iterative",
      initialLayoutType: "napkin",
    };
    setMindMaps((prev) => [record, ...prev.filter((item) => item.id !== record.id)]);
    await fetchMindMaps();
    if (resumed) {
      // The tab that started this job is gone — don't yank the user into a
      // full-screen overlay for a job they may not even remember starting.
      toast("Sơ đồ đã xong trong lúc bạn vắng mặt — mở từ danh sách", { type: "info" });
    } else {
      toast("Sơ đồ sẵn sàng", { type: "success" });
      setShowModalMap(record);
    }
  }, [fetchMindMaps]);

  const handleMindmapError = useCallback((err, { isRegenerate = false } = {}) => {
    clearActiveMindmapJob();
    if (isRegenerate) setMindmapGenerating(false);
    console.error("Mind Map Error:", err);
    toast(err?.message ? `Không tạo được sơ đồ: ${err.message}` : "Không tạo được sơ đồ, kiểm tra console!", { type: "error" });
  }, []);

  // Starts a fresh poller instance (Task 1's createMindmapPoller has no hard
  // timeout and does NOT guard double-start) and drives the chip state.
  const startMindmapPoller = useCallback((jobId, sourceList, { resumed = false, isRegenerate = false } = {}) => {
    // Fresh instance per run (createMindmapPoller does not self-guard against
    // double-start) — stop whatever was previously tracked in the ref first so
    // a second click can't leave an orphaned poller ticking in the background.
    pollerRef.current?.stop();
    currentMindmapJobIdRef.current = jobId;
    cancelRequestedRef.current = false;
    setMindmapJobUi({ running: true, label: "Đang tạo sơ đồ…", progress: null, stalled: false });

    const fetchStatus = (id) =>
      apiFetch(`/mindmap-status/${encodeURIComponent(id)}`).then((r) => {
        if (!r.ok) {
          const e = new Error(`HTTP ${r.status}`);
          e.status = r.status; // poller cần biết 404 = job không tồn tại (terminal)
          throw e;
        }
        return r.json();
      });

    const poller = createMindmapPoller({
      fetchStatus,
      onTick: (status, { stalled }) => {
        setMindmapJobUi({
          running: true,
          label: cancelRequestedRef.current ? "Đang huỷ…" : stageLabel(status),
          progress: typeof status.progress === "number" ? status.progress : null,
          stalled,
        });
      },
      onDone: (result) => {
        setMindmapJobUi(IDLE_JOB_UI);
        currentMindmapJobIdRef.current = null;
        handleMindmapDone(result, sourceList, { resumed, isRegenerate });
      },
      onError: (err) => {
        setMindmapJobUi(IDLE_JOB_UI);
        currentMindmapJobIdRef.current = null;
        handleMindmapError(err, { isRegenerate });
      },
      onCancelled: () => {
        setMindmapJobUi(IDLE_JOB_UI);
        currentMindmapJobIdRef.current = null;
        if (isRegenerate) setMindmapGenerating(false);
        clearActiveMindmapJob();
        showCancelNotice();
      },
    });
    pollerRef.current = poller;
    poller.start(jobId);
  }, [handleMindmapDone, handleMindmapError, showCancelNotice]);

  // Resume-after-reload (Task 4 point 6): on mount, pick up a job that was
  // still running when the page unloaded and re-attach a poller so the chip
  // reappears. No StrictMode guard on purpose: the cleanup effect below stops
  // the poller between StrictMode's dev double-invoke passes, and a run-once
  // ref would block pass 2 from starting a replacement (chip stuck forever).
  // Re-running is safe — startMindmapPoller stops the previous instance first.
  useEffect(() => {
    const active = loadActiveMindmapJob();
    if (active?.jobId) {
      startMindmapPoller(active.jobId, active.sources, { resumed: true, isRegenerate: false });
    }
    const activeSummary = loadActiveSummaryJob();
    if (activeSummary?.jobId) {
      startSummaryPoller(activeSummary.jobId, { resumed: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => { pollerRef.current?.stop(); summaryPollerRef.current?.stop(); }, []);

  // ── Handlers ───────────────────────────────────────
  // Shared by "Tạo sơ đồ" (force=false, uses BE content-hash cache) and the
  // mindmap viewer's degraded-banner "Tạo lại" (force=true, bypasses cache).
  const runMindmapGeneration = async (sourceList, { force = false } = {}) => {
    if (!sourceList?.length) { toast("Vui lòng chọn ít nhất một tài liệu để tạo Sơ đồ!", { type: "error" }); return; }
    setLoading(true);
    setMindmapCancelNotice(false);
    cancelNoticeShownRef.current = false;
    if (force) setMindmapGenerating(true); // keep the open viewer's banner up during "Tạo lại"
    try {
      const startData = await generateMindmap(sourceList, { force });
      if (startData.error) throw new Error(startData.error);

      if (startData.status === "done" && startData.result) {
        // Cache-hit (content_hash match, force=false): BE returns the record
        // straight away with no job_id — skip polling entirely instead of
        // throwing "Server không trả job_id." (known issue, fixed here).
        await handleMindmapDone(startData.result, sourceList, { resumed: false, isRegenerate: force });
        return;
      }

      if (!startData.job_id) throw new Error("Server không trả job_id.");
      // Generation now runs in the background — persist the job so a reload
      // mid-flight can resume polling (Task 4 point 6) instead of the user
      // having to F5 and lose track of it.
      saveActiveMindmapJob({ jobId: startData.job_id, sources: sourceList, startedAt: Date.now() });
      startMindmapPoller(startData.job_id, sourceList, { resumed: false, isRegenerate: force });
    } catch (err) {
      console.error("Mind Map Error:", err);
      toast(_errText(err, "Không tạo được sơ đồ", "Không tạo được sơ đồ, kiểm tra console!"), { type: "error" });
      if (force) setMindmapGenerating(false);
    }
    finally {
      setLoading(false);
    }
  };

  const handleGenerateMindMap = () => runMindmapGeneration(selectedSources, { force: false });

  // Degraded-banner "Tạo lại": regenerate the map that's currently open, using
  // the sources it was built from (falls back to the sidebar selection if the
  // record doesn't carry its own).
  const handleRegenerateMindMap = () => {
    const sources = showModalMap?.sources?.length ? showModalMap.sources : selectedSources;
    return runMindmapGeneration(sources, { force: true });
  };

  // Real cancel (codex #4): BE chỉ kiểm cờ cancel GIỮA các node — LLM call đang
  // bay không dừng được, job có thể vẫn chạy xong và persist. Vì vậy KHÔNG stop
  // poller ngay: gửi cancel rồi tiếp tục poll tới trạng thái terminal thật
  // ("Đang huỷ…" → onCancelled xác nhận / onDone nếu job kịp xong trước cancel).
  const handleCancelMindMap = () => {
    const jobId = currentMindmapJobIdRef.current;
    if (!jobId) { // không có job đang theo dõi — dọn UI là đủ
      pollerRef.current?.stop();
      pollerRef.current = null;
      clearActiveMindmapJob();
      setMindmapJobUi(IDLE_JOB_UI);
      setMindmapGenerating(false);
      showCancelNotice();
      return;
    }
    cancelRequestedRef.current = true;
    setMindmapJobUi((prev) => ({ ...prev, running: true, label: "Đang huỷ…" }));
    cancelMindmap(jobId).catch((err) => console.error("[MindMap] cancel request failed:", err));
  };

  // "Hỏi về đoạn này" (EvidenceDrawer) → close the mindmap overlay so the chat
  // underneath is visible again, then hand the snippet up to MainLayout to
  // prefill + focus the composer.
  const handleAskAbout = useCallback((snippet) => {
    setShowModalMap(null);
    onAskAbout?.(snippet);
  }, [onAskAbout]);

  // Task 8: after MindElixirView's explicit Save (PUT /mindmaps/<id>) succeeds,
  // sync both the saved-list card and the still-open modal with the returned
  // record — spread `saved` first so v2 fields (schema_version/relations/
  // generator) survive, mirroring the same rebuild-drops-fields lesson as
  // handleMindmapDone above.
  const handleMindmapSaved = useCallback((saved) => {
    setMindMaps((prev) => prev.map((m) => (m.id === saved.id ? saved : m)));
    setShowModalMap((prev) => (prev ? { ...prev, ...saved } : prev));
  }, []);

  const handleDeleteMap = async (id) => {
    if (!window.confirm("Xóa sơ đồ này?")) return;
    try {
      const res = await apiFetch(`/mindmaps/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMindMaps((prev) => prev.filter((m) => m.id !== id));
      await fetchMindMaps();
    } catch (err) { console.error("Mind Map delete error:", err); alert("Không xóa được sơ đồ!"); }
  };

  // ── Summary v2 job flow (mirror mindmap: poller + resume + cancel thật) ──
  const handleSummaryDone = useCallback(async (record, { resumed = false } = {}) => {
    clearActiveSummaryJob();
    await fetchSummaries(); // BE đã tự persist record — chỉ cần refresh danh sách
    if (resumed) {
      toast("Tóm tắt đã xong trong lúc bạn vắng mặt — mở từ danh sách", { type: "info" });
    } else {
      toast("Tóm tắt sẵn sàng", { type: "success" });
      setShowSummaryModal(record);
    }
  }, [fetchSummaries]);

  const handleSummaryError = useCallback((err) => {
    clearActiveSummaryJob();
    console.error("Summary Error:", err);
    toast(_errText(err, "Không tạo được tóm tắt", "Không tạo được tóm tắt, kiểm tra console!"), { type: "error" });
  }, []);

  const startSummaryPoller = useCallback((jobId, { resumed = false } = {}) => {
    // poller không tự guard double-start — dừng instance cũ trước khi gán mới
    summaryPollerRef.current?.stop();
    currentSummaryJobIdRef.current = jobId;
    summaryCancelRequestedRef.current = false;
    setSummaryJobUi({ running: true, label: "Đang tóm tắt…", progress: null, stalled: false });

    const fetchStatus = (id) =>
      apiFetch(`/summary-status/${encodeURIComponent(id)}`).then((r) => {
        if (!r.ok) {
          const e = new Error(`HTTP ${r.status}`);
          e.status = r.status; // 404 = job không tồn tại (terminal)
          throw e;
        }
        return r.json();
      });

    const poller = createSummaryPoller({
      fetchStatus,
      onTick: (status, { stalled }) => {
        setSummaryJobUi({
          running: true,
          label: summaryCancelRequestedRef.current ? "Đang huỷ…" : summaryStageLabel(status),
          progress: typeof status.progress === "number" ? status.progress : null,
          stalled,
        });
      },
      onDone: (result) => {
        setSummaryJobUi(IDLE_JOB_UI);
        currentSummaryJobIdRef.current = null;
        handleSummaryDone(result, { resumed });
      },
      onError: (err) => {
        setSummaryJobUi(IDLE_JOB_UI);
        currentSummaryJobIdRef.current = null;
        handleSummaryError(err);
      },
      onCancelled: () => {
        setSummaryJobUi(IDLE_JOB_UI);
        currentSummaryJobIdRef.current = null;
        clearActiveSummaryJob();
        setSummaryCancelNotice(true);
      },
    });
    summaryPollerRef.current = poller;
    poller.start(jobId);
  }, [handleSummaryDone, handleSummaryError]);

  const handleGenerateSummary = async () => {
    if (!selectedSources?.length) { toast("Vui lòng chọn ít nhất một tài liệu để tóm tắt!", { type: "error" }); return; }
    setSummaryLoading(true);
    setSummaryCancelNotice(false);
    try {
      const startData = await generateSummary(selectedSources, { lengthMode: summaryLength, mode: summaryMode });
      if (startData.error) throw new Error(startData.error);

      if (startData.status === "done" && startData.result) {
        // Cache-hit: BE trả record thẳng, KHÔNG có job_id — branch trước (aec6017)
        await handleSummaryDone(startData.result, { resumed: false });
        return;
      }

      if (!startData.job_id) throw new Error("Server không trả job_id.");
      saveActiveSummaryJob({ jobId: startData.job_id, sources: selectedSources, startedAt: Date.now(), extra: { lengthMode: summaryLength, mode: summaryMode } });
      startSummaryPoller(startData.job_id, { resumed: false });
    } catch (err) {
      handleSummaryError(err);
    } finally {
      setSummaryLoading(false);
    }
  };

  // Cancel thật (mirror mindmap): gửi cờ rồi TIẾP TỤC poll tới terminal —
  // LLM call đang bay không dừng được, job có thể vẫn kịp xong.
  const handleCancelSummary = () => {
    const jobId = currentSummaryJobIdRef.current;
    if (!jobId) {
      summaryPollerRef.current?.stop();
      summaryPollerRef.current = null;
      clearActiveSummaryJob();
      setSummaryJobUi(IDLE_JOB_UI);
      setSummaryCancelNotice(true);
      return;
    }
    summaryCancelRequestedRef.current = true;
    setSummaryJobUi((prev) => ({ ...prev, running: true, label: "Đang huỷ…" }));
    cancelSummary(jobId).catch((err) => console.error("[Summary] cancel request failed:", err));
  };

  const handleDeleteSummary = async (id) => {
    if (!id) { alert("Không xác định được ID tóm tắt"); return; }
    if (!window.confirm("Xóa tóm tắt này?")) return;
    try {
      const res = await apiFetch(`/summaries/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
      await fetchSummaries();
    } catch (err) { console.error("Delete summary error:", err); alert("Không xóa được tóm tắt!"); }
  };

  // ── Derived ───────────────────────────────────────
  const chunks = Array.isArray(evidence?.chunks) ? evidence.chunks : [];
  const sourceStems = Array.isArray(evidence?.sources) ? evidence.sources : [];
  // `mindmapJobUi.running` is included so the button stays disabled for the
  // whole background run, not just the initial POST round-trip — otherwise a
  // second click while a job (or a resumed one) is in flight could kick off a
  // duplicate generation.
  const isGenerating = artifactTab === "mindmap"
    ? (loading || mindmapJobUi.running)
    : (summaryLoading || summaryJobUi.running);
  const onGenerate = artifactTab === "mindmap" ? handleGenerateMindMap : handleGenerateSummary;
  // Chip tiến độ dùng chung markup — jobUi/cancel theo tab đang mở
  const jobUi = artifactTab === "mindmap" ? mindmapJobUi : summaryJobUi;
  const onCancelJob = artifactTab === "mindmap" ? handleCancelMindMap : handleCancelSummary;
  const cancelNotice = artifactTab === "mindmap" ? mindmapCancelNotice : summaryCancelNotice;
  const cancelNoticeText = artifactTab === "mindmap" ? "Đã huỷ tạo sơ đồ." : "Đã huỷ tạo tóm tắt.";

  // MindMapModal forwards `data` to MindmapView verbatim, so this is how
  // generating/progress/cancel/ask-about reach it without widening that
  // shell's prop list — applies to every open map (fresh, regenerated, or
  // reopened from the saved list), not just the live-generation preview.
  const modalMapData = useMemo(() => (showModalMap ? {
    ...showModalMap,
    generating: mindmapGenerating,
    progress: mindmapJobUi.progress,
    onCancel: handleCancelMindMap,
    onAskAbout: handleAskAbout,
    onSaved: handleMindmapSaved,
  } : null), [showModalMap, mindmapGenerating, mindmapJobUi.progress, handleCancelMindMap, handleAskAbout, handleMindmapSaved]);

  // ── Render ────────────────────────────────────────
  return (
    <div className="flex flex-col h-full overflow-hidden transition-theme" style={{ background: "var(--bg-sidebar)" }}>

      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-border flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Icon name="Quote" size={15} className="text-brand flex-shrink-0" />
          <span className="text-[13px] font-semibold text-text-primary">Lề bằng chứng</span>
        </div>
        <button onClick={onClose} className="md:hidden icon-btn w-8 h-8" aria-label="Đóng">
          <Icon name="X" size={16} />
        </button>
      </div>

      {/* ── EVIDENCE MARGIN ── */}
      <div className="flex-1 min-h-0 overflow-y-auto px-3 py-3">
        {chunks.length > 0 ? (
          <>
            <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-text-muted mb-2 px-1">
              Nguồn của câu trả lời ({chunks.length})
            </div>
            <div className="flex flex-col gap-2">
              {chunks.map((c, i) => {
                const stem = c.stem || "";
                const chunkId = c.chunk_id ?? "";
                const key = `${normStem(stem)}::${String(chunkId)}`;
                const active = highlight && normStem(highlight.stem) === normStem(stem) && String(highlight.chunkId) === String(chunkId);
                return (
                  <div
                    key={`${key}-${i}`}
                    ref={(el) => { if (el) frameRefs.current.set(key, el); else frameRefs.current.delete(key); }}
                    className={`evidence-frame ${active ? "evidence-frame--active" : ""} p-3 cursor-default`}
                    onMouseEnter={() => onHighlight?.({ stem, chunkId })}
                    onMouseLeave={() => onHighlight?.(null)}
                  >
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="w-5 h-5 rounded-[4px] inline-flex items-center justify-center text-[11px] font-mono font-semibold flex-shrink-0"
                        style={{ color: "var(--accent)", border: "1px solid color-mix(in srgb, var(--accent) 35%, transparent)" }}>
                        {i + 1}
                      </span>
                      <span className="coord truncate flex-1" title={stem}>
                        {stem || "nguồn"}{chunkId !== "" ? ` · đoạn ${chunkId}` : ""}
                      </span>
                    </div>
                    {c.snippet && (
                      <MdSnippet text={c.snippet}
                        className="font-reading text-[13px] leading-[1.55] text-text-secondary line-clamp-4" />
                    )}
                  </div>
                );
              })}
            </div>
          </>
        ) : sourceStems.length > 0 ? (
          <>
            <div className="text-[11px] font-mono uppercase tracking-[0.12em] text-text-muted mb-2 px-1">
              Tài liệu đã dùng ({sourceStems.length})
            </div>
            <div className="flex flex-col gap-1.5">
              {sourceStems.map((s, i) => (
                <div key={`${s}-${i}`} className="evidence-frame p-2.5 flex items-center gap-2">
                  <Icon name="FileText" size={13} className="text-slate flex-shrink-0" />
                  <span className="coord truncate" title={s}>{s}</span>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="text-center px-5 pt-12 text-text-muted">
            <Icon name="Quote" size={26} className="mx-auto mb-3 text-text-muted opacity-60" />
            <p className="text-[13px] leading-[1.6] text-text-secondary">
              Đặt một câu hỏi — nguồn dẫn chứng của câu trả lời sẽ hiện ở đây, khớp với các chú thích<sup className="cite-chip mx-0.5">n</sup>trong câu trả lời.
            </p>
          </div>
        )}
      </div>

      {/* ── ARTIFACTS — tạo từ tài liệu ── */}
      <div className="flex-shrink-0 border-t border-border px-3 pt-3 pb-3">
        <div className="flex items-center gap-2 mb-2.5">
          <span className="text-[11px] font-mono uppercase tracking-[0.12em] text-text-muted">Tạo từ tài liệu</span>
          <div className="flex gap-1 ml-auto">
            {ARTIFACTS.map((a) => (
              <button
                key={a.key}
                onClick={() => setArtifactTab(a.key)}
                className={`pill-tab !px-2.5 !py-1 ${artifactTab === a.key ? "pill-tab-active" : ""}`}
                aria-pressed={artifactTab === a.key}
              >
                <Icon name={a.icon} size={13} /> {a.label}
              </button>
            ))}
          </div>
        </div>

        {/* Độ dài tóm tắt — nằm trong content_hash BE: đổi mode = bản tóm tắt khác */}
        {artifactTab === "summary" && (
          <div className="flex gap-1 mb-2.5" role="radiogroup" aria-label="Độ dài tóm tắt">
            {LENGTH_MODES.map((m) => (
              <button
                key={m.value}
                onClick={() => setSummaryLength(m.value)}
                className={`pill-tab flex-1 !px-2 !py-1.5 justify-center ${summaryLength === m.value ? "pill-tab-active" : ""}`}
                role="radio"
                aria-checked={summaryLength === m.value}
                disabled={isGenerating}
              >
                {m.label}
              </button>
            ))}
          </div>
        )}

        {/* Kiểu tóm tắt — standard (thường) vs study (ôn tập). Trực giao độ dài; cũng nằm
            trong content_hash BE nên đổi kiểu = bản tóm tắt khác. */}
        {artifactTab === "summary" && (
          <div className="flex gap-1 mb-2.5" role="radiogroup" aria-label="Kiểu tóm tắt">
            {SUMMARY_MODES.map((m) => (
              <button
                key={m.value}
                onClick={() => setSummaryMode(m.value)}
                className={`pill-tab flex-1 !px-2 !py-1.5 justify-center ${summaryMode === m.value ? "pill-tab-active" : ""}`}
                role="radio"
                aria-checked={summaryMode === m.value}
                disabled={isGenerating}
              >
                {m.label}
              </button>
            ))}
          </div>
        )}

        <button
          onClick={onGenerate}
          disabled={isGenerating || !selectedSources?.length}
          className="btn-secondary w-full !py-2.5 inline-flex items-center justify-center gap-2"
        >
          {isGenerating ? (
            <><Spinner size={14} /> Đang tạo…</>
          ) : (
            <><Icon name="Plus" size={15} /> Tạo {artifactTab === "mindmap" ? "sơ đồ" : "tóm tắt"}</>
          )}
        </button>

        {/* Background-generation progress chip — dùng chung cho cả hai tab
            (jobUi/onCancelJob đã switch theo artifactTab ở phần Derived). */}
        {jobUi.running && (
          <div className="mx-3 mb-2 flex items-center gap-2 rounded-[8px] border px-2.5 py-2 text-[12px]"
            style={{ borderColor: jobUi.stalled ? "var(--warn)" : "var(--border-strong)", background: "var(--bg-elevated)" }}>
            <span className="animate-spin inline-block w-3.5 h-3.5 rounded-full border-2 border-t-transparent"
              style={{ borderColor: "var(--accent)", borderTopColor: "transparent" }} aria-hidden />
            <span className="flex-1 truncate text-text-secondary">
              {jobUi.stalled ? "Có vẻ kẹt — vẫn đang chờ máy chủ…" : jobUi.label}
              {typeof jobUi.progress === "number" ? ` (${jobUi.progress}%)` : ""}
            </span>
            <button onClick={onCancelJob} className="text-[12px] underline text-text-muted hover:text-accent">Huỷ</button>
          </div>
        )}
        {!jobUi.running && cancelNotice && (
          <div className="mt-2 flex items-center gap-1.5 text-[12px] text-text-muted">
            <Icon name="Ban" size={12} /> {cancelNoticeText}
          </div>
        )}

        {/* Saved list */}
        <div className="mt-3 max-h-[34vh] overflow-y-auto">
          {artifactTab === "mindmap" ? (
            initialLoading ? (
              <div className="flex items-center justify-center py-6 text-text-muted text-[12px] gap-2"><Spinner size={14} /> Đang tải…</div>
            ) : mindMaps.length === 0 ? (
              <p className="text-[12px] text-text-muted text-center py-4">Chưa có sơ đồ nào được lưu.</p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {mindMaps.map((map) => (
                  <ListCard key={map.id} icon="Network" title={map.title}
                    meta={`${map.sources?.length || 0} tài liệu · ${formatTimeAgo(map.createdAt || map.created_at)}`}
                    onOpen={() => setShowModalMap(map)} onDelete={() => handleDeleteMap(map.id)} deleteLabel="Xóa sơ đồ" />
                ))}
              </div>
            )
          ) : (
            summaries.length === 0 ? (
              <p className="text-[12px] text-text-muted text-center py-4">Chưa có tóm tắt nào được lưu.</p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {summaries.map((item) => (
                  <ListCard key={item.id} icon="ScrollText" title={item.title || "Tóm tắt"}
                    meta={formatTimeAgo(item.created_at || item.createdAt)}
                    onOpen={() => setShowSummaryModal(item)} onDelete={() => handleDeleteSummary(item.id)} deleteLabel="Xóa tóm tắt" />
                ))}
              </div>
            )
          )}
        </div>
      </div>

      {/* ── MODALS ── */}
      {showModalMap && (
        <MindMapModal
          data={modalMapData}
          initialLayoutType={showModalMap.initialLayoutType || "napkin"}
          onClose={() => setShowModalMap(null)}
          onRegenerate={handleRegenerateMindMap}
          regenerating={mindmapGenerating}
        />
      )}
      {showSummaryModal && (
        <SummaryModal data={showSummaryModal} onClose={() => setShowSummaryModal(null)} />
      )}

      <style>{`@media (min-width: 768px) { .md\\:hidden { display: none !important; } }`}</style>
    </div>
  );
}
