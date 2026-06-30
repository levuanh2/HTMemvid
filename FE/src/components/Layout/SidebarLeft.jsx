import { useState, useEffect, useRef } from "react";
import { apiFetch } from "../../utils/api";
import { Icon } from "../ui/Icon";
import Badge from "../ui/Badge";
import Spinner from "../ui/Spinner";

/** Phase sau FAISS: memory tree. memory_tree_ready = đã xong — không hiện « đang tối ưu ». */
const SUBSTATUS_OPTIMIZING = new Set(["faiss_ready", "building_memory_tree"]);

// ── Status config ──────────────────────────────────────
const getStatusConfig = (status, substatus, canQuery) => {
  if (canQuery === true) {
    return {
      mainText: "Sẵn sàng tra cứu",
      subText: substatus && SUBSTATUS_OPTIMIZING.has(substatus) ? "Đang hoàn thiện cây trí nhớ…" : null,
      showProgress: false,
      checkboxEnabled: true,
      badgeText: "Sẵn sàng",
      tone: "ready",
    };
  }
  switch (status) {
    case "processing":
      return { mainText: "Đang phân tích tài liệu…", showProgress: true, checkboxEnabled: false, badgeText: "Đang xử lý", tone: "processing" };
    case "index_ready":
      return { mainText: "Đang xử lý…", subText: "Đang tối ưu thêm nội dung", badgeText: "Đang xử lý", showProgress: true, checkboxEnabled: false, tone: "processing" };
    case "ready":
      return { mainText: "Đang xử lý…", badgeText: "Đang xử lý", showProgress: true, checkboxEnabled: false, tone: "processing" };
    case "error":
      return { mainText: "Lỗi xử lý tài liệu", showProgress: false, checkboxEnabled: false, badgeText: "Lỗi", tone: "error", showErrorIcon: true };
    default:
      return { mainText: "Không xác định", showProgress: false, checkboxEnabled: false, badgeText: null, tone: "ready" };
  }
};

const formatFileName = (name = "") => name.replace(/\.(mp4|avi|mov|mkv|webm|mp3|wav|pdf|txt|docx)$/i, "");

export default function SidebarLeft({ selectedSources, setSelectedSources, onSourcesChange, onClose }) {
  const [sources, setSources] = useState([]);
  const [menuOpen, setMenuOpen] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [deletingFile, setDeletingFile] = useState(null);
  const [query, setQuery] = useState("");
  const fileInputRef = useRef(null);
  const pollingIntervalsRef = useRef({});

  // ── Polling logic (unchanged) ──────────────────────
  const pollSourceStatus = (sourceId) => {
    if (pollingIntervalsRef.current[sourceId]) return;
    const poll = async () => {
      try {
        const res = await apiFetch(`/sources/${sourceId}/status`);
        if (!res.ok) { stopPolling(sourceId); return; }
        const data = await res.json();
        setSources((prev) => prev.map((s) => s.source_id === sourceId ? { ...s, status: data.status, progress: data.progress ?? s.progress, substatus: data.substatus, capabilities: data.capabilities, can_query: data.can_query === true, video_stem: data.video_stem ?? s.video_stem, error: data.error } : s));
        if (data.status === "ready" || data.status === "error") {
          stopPolling(sourceId);
          if (data.status === "ready") setTimeout(() => fetchSourcesFromBackend(), 500);
        }
      } catch (err) { console.error(`Error polling status for ${sourceId}:`, err); stopPolling(sourceId); }
    };
    poll();
    pollingIntervalsRef.current[sourceId] = setInterval(poll, 1500);
  };

  const stopPolling = (sourceId) => {
    if (pollingIntervalsRef.current[sourceId]) { clearInterval(pollingIntervalsRef.current[sourceId]); delete pollingIntervalsRef.current[sourceId]; }
  };

  const fetchSourcesFromBackend = () => {
    apiFetch(`/list-indexed`)
      .then((res) => res.json())
      .then((data) => {
        const backendSources = data.sources || [];
        const keyOf = (s) => s.video_stem || s.video;
        setSources((prev) => {
          const activeSources = prev.filter((s) => s.status === "processing" || s.status === "index_ready");
          const readySources = backendSources.map((s) => ({ source_id: null, filename: s.filename || formatFileName(keyOf(s)), video_stem: keyOf(s), status: "ready", progress: 1.0, substatus: null, capabilities: { chunk_query: true, memory_query: true }, can_query: true, num_chunks: s.num_chunks }));
          const combined = [...activeSources];
          readySources.forEach((rs) => { if (!combined.some((ps) => (ps.video_stem || ps.video) === rs.video_stem)) combined.push(rs); });
          return combined;
        });
        setSelectedSources((prev) => prev.filter((p) => backendSources.some((s) => keyOf(s) === p)));
      })
      .catch((err) => console.error("Error fetching sources:", err));
  };

  useEffect(() => { fetchSourcesFromBackend(); }, []);
  useEffect(() => { if (onSourcesChange) onSourcesChange(sources); }, [sources, onSourcesChange]);

  // ── Upload logic (unchanged) ───────────────────────
  const handleAddFiles = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    setUploading(true);
    try {
      for (const file of files) {
        const fd = new FormData();
        fd.append("file", file);
        const res = await apiFetch("/upload", { method: "POST", body: fd });
        if (!res.ok) { const d = await res.json().catch(() => ({})); console.error("Upload failed:", d); continue; }
        const data = await res.json();
        const newSource = { source_id: data.source_id, filename: formatFileName(file.name), video_stem: data.video_stem, status: data.status || "processing", progress: 0, can_query: false };
        setSources((prev) => [newSource, ...prev]);
        if (data.source_id) pollSourceStatus(data.source_id);
      }
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDeleteSource = async (src) => {
    const key = src.video_stem || src.video;
    if (!window.confirm(`Xóa "${src.filename}"?`)) return;
    setMenuOpen(null);
    setDeletingFile(key);
    try {
      let res;
      if (src.source_id) {
        res = await apiFetch(`/sources/${encodeURIComponent(src.source_id)}`, { method: "DELETE" });
      } else {
        res = await apiFetch(`/delete-source`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video: String(key || "") }) });
      }
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `HTTP ${res.status}`); }
      setSources((prev) => prev.filter((s) => (s.video_stem || s.video) !== key));
      setSelectedSources((prev) => prev.filter((p) => p !== key));
    } catch (err) { console.error("Delete source error:", err); alert("Không xóa được tài liệu, kiểm tra console!"); }
    finally { setDeletingFile(null); }
  };

  const toggleSelect = (src) => {
    const key = src.video_stem || src.video;
    setSelectedSources((prev) => prev.includes(key) ? prev.filter((p) => p !== key) : [...prev, key]);
  };

  const handleSelectAll = (checked) => {
    if (checked) setSelectedSources(sources.filter((s) => s.can_query).map((s) => s.video_stem || s.video));
    else setSelectedSources([]);
  };

  const readySources = sources.filter((s) => s.can_query);
  const allSelected = readySources.length > 0 && readySources.every((s) => selectedSources.includes(s.video_stem || s.video));

  // ── Client-side search filter ──────────────────────
  const q = query.trim().toLowerCase();
  const visibleSources = q
    ? sources.filter((s) => (s.filename || formatFileName(s.video || s.video_stem || "")).toLowerCase().includes(q))
    : sources;

  // ── Render ─────────────────────────────────────────
  return (
    <div className="flex flex-col h-full" style={{ background: "var(--bg-sidebar)" }}>

      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-border flex items-center justify-between flex-shrink-0">
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-text-primary flex items-center gap-1.5">
            <Icon name="Library" size={14} className="text-brand" /> Thư mục nguồn
          </div>
          <div className="text-[11px] text-text-muted mt-1 font-mono">
            {sources.length} tài liệu · <span className="text-brand">{selectedSources.length} đang chọn</span>
          </div>
        </div>
        <button onClick={onClose} className="md:hidden icon-btn w-8 h-8" aria-label="Đóng">
          <Icon name="X" size={16} />
        </button>
      </div>

      {/* Controls */}
      <div className="px-3 pt-3 pb-2 flex-shrink-0 flex flex-col gap-2">
        {/* Upload */}
        <label className={["select-none w-full inline-flex items-center justify-center gap-2", uploading ? "btn-secondary cursor-not-allowed opacity-70" : "btn-primary cursor-pointer"].join(" ")}>
          {uploading ? (<><Spinner size={15} /> Đang tải lên…</>) : (<><Icon name="Plus" size={16} strokeWidth={2} /> Thêm tài liệu</>)}
          <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleAddFiles} disabled={uploading} />
        </label>

        {/* Search — functional filter */}
        <div className="header-search !rounded-[7px] !min-w-0 !px-3 !py-2">
          <Icon name="Search" size={14} className="text-text-muted flex-shrink-0" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Lọc theo tên tài liệu…"
            aria-label="Lọc tài liệu"
            className="bg-transparent outline-none text-[13px] text-text-primary placeholder:text-text-muted w-full"
          />
          {query && (
            <button onClick={() => setQuery("")} className="text-text-muted hover:text-text-primary" aria-label="Xoá bộ lọc">
              <Icon name="X" size={13} />
            </button>
          )}
        </div>

        {/* Select all */}
        <label className="flex items-center gap-2 cursor-pointer px-1 py-0.5">
          <input type="checkbox" checked={allSelected} onChange={(e) => handleSelectAll(e.target.checked)} className="w-3.5 h-3.5 accent-brand cursor-pointer rounded" />
          <span className="text-[12px] text-text-secondary font-medium">Chọn tất cả tài liệu sẵn sàng</span>
        </label>
      </div>

      {/* Sources list */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 flex flex-col gap-1.5">
        {sources.length === 0 && (
          <div className="text-center py-12 px-4">
            <Icon name="FolderOpen" size={30} className="mx-auto mb-3 text-text-muted opacity-60" />
            <p className="text-[13px] font-semibold text-text-secondary">Chưa có tài liệu nào.</p>
            <p className="text-[12px] text-text-muted mt-1">Nhấn “Thêm tài liệu” để bắt đầu.</p>
          </div>
        )}
        {sources.length > 0 && visibleSources.length === 0 && (
          <div className="text-center py-10 px-4 text-[12px] text-text-muted">Không có tài liệu khớp “{query}”.</div>
        )}

        {visibleSources.map((src, idx) => {
          const displayName = src.filename || formatFileName(src.video || "");
          const isDeleting = deletingFile === (src.video_stem || src.video);
          const isSelected = selectedSources.includes(src.video_stem || src.video);
          const isSelectable = src?.can_query === true;
          const statusConfig = getStatusConfig(src.status, src.substatus, isSelectable);
          const checkboxEnabled = statusConfig.checkboxEnabled && isSelectable && !isDeleting;

          return (
            <div
              key={src.source_id || src.video_stem || idx}
              onClick={() => checkboxEnabled && toggleSelect(src)}
              className={[
                "rounded-[8px] border px-3 py-2.5 transition-all duration-150 transition-theme",
                checkboxEnabled ? "cursor-pointer" : "cursor-default",
                isSelected ? "border-brand/45 shadow-glow" : "border-border bg-surface-card hover:border-border-strong",
                isDeleting ? "opacity-50" : "",
              ].join(" ")}
              style={isSelected ? { background: "color-mix(in srgb, var(--accent) 6%, var(--bg-card))" } : undefined}
            >
              <div className="flex items-start gap-2.5">
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={(e) => { e.stopPropagation(); toggleSelect(src); }}
                  disabled={!checkboxEnabled}
                  className="mt-0.5 w-3.5 h-3.5 accent-brand flex-shrink-0 cursor-pointer"
                />
                <Icon name="FileText" size={14} className={`flex-shrink-0 mt-0.5 ${isSelected ? "text-brand" : "text-text-muted"}`} />

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[13px] font-semibold text-text-primary truncate flex-1" title={displayName}>{displayName}</span>
                    {statusConfig.showErrorIcon && <Icon name="AlertCircle" size={13} className="flex-shrink-0 text-[var(--err)]" />}
                  </div>

                  <div className="flex items-center gap-2 mt-1.5">
                    {isSelectable && src.num_chunks ? (
                      <span className="text-[11px] text-text-muted font-mono">{src.num_chunks} đoạn</span>
                    ) : null}
                    {statusConfig.badgeText && <Badge tone={statusConfig.tone}>{statusConfig.badgeText}</Badge>}
                  </div>

                  {statusConfig.subText && <div className="text-[11px] text-text-muted mt-1">{statusConfig.subText}</div>}

                  {statusConfig.showProgress && (
                    <div className="mt-1.5">
                      <div className="progress-track"><div className="progress-fill" style={{ width: `${(src.progress || 0) * 100}%` }} /></div>
                    </div>
                  )}

                  {src.status === "error" && (
                    <div className="text-[11px] mt-1.5 rounded-[6px] px-2 py-1 whitespace-pre-wrap break-words"
                      style={{ color: "var(--err)", background: "color-mix(in srgb, var(--err) 10%, transparent)", border: "1px solid color-mix(in srgb, var(--err) 25%, transparent)" }}>
                      {String(src.error ?? "").trim() || "Không có chi tiết lỗi — xem log backend."}
                    </div>
                  )}
                </div>

                <div className="relative flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                  {isDeleting ? (
                    <Spinner size={16} className="text-text-muted" />
                  ) : (
                    <>
                      <button onClick={() => setMenuOpen(menuOpen === idx ? null : idx)} className="w-7 h-7 rounded-[6px] inline-flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-surface-elevated transition-colors" aria-label="Tuỳ chọn">
                        <Icon name="MoreVertical" size={14} />
                      </button>
                      {menuOpen === idx && (
                        <div className="absolute right-0 top-8 z-20 min-w-[120px] border border-border rounded-[8px] overflow-hidden" style={{ background: "var(--bg-card)", boxShadow: "var(--shadow-card-hover)" }}>
                          <button onClick={() => handleDeleteSource(src)} className="w-full text-left px-3 py-2 text-[13px] font-semibold inline-flex items-center gap-2 hover:bg-surface-elevated transition-colors" style={{ color: "var(--err)" }}>
                            <Icon name="Trash2" size={13} /> Xóa
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <style>{`@media (min-width: 768px) { .md\\:hidden { display: none !important; } }`}</style>
    </div>
  );
}
