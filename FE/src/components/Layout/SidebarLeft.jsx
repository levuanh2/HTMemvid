import { useState, useEffect, useRef } from "react";
import { FiMoreVertical, FiAlertCircle, FiX, FiFile } from "react-icons/fi";
import { apiFetch } from "../../utils/api";

/** Phase sau FAISS: memory tree. memory_tree_ready = đã xong — không hiện « đang tối ưu ». */
const SUBSTATUS_OPTIMIZING = new Set(["faiss_ready", "building_memory_tree"]);

// ── Status config ──────────────────────────────────────
const getStatusConfig = (status, substatus, canQuery) => {
  if (canQuery === true) {
    return {
      mainText: "Sẵn sàng tra cứu",
      subText: substatus && SUBSTATUS_OPTIMIZING.has(substatus) ? "Đang hoàn thiện memory tree…" : null,
      showProgress: false,
      checkboxEnabled: true,
      badge: "READY",
      badgeClass: "badge-ready",
      borderClass: "border-emerald-300/60",
    };
  }
  switch (status) {
    case "processing":
      return {
        mainText: "Đang phân tích tài liệu…",
        showProgress: true,
        checkboxEnabled: false,
        badge: "PROCESSING",
        badgeClass: "badge-processing",
        borderClass: "border-amber-300/60",
      };
    case "index_ready":
      return {
        mainText: "Đang xử lý…",
        subText: "Đang tối ưu thêm nội dung",
        badge: "PROCESSING",
        showProgress: true,
        checkboxEnabled: false,
        badgeClass: "badge-processing",
        borderClass: "border-amber-300/60",
      };
    case "ready":
      return {
        mainText: "Đang xử lý…",
        badge: "PROCESSING",
        showProgress: true,
        checkboxEnabled: false,
        badgeClass: "badge-processing",
        borderClass: "border-amber-300/60",
      };
    case "error":
      return {
        mainText: "Lỗi xử lý tài liệu",
        showProgress: false,
        checkboxEnabled: false,
        badge: "ERROR",
        badgeClass: "badge-error",
        borderClass: "border-red-300/60",
        showErrorIcon: true,
      };
    default:
      return { mainText: "Không xác định", showProgress: false, checkboxEnabled: false, badge: null, badgeClass: "", borderClass: "border-border" };
  }
};

const formatFileName = (name = "") => name.replace(/\.(mp4|avi|mov|mkv|webm|mp3|wav|pdf|txt|docx)$/i, "");

export default function SidebarLeft({ selectedSources, setSelectedSources, onSourcesChange, onClose }) {
  const [sources, setSources] = useState([]);
  const [menuOpen, setMenuOpen] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [deletingFile, setDeletingFile] = useState(null);
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
        // Khóa canonical DÙNG CHUNG với BE: ưu tiên video_stem (BE trả canonical),
        // fallback video. filename hiển thị lấy từ BE nếu có.
        const keyOf = (s) => s.video_stem || s.video;
        setSources((prev) => {
          const activeSources = prev.filter((s) => s.status === "processing" || s.status === "index_ready");
          const readySources = backendSources.map((s) => ({ source_id: null, filename: s.filename || formatFileName(keyOf(s)), video_stem: keyOf(s), status: "ready", progress: 1.0, substatus: null, capabilities: { chunk_query: true, memory_query: true }, can_query: true, num_chunks: s.num_chunks }));
          const combined = [...activeSources];
          readySources.forEach((rs) => { if (!combined.some((ps) => (ps.video_stem || ps.video) === rs.video_stem)) combined.push(rs); });
          return combined;
        });
        // Giữ lựa chọn cũ nếu vẫn còn trong danh sách index (so theo khóa canonical).
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
        res = await apiFetch(`/delete-source`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ video: String(key || "") }),
        });
      }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.error || `HTTP ${res.status}`);
      }
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

  // ── Render ─────────────────────────────────────────
  return (
    <div className="flex flex-col h-full" style={{ background: 'var(--bg-sidebar)' }}>

      {/* Header */}
      <div className="px-4 pt-4 pb-3 border-b border-border flex items-center justify-between flex-shrink-0">
        <div className="min-w-0">
          <div className="text-[13px] font-bold text-text-primary">Tài liệu</div>
          <div className="text-[12px] text-text-muted mt-0.5">
            {sources.length} files · <span className="text-brand font-semibold">{selectedSources.length} đang chọn</span>
          </div>
        </div>
        <button onClick={onClose} className="md:hidden icon-btn w-8 h-8 border-0 shadow-none" aria-label="Đóng">
          <FiX size={17} />
        </button>
      </div>

      {/* Controls */}
      <div className="px-3 pt-3 pb-2 flex-shrink-0 flex flex-col gap-2">
        {/* Upload button */}
        <label
          className={[
            "select-none w-full",
            "inline-flex items-center justify-center gap-2",
            uploading ? "btn-secondary cursor-not-allowed opacity-70" : "btn-primary cursor-pointer",
          ].join(" ")}
        >
          {uploading ? (
            <>
              <span className="w-4 h-4 border-2 border-white/40 border-t-transparent rounded-full inline-block animate-spin" />
              Đang tải lên…
            </>
          ) : (
            <>
              <span className="text-lg leading-none font-light">+</span>
              <span>Thêm</span>
            </>
          )}
          <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleAddFiles} disabled={uploading} />
        </label>

        {/* Search input */}
        <div className="flex items-center gap-2 bg-surface-elevated border border-border rounded-[10px] px-3 py-2">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="w-3.5 h-3.5 text-text-muted flex-shrink-0">
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <span className="text-[12px] text-text-muted">Tìm kiếm tài liệu</span>
        </div>

        {/* Select all */}
        <label className="flex items-center gap-2 cursor-pointer px-1 py-0.5">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={(e) => handleSelectAll(e.target.checked)}
            className="w-3.5 h-3.5 accent-brand cursor-pointer rounded"
          />
          <span className="text-[12px] text-text-secondary font-medium">Chọn tất cả</span>
        </label>
      </div>

      {/* Sources list */}
      <div className="flex-1 overflow-y-auto px-3 pb-3 flex flex-col gap-1.5">
        {sources.length === 0 && (
          <div className="text-center py-10 px-4">
            <div className="text-3xl mb-2">📂</div>
            <p className="text-[13px] font-semibold text-text-secondary">Chưa có tài liệu nào.</p>
            <p className="text-[12px] text-text-muted mt-1">Nhấn "Thêm" để bắt đầu.</p>
          </div>
        )}

        {sources.map((src, idx) => {
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
                "rounded-[10px] border px-3 py-2.5 transition-all duration-150 transition-theme",
                checkboxEnabled ? "cursor-pointer hover:shadow-card-hover" : "cursor-default",
                isSelected
                  ? "border-brand/40 shadow-glow" : `border-border bg-surface-card hover:border-border-strong`,
                isDeleting ? "opacity-50" : "",
              ].join(" ")}
            >
              <div className="flex items-start gap-2.5">
                {/* Checkbox */}
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={(e) => { e.stopPropagation(); toggleSelect(src); }}
                  disabled={!checkboxEnabled}
                  className="mt-0.5 w-3.5 h-3.5 accent-brand flex-shrink-0 cursor-pointer"
                />

                {/* File icon */}
                <FiFile size={14} className={`flex-shrink-0 mt-0.5 ${isSelected ? "text-brand" : "text-text-muted"}`} />

                {/* Content */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[13px] font-semibold text-text-primary truncate flex-1" title={displayName}>
                      {displayName}
                    </span>
                    {statusConfig.showErrorIcon && <FiAlertCircle color="#ef4444" size={13} className="flex-shrink-0" />}
                  </div>

                  {/* Chunks count & badge */}
                  <div className="flex items-center gap-2 mt-1">
                    {isSelectable && src.num_chunks && (
                      <span className="text-[11px] text-text-muted">{src.num_chunks} chunks</span>
                    )}
                    {statusConfig.badge && (
                      <span className={statusConfig.badgeClass || ""}>{statusConfig.badge}</span>
                    )}
                  </div>

                  {statusConfig.subText && (
                    <div className="text-[11px] text-text-muted mt-0.5">{statusConfig.subText}</div>
                  )}

                  {/* Progress bar */}
                  {statusConfig.showProgress && (
                    <div className="mt-1.5">
                      <div className="progress-track">
                        <div className="progress-fill" style={{ width: `${(src.progress || 0) * 100}%` }} />
                      </div>
                    </div>
                  )}

                  {/* Error message */}
                  {src.status === "error" && (
                    <div className="text-[11px] text-red-500 mt-1.5 bg-red-50 border border-red-200 rounded-[6px] px-2 py-1 whitespace-pre-wrap break-words">
                      {String(src.error ?? "").trim() || "Không có chi tiết lỗi — xem log backend."}
                    </div>
                  )}
                </div>

                {/* Context menu */}
                <div className="relative flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                  {isDeleting ? (
                    <span className="w-4 h-4 border-2 border-text-muted/30 border-t-text-muted rounded-full inline-block animate-spin" />
                  ) : (
                    <>
                      <button
                        onClick={() => setMenuOpen(menuOpen === idx ? null : idx)}
                        className="w-7 h-7 rounded-[7px] inline-flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-surface-elevated transition-colors"
                      >
                        <FiMoreVertical size={14} />
                      </button>
                      {menuOpen === idx && (
                        <div className="absolute right-0 top-8 z-20 min-w-[100px] border border-border rounded-[10px] shadow-card-hover overflow-hidden" style={{ background: 'var(--bg-card)' }}>
                          <button
                            onClick={() => handleDeleteSource(src)}
                            className="w-full text-left px-3 py-2 text-[13px] font-semibold text-red-500 hover:bg-red-50 transition-colors"
                          >
                            🗑 Xóa
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
    </div>
  );
}