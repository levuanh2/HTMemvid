import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import MindMapModal from "./MindMapModal";
import SummaryModal from "./SummaryModal";
import { apiFetch, generateMindmap, cancelMindmap } from "../../utils/api";
import { Icon } from "../ui/Icon";
import Spinner from "../ui/Spinner";
import { normStem } from "../../utils/evidence";

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
  const [mindmapJobHint, setMindmapJobHint] = useState({ progress: null, current_node: null });
  const [loading, setLoading]             = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [summaries, setSummaries]         = useState([]);
  // Task 16 — skeleton preview (renders while the job is still running) + real
  // cancel notice (BE job actually aborted, not just "FE stopped polling").
  const [mindmapGenerating, setMindmapGenerating] = useState(false);
  const [mindmapCancelNotice, setMindmapCancelNotice] = useState(false);

  const frameRefs = useRef(new Map());

  // ── Polling refs (guard duplicate + cleanup) ──────
  const pollingJobIdRef = useRef(null);
  const pollingIntervalRef = useRef(null);
  const pollingTimeoutRef = useRef(null);
  const pollingGenerationRef = useRef(0);
  const cancelMindmapRef = useRef(null);
  const currentMindmapJobIdRef = useRef(null);

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

  // ── Poll mindmap job (adaptive interval, deduped, auto-cleanup) ────
  const stopPolling = useCallback((reason = "manual") => {
    if (pollingIntervalRef.current != null) { clearInterval(pollingIntervalRef.current); pollingIntervalRef.current = null; }
    if (pollingTimeoutRef.current != null) { clearTimeout(pollingTimeoutRef.current); pollingTimeoutRef.current = null; }
    const prevId = pollingJobIdRef.current;
    if (prevId) console.log(`[MindMap Poll Stop] job_id=${prevId} reason=${reason}`);
    pollingJobIdRef.current = null;
  }, []);

  const startPolling = useCallback((jobId, opts = {}) => {
    const { onDone, onError, onTick, jobTimeoutMs = 180 * 1000, maxExtraMs = 10 * 1000 } = opts;

    if (pollingJobIdRef.current === jobId) {
      console.log(`[MindMap Poll] job_id=${jobId} already polling, skip duplicate start`);
      return;
    }
    if (pollingJobIdRef.current) stopPolling("new_job");

    pollingJobIdRef.current = jobId;
    const generation = ++pollingGenerationRef.current;
    const startTs = Date.now();
    const maxElapsedMs = jobTimeoutMs + maxExtraMs;

    const computeInterval = (elapsedMs) => {
      if (elapsedMs < 30 * 1000) return 2000;
      if (elapsedMs < 120 * 1000) return 5000;
      return 5000;
    };

    let inFlight = false;

    const tick = async () => {
      if (pollingGenerationRef.current !== generation) return;
      if (pollingJobIdRef.current !== jobId) return;
      if (inFlight) return;

      const elapsed = Date.now() - startTs;
      if (elapsed > maxElapsedMs) {
        console.log(`[MindMap Poll Stop] job_id=${jobId} reason=timeout elapsed=${Math.round(elapsed / 1000)}s`);
        stopPolling("timeout");
        if (typeof onError === "function") onError(new Error("Quá thời gian chờ tạo Sơ đồ (frontend timeout)."));
        return;
      }

      inFlight = true;
      try {
        const res = await apiFetch(`/mindmap-status/${encodeURIComponent(jobId)}`, { method: "GET", headers: { "Content-Type": "application/json" } });
        if (pollingGenerationRef.current !== generation) return;
        if (pollingJobIdRef.current !== jobId) return;
        if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `HTTP ${res.status}`); }
        const data = await res.json();
        if (typeof onTick === "function") onTick(data);
        if (data.status === "done") { stopPolling("done"); if (typeof onDone === "function") onDone(data.result); return; }
        if (data.status === "error" || data.status === "timeout") {
          stopPolling(data.status);
          if (typeof onError === "function") onError(new Error(data.error || "Lỗi khi tạo Sơ đồ."));
          return;
        }
      } catch (err) {
        if (pollingGenerationRef.current !== generation) return;
        if (pollingJobIdRef.current !== jobId) return;
        console.error(`[MindMap Poll] job_id=${jobId} error:`, err);
      } finally {
        inFlight = false;
      }
    };

    tick();
    const schedule = () => {
      if (pollingGenerationRef.current !== generation) return;
      if (pollingJobIdRef.current !== jobId) return;
      const elapsed = Date.now() - startTs;
      const interval = computeInterval(elapsed);
      pollingIntervalRef.current = setTimeout(schedule, interval);
    };
    pollingIntervalRef.current = setTimeout(schedule, 2000);
  }, [stopPolling]);

  useEffect(() => () => stopPolling("unmount"), [stopPolling]);

  // ── Handlers (logic unchanged for the plain-generate path) ─
  // Shared by "Tạo sơ đồ" (force=false, uses BE content-hash cache) and the
  // mindmap viewer's degraded-banner "Tạo lại" (force=true, bypasses cache).
  const runMindmapGeneration = async (sourceList, { force = false } = {}) => {
    if (!sourceList?.length) { alert("Vui lòng chọn ít nhất một tài liệu để tạo Sơ đồ!"); return; }
    setLoading(true);
    setMindmapCancelNotice(false);
    setMindmapJobHint({ progress: null, current_node: null });
    try {
      const startData = await generateMindmap(sourceList, { force });
      if (startData.error) throw new Error(startData.error);

      let data;
      if (startData.status === "done" && startData.result) {
        // Cache-hit (content_hash match, force=false): BE returns the record
        // straight away with no job_id — skip polling entirely instead of
        // throwing "Server không trả job_id." (known issue, fixed here).
        data = startData.result;
      } else {
        if (!startData.job_id) throw new Error("Server không trả job_id.");
        currentMindmapJobIdRef.current = startData.job_id;
        data = await new Promise((resolve, reject) => {
          cancelMindmapRef.current = () => reject(new Error("__cancelled__"));
          startPolling(startData.job_id, {
            jobTimeoutMs: (startData.jobTimeout || 180) * 1000,
            onTick: (j) => {
              setMindmapJobHint({ progress: j.progress ?? null, current_node: j.current_node ?? null });
              // Skeleton preview: as soon as the Skeleton node's partial (title
              // + nodes, no relations yet) is available, open the viewer with
              // it right away instead of waiting for enrich/relations to finish.
              if (j.partial?.nodes?.length) {
                setMindmapGenerating(true);
                setShowModalMap({
                  ...j.partial,
                  schema_version: 2,
                  id: "preview",
                  sources: sourceList,
                  initialLayoutType: "napkin",
                });
              }
            },
            onDone: (result) => resolve(result),
            onError: (err) => reject(err),
          });
        });
      }
      const hasNodes = (Array.isArray(data?.nodes) && data.nodes.length > 0) ||
        (Array.isArray(data?.diagram?.nodes) && data.diagram.nodes.length > 0);
      if (!hasNodes) {
        alert("Không tạo được sơ đồ từ tài liệu đã chọn (nội dung quá ngắn hoặc không trích được ý chính). Thử chọn tài liệu khác.");
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
        createdAt: data.createdAt || new Date().toISOString(),
        strategy: data.strategy || "iterative",
        initialLayoutType: "napkin",
      };
      setMindMaps((prev) => [record, ...prev.filter((item) => item.id !== record.id)]);
      await fetchMindMaps();
      setShowModalMap(record);
    } catch (err) {
      if (err?.message === "__cancelled__") { console.log("[MindMap] cancelled by user"); }
      else { console.error("Mind Map Error:", err); alert(err?.message ? `Không tạo được sơ đồ: ${err.message}` : "Không tạo được sơ đồ, kiểm tra console!"); }
    }
    finally {
      cancelMindmapRef.current = null;
      currentMindmapJobIdRef.current = null;
      setMindmapGenerating(false);
      setLoading(false);
      setMindmapJobHint({ progress: null, current_node: null });
      stopPolling("done");
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

  // Real cancel (Task 16): stop FE polling immediately (as before) AND tell
  // the BE to actually abort the job (cooperative-abort flag the worker checks
  // between graph nodes) — fire-and-forget, we don't block the UI on it.
  const handleCancelMindMap = () => {
    const jobId = currentMindmapJobIdRef.current;
    stopPolling("cancel");
    if (cancelMindmapRef.current) cancelMindmapRef.current();
    if (jobId) {
      cancelMindmap(jobId).catch((err) => console.error("[MindMap] cancel request failed:", err));
    }
    // Only dismiss the overlay if it's showing the transient skeleton preview
    // (fresh "Tạo"). A "Tạo lại" cancel leaves the still-valid previous map
    // on screen — just drops the generating banner (mindmapGenerating below).
    setShowModalMap((prev) => (prev?.id === "preview" ? null : prev));
    setMindmapGenerating(false);
    setMindmapCancelNotice(true);
  };

  // "Hỏi về đoạn này" (EvidenceDrawer) → close the mindmap overlay so the chat
  // underneath is visible again, then hand the snippet up to MainLayout to
  // prefill + focus the composer.
  const handleAskAbout = useCallback((snippet) => {
    setShowModalMap(null);
    onAskAbout?.(snippet);
  }, [onAskAbout]);

  const handleDeleteMap = async (id) => {
    if (!window.confirm("Xóa sơ đồ này?")) return;
    try {
      const res = await apiFetch(`/mindmaps/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMindMaps((prev) => prev.filter((m) => m.id !== id));
      await fetchMindMaps();
    } catch (err) { console.error("Mind Map delete error:", err); alert("Không xóa được sơ đồ!"); }
  };

  const handleGenerateSummary = async () => {
    if (!selectedSources?.length) { alert("Vui lòng chọn ít nhất một tài liệu để tóm tắt!"); return; }
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

  // ── Derived ───────────────────────────────────────
  const chunks = Array.isArray(evidence?.chunks) ? evidence.chunks : [];
  const sourceStems = Array.isArray(evidence?.sources) ? evidence.sources : [];
  const isGenerating = artifactTab === "mindmap" ? loading : summaryLoading;
  const onGenerate = artifactTab === "mindmap" ? handleGenerateMindMap : handleGenerateSummary;

  // MindMapModal forwards `data` to MindmapView verbatim, so this is how
  // generating/progress/cancel/ask-about reach it without widening that
  // shell's prop list — applies to every open map (fresh, regenerated, or
  // reopened from the saved list), not just the live-generation preview.
  const modalMapData = useMemo(() => (showModalMap ? {
    ...showModalMap,
    generating: mindmapGenerating,
    progress: mindmapJobHint.progress,
    onCancel: handleCancelMindMap,
    onAskAbout: handleAskAbout,
  } : null), [showModalMap, mindmapGenerating, mindmapJobHint.progress, handleAskAbout]);

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
                      <p className="font-reading text-[13px] leading-[1.55] text-text-secondary line-clamp-4">{c.snippet}</p>
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

        {/* Mindmap generation progress + cancel. Once the skeleton preview
            opens (full-screen overlay), this row sits behind it — the same
            Huỷ action is reachable from the banner MindmapView renders while
            `generating`, wired to the same handleCancelMindMap. */}
        {artifactTab === "mindmap" && loading && (
          <div className="mt-2 flex items-center gap-2 text-[12px] text-text-secondary">
            <span className="font-mono tabular-nums">{mindmapJobHint.progress != null ? `${mindmapJobHint.progress}%` : "…"}</span>
            {mindmapJobHint.current_node && <span className="text-text-muted truncate flex-1">{mindmapJobHint.current_node}</span>}
            <button onClick={handleCancelMindMap} className="ml-auto px-2 py-0.5 rounded-[5px] border border-border text-[11px] text-text-muted hover:text-[var(--err)] hover:border-[var(--err)]/40 transition-colors">
              Huỷ
            </button>
          </div>
        )}
        {artifactTab === "mindmap" && !loading && mindmapCancelNotice && (
          <div className="mt-2 flex items-center gap-1.5 text-[12px] text-text-muted">
            <Icon name="Ban" size={12} /> Đã huỷ tạo sơ đồ.
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
                    meta={`${map.sources?.length || 0} tài liệu · ${formatTimeAgo(map.createdAt)}`}
                    onOpen={() => setShowModalMap(map)} onDelete={() => handleDeleteMap(map.id)} deleteLabel="Xóa sơ đồ" />
                ))}
              </div>
            )
          ) : (
            summaries.length === 0 ? (
              <p className="text-[12px] text-text-muted text-center py-4">Chưa có tóm tắt nào được lưu.</p>
            ) : (
              <div className="flex flex-col gap-1.5">
                {summaries.map((item) => {
                  const summaryId = item.id || item?.data?.id;
                  return (
                    <ListCard key={item.id} icon="ScrollText" title={item.title || "Tóm tắt"}
                      meta={formatTimeAgo(item.createdAt)}
                      onOpen={() => setShowSummaryModal(item.data || item)} onDelete={() => handleDeleteSummary(summaryId)} deleteLabel="Xóa tóm tắt" />
                  );
                })}
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
          regenerating={loading}
        />
      )}
      {showSummaryModal && (
        <SummaryModal data={showSummaryModal} onClose={() => setShowSummaryModal(null)} onSave={handleSaveSummary} />
      )}

      <style>{`@media (min-width: 768px) { .md\\:hidden { display: none !important; } }`}</style>
    </div>
  );
}
