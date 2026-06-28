import { useState, useEffect, useCallback, useRef } from "react";
import { FiTrash2, FiClock, FiMaximize2 } from "react-icons/fi";
import MindMapModal from "./MindMapModal";
import SummaryModal from "./SummaryModal";
import { apiFetch } from "../../utils/api";

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

// ── Spinner ──────────────────────────────────────────
const Spinner = ({ className = "" }) => (
  <span className={`w-3.5 h-3.5 flex-shrink-0 border-2 border-current/20 border-t-current rounded-full inline-block animate-spin ${className}`} />
);

// ── Empty placeholder ─────────────────────────────────
const EmptyPlaceholder = ({ icon, text }) => (
  <div className="text-center py-8 px-4 text-text-muted">
    <div className="text-3xl mb-2">{icon}</div>
    <p className="text-[13px] leading-5 m-0 text-text-secondary">{text}</p>
  </div>
);

// ── List card item ────────────────────────────────────
const ListCard = ({ title, meta, onOpen, onDelete, deleteLabel = "Xóa" }) => (
  <div
    onClick={onOpen}
    className="flex items-center gap-3 px-3 py-2.5 rounded-[10px] border border-border hover:border-brand/30 hover:shadow-card-hover cursor-pointer transition-all group transition-theme"
    style={{ background: 'var(--bg-card)', boxShadow: 'var(--shadow-card)' }}
  >
    <div className="w-8 h-8 rounded-[8px] bg-brand/8 flex items-center justify-center flex-shrink-0 text-[16px]">
      📋
    </div>
    <div className="flex-1 min-w-0">
      <div className="text-[13px] font-semibold text-text-primary truncate">{title}</div>
      <div className="text-[11px] text-text-muted flex items-center gap-1 mt-0.5">
        <FiClock size={10} />{meta}
      </div>
    </div>
    <button
      onClick={(e) => { e.stopPropagation(); onDelete(); }}
      title={deleteLabel}
      className="w-7 h-7 rounded-[7px] inline-flex items-center justify-center text-text-muted opacity-0 group-hover:opacity-100 hover:text-red-500 hover:bg-red-50 transition-all"
    >
      <FiTrash2 size={13} />
    </button>
  </div>
);

// ── Mini MindMap Preview (static SVG) ────────────────
const MiniMindMapPreview = ({ mindmap, onOpen }) => {
  if (!mindmap || !mindmap.nodes?.length) return null;

  const rootNode = mindmap.nodes.find((n) => n.parent === null);
  const branches = mindmap.nodes.filter((n) => n.parent === rootNode?.id).slice(0, 6);

  return (
    <div
      onClick={onOpen}
      className="relative w-full rounded-[12px] border border-border bg-surface-elevated overflow-hidden cursor-pointer hover:border-brand/30 transition-all group"
      style={{ height: 160, boxShadow: "inset 0 1px 3px rgba(0,0,0,0.04)" }}
    >
      <svg width="100%" height="100%" viewBox="0 0 280 160" className="select-none">
        {/* Center node */}
        <ellipse cx="140" cy="80" rx="38" ry="20" fill="#4f46e5" />
        <text x="140" y="85" textAnchor="middle" fill="white" fontSize="11" fontWeight="700" fontFamily="DM Sans, sans-serif">
          {(rootNode?.title || "Root").slice(0, 12)}
        </text>

        {/* Branch nodes */}
        {branches.map((node, i) => {
          const angle = (i * Math.PI * 2) / branches.length - Math.PI / 2;
          const r = 68;
          const x = 140 + Math.cos(angle) * r;
          const y = 80 + Math.sin(angle) * r;
          const colors = ["#6366f1", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b", "#ef4444"];
          const color = colors[i % colors.length];
          return (
            <g key={node.id}>
              <line x1="140" y1="80" x2={x} y2={y} stroke={color} strokeWidth="1.5" strokeOpacity="0.5" />
              <ellipse cx={x} cy={y} rx="28" ry="14" fill={color} fillOpacity="0.15" stroke={color} strokeWidth="1.2" strokeOpacity="0.6" />
              <text x={x} y={y + 4} textAnchor="middle" fill={color} fontSize="9" fontFamily="DM Sans, sans-serif" fontWeight="600">
                {(node.title || "").slice(0, 10)}
              </text>
            </g>
          );
        })}
      </svg>

      {/* Hover overlay */}
      <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity bg-white/60 backdrop-blur-sm rounded-[12px]">
        <span className="text-[13px] font-semibold text-brand flex items-center gap-1.5">
          <FiMaximize2 size={14} /> Mở rộng
        </span>
      </div>
    </div>
  );
};

// ── Tabs config ───────────────────────────────────────
const TABS = [
  { key: "mindmap", label: "Sơ đồ" },
  { key: "summary", label: "Tóm tắt" },
  { key: "history", label: "Lịch sử" },
];

// ── Main component ────────────────────────────────────
export default function SidebarRight({ selectedSources, onClose }) {
  const [activeTab, setActiveTab] = useState("mindmap");
  const [mindMaps, setMindMaps]           = useState([]);
  const [showModalMap, setShowModalMap]   = useState(null);
  const [showSummaryModal, setShowSummaryModal] = useState(null);
  const [mindmapJobHint, setMindmapJobHint] = useState({ progress: null, current_node: null });
  const [loading, setLoading]             = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [mindmapMode, setMindmapMode] = useState("mindmap"); // Kept for compatibility but not shown in UI
  const [summaries, setSummaries]         = useState([]);

  // ── Polling refs (guard duplicate + cleanup) ──────
  // Active job id being polled, null if no active polling
  const pollingJobIdRef = useRef(null);
  // Current interval handle
  const pollingIntervalRef = useRef(null);
  // Current timeout handle (for auto-stop after jobTimeout+10s)
  const pollingTimeoutRef = useRef(null);
  // Generation counter to discard late responses after user started a new job
  const pollingGenerationRef = useRef(0);

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

  // ── Poll mindmap job (adaptive interval, deduped, auto-cleanup) ────
  // Adaptive interval:
  //   - 0-30s   elapsed: 2s/req
  //   - 30-120s elapsed: 5s/req
  //   - > maxElapsed (jobTimeout+10s or 120s): stop, treat as timeout
  // Stops automatically on: done, error, timeout, new job, unmount.
  // Only 1 polling loop active at a time (per jobId).
  const stopPolling = useCallback((reason = "manual") => {
    if (pollingIntervalRef.current != null) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
    if (pollingTimeoutRef.current != null) {
      clearTimeout(pollingTimeoutRef.current);
      pollingTimeoutRef.current = null;
    }
    const prevId = pollingJobIdRef.current;
    if (prevId) {
      console.log(`[MindMap Poll Stop] job_id=${prevId} reason=${reason}`);
    }
    pollingJobIdRef.current = null;
  }, []);

  const startPolling = useCallback((jobId, opts = {}) => {
    const {
      onDone,
      onError,
      onTick,
      jobTimeoutMs = 180 * 1000, // matches backend JOB_TIMEOUT_BALANCED
      maxExtraMs = 10 * 1000,    // +10s buffer
    } = opts;

    // GUARD 1: If a polling loop is already running for THIS job, do nothing.
    if (pollingJobIdRef.current === jobId) {
      console.log(`[MindMap Poll] job_id=${jobId} already polling, skip duplicate start`);
      return;
    }

    // GUARD 2: If a polling loop is running for ANOTHER job, stop it first.
    if (pollingJobIdRef.current) {
      stopPolling("new_job");
    }

    pollingJobIdRef.current = jobId;
    // Bump generation so any in-flight request becomes stale.
    const generation = ++pollingGenerationRef.current;
    const startTs = Date.now();
    const maxElapsedMs = jobTimeoutMs + maxExtraMs;

    // Compute current interval based on elapsed
    const computeInterval = (elapsedMs) => {
      if (elapsedMs < 30 * 1000) return 2000;
      if (elapsedMs < 120 * 1000) return 5000;
      return 5000; // > 120s, still poll every 5s until maxElapsedMs hit
    };

    let inFlight = false; // prevent overlapping requests

    const tick = async () => {
      // Bail out if a newer polling cycle started or polling was stopped
      if (pollingGenerationRef.current !== generation) return;
      if (pollingJobIdRef.current !== jobId) return;
      if (inFlight) return; // skip overlap

      const elapsed = Date.now() - startTs;

      // Hard cap: stop polling after jobTimeout + 10s
      if (elapsed > maxElapsedMs) {
        console.log(`[MindMap Poll Stop] job_id=${jobId} reason=timeout elapsed=${Math.round(elapsed / 1000)}s`);
        stopPolling("timeout");
        if (typeof onError === "function") {
          onError(new Error("Quá thời gian chờ tạo Mind Map (frontend timeout)."));
        }
        return;
      }

      inFlight = true;
      try {
        const res = await apiFetch(`/mindmap-status/${encodeURIComponent(jobId)}`, {
          method: "GET",
          headers: { "Content-Type": "application/json" },
        });
        // Discard late responses from old generations
        if (pollingGenerationRef.current !== generation) return;
        if (pollingJobIdRef.current !== jobId) return;

        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          throw new Error(d.error || `HTTP ${res.status}`);
        }
        const data = await res.json();
        const interval = computeInterval(Date.now() - startTs);
        console.log(`[MindMap Poll] job_id=${jobId} interval=${interval} elapsed=${Math.round((Date.now() - startTs) / 1000)}s status=${data.status}`);
        if (typeof onTick === "function") onTick(data);

        if (data.status === "done") {
          stopPolling("done");
          if (typeof onDone === "function") onDone(data.result);
          return;
        }
        if (data.status === "error" || data.status === "timeout") {
          stopPolling(data.status);
          if (typeof onError === "function") {
            onError(new Error(data.error || "Lỗi khi tạo Mind Map."));
          }
          return;
        }
      } catch (err) {
        if (pollingGenerationRef.current !== generation) return;
        if (pollingJobIdRef.current !== jobId) return;
        console.error(`[MindMap Poll] job_id=${jobId} error:`, err);
        // Don't stop on network error - retry next tick.
      } finally {
        inFlight = false;
      }
    };

    // First tick immediately
    tick();
    // Then schedule on adaptive interval
    const schedule = () => {
      if (pollingGenerationRef.current !== generation) return;
      if (pollingJobIdRef.current !== jobId) return;
      const elapsed = Date.now() - startTs;
      const interval = computeInterval(elapsed);
      pollingIntervalRef.current = setTimeout(schedule, interval);
    };
    pollingIntervalRef.current = setTimeout(schedule, 2000);
  }, [stopPolling]);

  // Cleanup on unmount
  useEffect(() => {
    return () => stopPolling("unmount");
  }, [stopPolling]);

  // ── Handlers (logic unchanged) ────────────────────
  const handleGenerateMindMap = async () => {
    if (!selectedSources?.length) { alert("Vui lòng chọn ít nhất một file để tạo Mind Map!"); return; }
    setLoading(true);
    setMindmapJobHint({ progress: null, current_node: null });
    try {
      const res = await apiFetch(`/generate-mindmap`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sources: selectedSources, q: "tóm tắt tài liệu" }) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const startData = await res.json();
      if (startData.error) throw new Error(startData.error);
      if (!startData.job_id) throw new Error("Server không trả job_id.");

      // Use adaptive interval-based polling (deduped, auto-cleanup)
      const data = await new Promise((resolve, reject) => {
        startPolling(startData.job_id, {
          jobTimeoutMs: (startData.jobTimeout || 180) * 1000,
          onTick: (j) => setMindmapJobHint({ progress: j.progress ?? null, current_node: j.current_node ?? null }),
          onDone: (result) => resolve(result),
          onError: (err) => reject(err),
        });
      });
      if (import.meta.env.DEV) {
        console.log("[SidebarRight] mindmap result", data);
        console.log("[SidebarRight] nodes", data?.nodes?.length, "diagram", data?.diagram?.nodes?.length);
      }
      // Build record with diagram
      const record = {
        id: data.id || Date.now().toString(),
        title: data.title || "Sơ đồ tư duy",
        nodes: Array.isArray(data.nodes) ? data.nodes : [],
        diagram: data.diagram || null,
        sources: Array.isArray(data.sources) ? data.sources : selectedSources,
        createdAt: data.createdAt || new Date().toISOString(),
        strategy: data.strategy || "iterative",
        initialLayoutType: "napkin",
      };
      setMindMaps((prev) => [record, ...prev.filter((item) => item.id !== record.id)]);
      await fetchMindMaps();
    } catch (err) { console.error("Mind Map Error:", err); alert("Không tạo được Mind Map, kiểm tra console!"); }
    finally {
      setLoading(false);
      setMindmapJobHint({ progress: null, current_node: null });
      stopPolling("done");
    }
  };

  const handleDeleteMap = async (id) => {
    if (!window.confirm("Xóa mind map này?")) return;
    try {
      const res = await apiFetch(`/mindmaps/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMindMaps((prev) => prev.filter((m) => m.id !== id));
      await fetchMindMaps();
    } catch (err) { console.error("Mind Map delete error:", err); alert("Không xóa được mind map!"); }
  };

  const handleGenerateSummary = async () => {
    if (!selectedSources?.length) { alert("Vui lòng chọn ít nhất một file để tóm tắt!"); return; }
    setSummaryLoading(true);
    try {
      const res = await apiFetch(`/summarize-documents`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sources: selectedSources, use_dancer: true, use_entity_chain: true, use_cod: true, use_structured: true, use_fact_check: true }) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setShowSummaryModal({ ...data, sources: selectedSources });
    } catch (err) { console.error("Summary Error:", err); alert("Không tạo được tóm tắt, kiểm tra console!"); }
    finally { setSummaryLoading(false); }
  };

  const handleSaveSummary = async (payload) => {
    try {
      const res = await apiFetch(`/summaries`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await fetchSummaries();
    } catch (err) { console.error("Save summary error:", err); alert("Không lưu được tóm tắt!"); }
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

  // ── Render ────────────────────────────────────────
  return (
    <div className="flex flex-col h-full overflow-hidden transition-theme" style={{ background: 'var(--bg-sidebar)' }}>

      {/* ── TAB BAR ── */}
      <div className="flex border-b border-border px-1 flex-shrink-0">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`sidebar-tab ${activeTab === tab.key ? "sidebar-tab-active" : ""}`}
          >
            {tab.label}
          </button>
        ))}
        {/* Mobile close */}
        <button
          onClick={onClose}
          className="md:hidden ml-auto w-8 h-8 my-1 mr-1 rounded-[8px] inline-flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-surface-elevated transition-colors"
          aria-label="Đóng"
        >
          ✕
        </button>
      </div>

      {/* ── TAB CONTENT ── */}
      <div className="flex-1 min-h-0 overflow-y-auto">

        {/* ── MINDMAP TAB ── */}
        {activeTab === "mindmap" && (
          <div className="flex flex-col h-full">
            {/* Mindmap preview area */}
            <div className="p-3 flex-shrink-0">
              {initialLoading ? (
                <div className="flex items-center justify-center h-[160px] text-text-muted text-[13px] gap-2">
                  <Spinner /> Đang tải…
                </div>
              ) : mindMaps.length > 0 ? (
                <MiniMindMapPreview mindmap={mindMaps[0]} onOpen={() => setShowModalMap(mindMaps[0])} />
              ) : (
                <div className="h-[160px] rounded-[12px] border border-dashed border-border bg-surface-elevated flex items-center justify-center">
                  <div className="text-center">
                    <div className="text-3xl mb-1">🧠</div>
                    <p className="text-[12px] text-text-muted">Chưa có sơ đồ tư duy</p>
                  </div>
                </div>
              )}

              {/* Loading progress */}
              {loading && (
                <div className="mt-2 flex items-center gap-2 text-[12px] text-text-secondary">
                  <Spinner className="text-brand" />
                  <span className="font-medium">{mindmapJobHint.progress != null ? `${mindmapJobHint.progress}%` : "Đang tạo…"}</span>
                  {mindmapJobHint.current_node && (
                    <span className="text-text-muted truncate">{mindmapJobHint.current_node}</span>
                  )}
                </div>
              )}
            </div>

            {/* Action buttons */}
            <div className="px-3 pb-3 flex flex-col gap-2 flex-shrink-0">
              <button
                onClick={handleGenerateMindMap}
                disabled={loading || !selectedSources?.length}
                className="w-full h-10 rounded-[10px] border border-border text-[13px] font-semibold text-text-primary inline-flex items-center justify-center gap-2 hover:border-brand/30 hover:text-brand transition-all disabled:opacity-50 disabled:cursor-not-allowed transition-theme"
                style={{ background: 'var(--bg-card)', boxShadow: 'var(--shadow-card)' }}
              >
                {loading ? <><Spinner /> Đang tạo…</> : <><span>🧠</span> Tạo sơ đồ tư duy</>}
              </button>
              {mindMaps.length > 0 && (
                <button
                  onClick={() => setShowModalMap(mindMaps[0])}
                  className="w-full h-10 rounded-[10px] border border-border text-[13px] font-semibold text-text-secondary inline-flex items-center justify-center gap-2 hover:border-brand/30 hover:text-brand transition-all transition-theme"
                  style={{ background: 'var(--bg-card)', boxShadow: 'var(--shadow-card)' }}
                >
                  <FiMaximize2 size={14} /> Mở rộng toàn màn hình
                </button>
              )}
            </div>

            {/* Mind map list */}
            {mindMaps.length > 0 && (
              <div className="px-3 pb-3 border-t border-border pt-3 flex-1 overflow-y-auto">
                <div className="text-[11px] font-bold tracking-wider uppercase text-text-muted mb-2">
                  Đã lưu ({mindMaps.length})
                </div>
                <div className="flex flex-col gap-1.5">
                  {mindMaps.map((map) => (
                    <ListCard
                      key={map.id}
                      title={map.title}
                      meta={`${map.sources?.length || 0} tài liệu · ${formatTimeAgo(map.createdAt)}`}
                      onOpen={() => setShowModalMap(map)}
                      onDelete={() => handleDeleteMap(map.id)}
                      deleteLabel="Xóa sơ đồ"
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── SUMMARY TAB ── */}
        {activeTab === "summary" && (
          <div className="flex flex-col h-full">
            {/* Generate button */}
            <div className="p-3 flex-shrink-0 border-b border-border">
              <button
                onClick={handleGenerateSummary}
                disabled={summaryLoading || !selectedSources?.length}
                className="w-full h-10 rounded-[10px] text-[13px] font-semibold text-white inline-flex items-center justify-center gap-2 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ background: "linear-gradient(135deg, #4f46e5, #6366f1)", boxShadow: "0 2px 8px rgba(79,70,229,0.25)" }}
              >
                {summaryLoading ? <><Spinner /> Đang tạo…</> : <><span>📄</span> + Tạo tóm tắt mới</>}
              </button>
            </div>

            {/* Summary list */}
            <div className="flex-1 overflow-y-auto px-3 py-3">
              {summaries.length === 0 ? (
                <EmptyPlaceholder icon="📝" text="Chưa có tóm tắt nào được lưu." />
              ) : (
                <div className="flex flex-col gap-1.5">
                  {summaries.map((item) => {
                    const summaryId = item.id || item?.data?.id;
                    return (
                      <ListCard
                        key={item.id}
                        title={item.title || "Tóm tắt"}
                        meta={formatTimeAgo(item.createdAt)}
                        onOpen={() => setShowSummaryModal(item.data || item)}
                        onDelete={() => handleDeleteSummary(summaryId)}
                        deleteLabel="Xóa tóm tắt"
                      />
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── HISTORY TAB ── */}
        {activeTab === "history" && (
          <EmptyPlaceholder icon="🕐" text={"Lịch sử trò chuyện\nsẽ xuất hiện ở đây."} />
        )}
      </div>

      {/* ── MODALS ── */}
      {showModalMap && <MindMapModal data={showModalMap} initialLayoutType={showModalMap.initialLayoutType || "napkin"} onClose={() => setShowModalMap(null)} />}
      {showSummaryModal && (
        <SummaryModal data={showSummaryModal} onClose={() => setShowSummaryModal(null)} onSave={handleSaveSummary} />
      )}

      <style>{`
        .bg-brand\\/4 { background: rgba(79,70,229,0.04); }
        .bg-brand\\/8 { background: rgba(79,70,229,0.08); }
        @media (min-width: 768px) { .md\\:hidden { display: none !important; } }
      `}</style>
    </div>
  );
}