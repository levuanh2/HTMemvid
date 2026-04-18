import { useState, useEffect, useRef } from "react";
import { FiMoreVertical, FiAlertCircle, FiX } from "react-icons/fi";
import { apiFetch } from "../../utils/api";

// ── Status config ──────────────────────────────────────
const getStatusConfig = (status, substatus, canQuery) => {
  if (canQuery === true) {
    return {
      mainText: "Sẵn sàng tra cứu",
      subText: substatus ? "Đang tối ưu thêm…" : null,
      showProgress: false,
      checkboxEnabled: true,
      badge: "READY",
      badgeStyle: { background: "#052e16", color: "#4ade80", border: "1px solid #166534" },
      borderColor: "#166534",
      bgColor: "transparent",
    };
  }
  switch (status) {
    case "processing":
      return {
        mainText: "Đang phân tích tài liệu…",
        showProgress: true,
        checkboxEnabled: false,
        badge: "PROCESSING",
        badgeStyle: { background: "#1c1400", color: "#fbbf24", border: "1px solid #92400e" },
        borderColor: "#92400e",
        bgColor: "transparent",
      };
    case "index_ready":
      return {
        mainText: "Đang xử lý…",
        subText: "Đang tối ưu thêm nội dung",
        badge: "PROCESSING",
        showProgress: true,
        checkboxEnabled: false,
        badgeStyle: { background: "#1c1400", color: "#fbbf24", border: "1px solid #92400e" },
        borderColor: "#92400e",
        bgColor: "transparent",
      };
    case "ready":
      return {
        mainText: "Đang xử lý…",
        badge: "PROCESSING",
        showProgress: true,
        checkboxEnabled: false,
        badgeStyle: { background: "#1c1400", color: "#fbbf24", border: "1px solid #92400e" },
        borderColor: "#92400e",
        bgColor: "transparent",
      };
    case "error":
      return {
        mainText: "Lỗi xử lý tài liệu",
        showProgress: false,
        checkboxEnabled: false,
        badge: "ERROR",
        badgeStyle: { background: "#1f0000", color: "#f87171", border: "1px solid #991b1b" },
        borderColor: "#991b1b",
        bgColor: "transparent",
        showErrorIcon: true,
      };
    default:
      return { mainText: "Không xác định", showProgress: false, checkboxEnabled: false, badge: null, borderColor: "#374151", bgColor: "transparent" };
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
        setSources((prev) => {
          const activeSources = prev.filter((s) => s.status === "processing" || s.status === "index_ready");
          const readySources = backendSources.map((s) => ({ source_id: null, filename: formatFileName(s.video), video_stem: s.video, status: "ready", progress: 1.0, substatus: "memory_tree_ready", capabilities: { chunk_query: true, memory_query: true }, can_query: true, num_chunks: s.num_chunks }));
          const combined = [...activeSources];
          readySources.forEach((rs) => { if (!combined.some((ps) => ps.video_stem === rs.video_stem || ps.filename === rs.filename)) combined.push(rs); });
          return combined;
        });
        setSelectedSources((prev) => prev.filter((p) => backendSources.some((s) => s.video === p)));
      })
      .catch((err) => console.error("Error fetching sources:", err));
  };

  useEffect(() => { fetchSourcesFromBackend(); }, []);
  useEffect(() => { if (onSourcesChange) onSourcesChange(sources); }, [sources, onSourcesChange]);

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
      const url = src.source_id ? `/sources/${src.source_id}` : `/sources/by-stem/${encodeURIComponent(key)}`;
      const res = await apiFetch(url, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
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

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#111827" }}>
      {/* Header */}
      <div style={{ padding: "14px 16px 10px", borderBottom: "1px solid #1e2d3d", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <div>
          <div style={{ fontSize: "0.8rem", fontWeight: 700, color: "#4b5563", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 2 }}>Tài liệu</div>
          <div style={{ fontSize: "0.75rem", color: "#6b7280" }}>{sources.length} files · {selectedSources.length} đang chọn</div>
        </div>
        <button onClick={onClose} style={{ background: "transparent", border: "none", cursor: "pointer", color: "#6b7280", padding: 4, display: "flex", borderRadius: 8 }} className="md:hidden" aria-label="Đóng">
          <FiX size={18} />
        </button>
      </div>

      {/* Controls */}
      <div style={{ padding: "10px 12px 8px", flexShrink: 0, display: "flex", flexDirection: "column", gap: 8 }}>
        {/* Select all */}
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", padding: "2px 4px" }}>
          <input
            type="checkbox"
            checked={allSelected}
            onChange={(e) => handleSelectAll(e.target.checked)}
            style={{ width: 14, height: 14, accentColor: "#4f46e5", cursor: "pointer" }}
          />
          <span style={{ fontSize: "0.78rem", color: "#9ca3af", fontWeight: 500 }}>Chọn tất cả</span>
        </label>

        {/* Upload button */}
        <label
          style={{
            display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
            background: uploading ? "#374151" : "linear-gradient(135deg, #2563eb, #4f46e5)",
            color: uploading ? "#9ca3af" : "#fff",
            borderRadius: 10, padding: "9px 14px",
            cursor: uploading ? "not-allowed" : "pointer",
            fontWeight: 600, fontSize: "0.82rem",
            transition: "all 0.2s",
            boxShadow: uploading ? "none" : "0 4px 12px rgba(37,99,235,0.25)",
            userSelect: "none",
          }}
        >
          {uploading ? (
            <>
              <span style={{ width: 14, height: 14, border: "2px solid #6b7280", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite", display: "inline-block" }} />
              Đang tải lên…
            </>
          ) : (
            <>
              <span style={{ fontSize: "1rem" }}>+</span> Thêm tài liệu
            </>
          )}
          <input ref={fileInputRef} type="file" multiple className="hidden" onChange={handleAddFiles} disabled={uploading} style={{ display: "none" }} />
        </label>
      </div>

      {/* Sources list */}
      <div style={{ flex: 1, overflowY: "auto", padding: "0 12px 12px", display: "flex", flexDirection: "column", gap: 6, scrollbarWidth: "thin", scrollbarColor: "#374151 transparent" }}>
        {sources.length === 0 && (
          <div style={{ textAlign: "center", padding: "40px 16px", color: "#6b7280" }}>
            <div style={{ fontSize: "2rem", marginBottom: 10 }}>📂</div>
            <p style={{ fontSize: "0.8rem" }}>Chưa có tài liệu nào.</p>
            <p style={{ fontSize: "0.75rem", color: "#4b5563", marginTop: 4 }}>Nhấn "Thêm tài liệu" để bắt đầu.</p>
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
              style={{
                background: isSelected ? "#1e1b4b" : "#1f2937",
                border: `1.5px solid ${isSelected ? "#4f46e5" : statusConfig.borderColor}`,
                borderRadius: 12,
                padding: "10px 12px",
                cursor: checkboxEnabled ? "pointer" : "default",
                transition: "all 0.15s",
                opacity: isDeleting ? 0.5 : 1,
                boxShadow: isSelected ? "0 0 0 2px rgba(79,70,229,0.2)" : "none",
              }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={(e) => { e.stopPropagation(); toggleSelect(src); }}
                  disabled={!checkboxEnabled}
                  style={{ marginTop: 2, width: 14, height: 14, accentColor: "#4f46e5", cursor: checkboxEnabled ? "pointer" : "not-allowed", flexShrink: 0 }}
                />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ fontSize: "0.82rem", fontWeight: 600, color: "#e5e7eb", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }} title={displayName}>
                      {displayName}
                    </span>
                    {statusConfig.showErrorIcon && <FiAlertCircle color="#f87171" size={13} style={{ flexShrink: 0 }} />}
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 5 }}>
                    <span style={{ fontSize: "0.7rem", color: "#9ca3af" }}>{statusConfig.mainText}</span>
                    {statusConfig.badge && (
                      <span style={{ fontSize: "0.65rem", padding: "1px 6px", borderRadius: 99, fontWeight: 700, ...statusConfig.badgeStyle }}>{statusConfig.badge}</span>
                    )}
                  </div>

                  {statusConfig.subText && (
                    <div style={{ fontSize: "0.7rem", color: "#6b7280", marginTop: 2 }}>{statusConfig.subText}</div>
                  )}

                  {statusConfig.showProgress && (
                    <div style={{ marginTop: 7 }}>
                      <div style={{ background: "#374151", borderRadius: 99, height: 4, overflow: "hidden" }}>
                        <div style={{ width: `${(src.progress || 0) * 100}%`, height: "100%", background: "linear-gradient(90deg, #2563eb, #4f46e5)", borderRadius: 99, transition: "width 0.5s ease-out" }} />
                      </div>
                      <div style={{ fontSize: "0.67rem", color: "#6b7280", marginTop: 3 }}>{Math.round((src.progress || 0) * 100)}%</div>
                    </div>
                  )}

                  {src.status === "ready" && src.num_chunks && (
                    <div style={{ fontSize: "0.7rem", color: "#6b7280", marginTop: 3 }}>📄 {src.num_chunks} chunks</div>
                  )}

                  {src.status === "error" && src.error && (
                    <div style={{ fontSize: "0.7rem", color: "#f87171", marginTop: 4, background: "#1f0000", padding: "5px 8px", borderRadius: 6 }}>{src.error}</div>
                  )}
                </div>

                {/* Menu */}
                <div style={{ position: "relative", flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                  {isDeleting ? (
                    <span style={{ width: 16, height: 16, border: "2px solid #6b7280", borderTopColor: "transparent", borderRadius: "50%", animation: "spin 0.8s linear infinite", display: "inline-block" }} />
                  ) : (
                    <>
                      <button
                        onClick={() => setMenuOpen(menuOpen === idx ? null : idx)}
                        style={{ background: "transparent", border: "none", cursor: "pointer", padding: 4, color: "#6b7280", borderRadius: 6, display: "flex" }}
                      >
                        <FiMoreVertical size={15} />
                      </button>
                      {menuOpen === idx && (
                        <div style={{ position: "absolute", right: 0, top: 26, background: "#1f2937", border: "1px solid #374151", borderRadius: 10, boxShadow: "0 8px 24px rgba(0,0,0,0.4)", zIndex: 20, minWidth: 110 }}>
                          <button
                            onClick={() => handleDeleteSource(src)}
                            style={{ display: "block", width: "100%", textAlign: "left", padding: "9px 14px", background: "transparent", border: "none", color: "#f87171", fontSize: "0.82rem", cursor: "pointer", fontWeight: 600, borderRadius: 10 }}
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

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        .md\\:hidden { display: flex; }
        @media (min-width: 768px) { .md\\:hidden { display: none !important; } }
      `}</style>
    </div>
  );
}