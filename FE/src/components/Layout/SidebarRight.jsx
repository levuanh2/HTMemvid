import { useState, useEffect, useCallback } from "react";
import { FiMoreVertical, FiX } from "react-icons/fi";
import MindMapModal from "./MindMapModal";
import SummaryModal from "./SummaryModal";

const formatTimeAgo = (isoDate) => {
  if (!isoDate) return "Không xác định";
  const diff = (Date.now() - new Date(isoDate).getTime()) / 1000;
  if (isNaN(diff)) return "Không xác định";
  if (diff < 60) return `${Math.floor(diff)}s trước`;
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`;
  return `${Math.floor(diff / 86400)} ngày trước`;
};

// ── Action button config ──────────────────────────────
const ACTION_BUTTONS = [
  { label: "Tổng quan Âm thanh", icon: "🎧", key: "audio", disabled: true },
  { label: "Tổng quan Video",    icon: "🎥", key: "video", disabled: true },
  { label: "Sơ đồ Tư duy",       icon: "🧠", key: "mindmap" },
  { label: "Tóm tắt",            icon: "📝", key: "summary" },
  { label: "Báo cáo",            icon: "📊", key: "report", disabled: true },
];

const BTN_COLORS = {
  audio:   { border: "#134e4a", bg: "#022c22", text: "#2dd4bf" },
  video:   { border: "#14532d", bg: "#052e16", text: "#4ade80" },
  mindmap: { border: "#831843", bg: "#2d0a1a", text: "#f472b6" },
  summary: { border: "#4c1d95", bg: "#1e1040", text: "#a78bfa" },
  report:  { border: "#78350f", bg: "#1c1400", text: "#fbbf24" },
};

export default function SidebarRight({ selectedSources, onClose }) {
  const [mindMaps, setMindMaps] = useState([]);
  const [showModalMap, setShowModalMap] = useState(null);
  const [showSummaryModal, setShowSummaryModal] = useState(null);
  const [loading, setLoading] = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [summaries, setSummaries] = useState([]);

  const fetchMindMaps = useCallback(async () => {
    try {
      const res = await fetch(`/api/mindmaps`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setMindMaps(Array.isArray(data?.mindmaps) ? data.mindmaps : []);
    } catch (err) { console.error("Mind map fetch error:", err); setMindMaps([]); }
    finally { setInitialLoading(false); }
  }, []);

  const fetchSummaries = useCallback(async () => {
    try {
      const res = await fetch(`/api/summaries`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSummaries(Array.isArray(data?.summaries) ? data.summaries : []);
    } catch (err) { console.error("Summary fetch error:", err); setSummaries([]); }
  }, []);

  useEffect(() => { fetchMindMaps(); fetchSummaries(); }, [fetchMindMaps, fetchSummaries]);

  const pollMindmapJob = async (jobId, opts = {}) => {
    const { intervalMs = 1200, timeoutMs = 15 * 60 * 1000 } = opts;
    const start = Date.now();
    while (true) {
      if (Date.now() - start > timeoutMs) throw new Error("Quá thời gian chờ tạo Mind Map.");
      const res = await fetch(`/api/mindmap-status/${encodeURIComponent(jobId)}`, { method: "GET", headers: { "Content-Type": "application/json" } });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `HTTP ${res.status}`); }
      const data = await res.json();
      if (data.status === "done") return data.result;
      if (data.status === "error" || data.status === "timeout") throw new Error(data.error || "Lỗi khi tạo Mind Map.");
      await new Promise((r) => setTimeout(r, intervalMs));
    }
  };

  const handleGenerateMindMap = async () => {
    if (!selectedSources?.length) { alert("Vui lòng chọn ít nhất một file để tạo Mind Map!"); return; }
    setLoading(true);
    try {
      const res = await fetch(`/api/generate-mindmap`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sources: selectedSources, q: "tóm tắt tài liệu" }) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const startData = await res.json();
      if (startData.error) throw new Error(startData.error);
      if (!startData.job_id) throw new Error("Server không trả job_id.");
      const data = await pollMindmapJob(startData.job_id);
      const record = { id: data.id || Date.now().toString(), title: data.title || "Mind Map mới", nodes: Array.isArray(data.nodes) ? data.nodes : [], sources: Array.isArray(data.sources) ? data.sources : selectedSources, createdAt: data.createdAt || new Date().toISOString() };
      setMindMaps((prev) => [record, ...prev.filter((item) => item.id !== record.id)]);
      await fetchMindMaps();
    } catch (err) { console.error("Mind Map Error:", err); alert("Không tạo được Mind Map, kiểm tra console!"); }
    finally { setLoading(false); }
  };

  const handleDeleteMap = async (id) => {
    if (!window.confirm("Xóa mind map này?")) return;
    try {
      const res = await fetch(`/api/mindmaps/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMindMaps((prev) => prev.filter((m) => m.id !== id));
      await fetchMindMaps();
    } catch (err) { console.error("Mind Map delete error:", err); alert("Không xóa được mind map!"); }
  };

  const handleGenerateSummary = async () => {
    if (!selectedSources?.length) { alert("Vui lòng chọn ít nhất một file để tóm tắt!"); return; }
    setSummaryLoading(true);
    try {
      const res = await fetch(`/api/summarize-documents`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sources: selectedSources, use_dancer: true, use_entity_chain: true, use_cod: true, use_structured: true, use_fact_check: true }) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      setShowSummaryModal({ ...data, sources: selectedSources });
    } catch (err) { console.error("Summary Error:", err); alert("Không tạo được tóm tắt, kiểm tra console!"); }
    finally { setSummaryLoading(false); }
  };

  const handleSaveSummary = async (payload) => {
    try {
      const res = await fetch(`/api/summaries`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await fetchSummaries();
    } catch (err) { console.error("Save summary error:", err); alert("Không lưu được tóm tắt!"); }
  };

  const handleDeleteSummary = async (id) => {
    if (!id) { alert("Không xác định được ID tóm tắt"); return; }
    if (!window.confirm("Xóa tóm tắt này?")) return;
    try {
      const res = await fetch(`/api/summaries/${id}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
      await fetchSummaries();
    } catch (err) { console.error("Delete summary error:", err); alert("Không xóa được tóm tắt!"); }
  };

  const handleAction = (key) => {
    if (key === "mindmap") handleGenerateMindMap();
    else if (key === "summary") handleGenerateSummary();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#111827" }}>
      {/* Header */}
      <div style={{ padding: "14px 16px 10px", borderBottom: "1px solid #1e2d3d", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <div style={{ fontSize: "0.8rem", fontWeight: 700, color: "#4b5563", textTransform: "uppercase", letterSpacing: "0.08em" }}>Công cụ AI</div>
        <button onClick={onClose} style={{ background: "transparent", border: "none", cursor: "pointer", color: "#6b7280", padding: 4, display: "flex", borderRadius: 8 }} className="md:hidden" aria-label="Đóng">
          <FiX size={18} />
        </button>
      </div>

      {/* Action buttons */}
      <div style={{ padding: "12px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, flexShrink: 0 }}>
        {ACTION_BUTTONS.map((btn) => {
          const c = BTN_COLORS[btn.key];
          const isLoading = (btn.key === "mindmap" && loading) || (btn.key === "summary" && summaryLoading);
          return (
            <button
              key={btn.key}
              onClick={() => !btn.disabled && handleAction(btn.key)}
              disabled={btn.disabled || isLoading}
              style={{
                background: c.bg,
                border: `1px solid ${c.border}`,
                color: btn.disabled ? "#4b5563" : c.text,
                borderRadius: 10,
                padding: "10px 6px",
                cursor: btn.disabled ? "not-allowed" : "pointer",
                fontSize: "0.75rem",
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 5,
                transition: "all 0.15s",
                opacity: btn.disabled ? 0.5 : 1,
              }}
            >
              {isLoading ? (
                <>
                  <span style={{ width: 12, height: 12, border: `2px solid ${c.text}40`, borderTopColor: c.text, borderRadius: "50%", animation: "spin 0.8s linear infinite", display: "inline-block" }} />
                  Đang tạo…
                </>
              ) : (
                <><span>{btn.icon}</span>{btn.label}</>
              )}
            </button>
          );
        })}
      </div>

      {/* Mind maps list */}
      <div style={{ flex: 1, overflowY: "auto", padding: "0 12px 8px", scrollbarWidth: "thin", scrollbarColor: "#374151 transparent" }}>
        <div style={{ fontSize: "0.7rem", fontWeight: 700, color: "#4b5563", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8, marginTop: 4 }}>
          🧠 Sơ đồ tư duy
        </div>

        {initialLoading && (
          <div style={{ fontSize: "0.75rem", color: "#6b7280", display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 10, height: 10, border: "2px solid #374151", borderTopColor: "#6b7280", borderRadius: "50%", animation: "spin 0.8s linear infinite", display: "inline-block" }} />
            Đang tải…
          </div>
        )}

        {!initialLoading && mindMaps.length === 0 && (
          <div style={{ textAlign: "center", padding: "20px 8px", color: "#4b5563" }}>
            <div style={{ fontSize: "1.5rem", marginBottom: 6 }}>🧠</div>
            <p style={{ fontSize: "0.75rem" }}>Chưa có sơ đồ tư duy.<br />Nhấn "Sơ đồ Tư duy" để tạo!</p>
          </div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {mindMaps.map((map) => (
            <div
              key={map.id}
              onClick={() => setShowModalMap(map)}
              style={{ background: "#1f2937", border: "1px solid #374151", borderRadius: 10, padding: "10px 12px", cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, transition: "all 0.15s" }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = "#4f46e5"; e.currentTarget.style.background = "#1e1b4b"; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = "#374151"; e.currentTarget.style.background = "#1f2937"; }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "#e5e7eb", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{map.title}</div>
                <div style={{ fontSize: "0.68rem", color: "#6b7280", marginTop: 3 }}>
                  📄 {map.sources?.length || 0} tài liệu · {formatTimeAgo(map.createdAt)}
                </div>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); handleDeleteMap(map.id); }}
                style={{ background: "transparent", border: "none", cursor: "pointer", color: "#6b7280", padding: 3, borderRadius: 6, display: "flex", flexShrink: 0 }}
              >
                <FiMoreVertical size={14} />
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* Summaries list */}
      <div style={{ borderTop: "1px solid #1e2d3d", padding: "10px 12px", maxHeight: 240, overflowY: "auto", background: "#0f172a", scrollbarWidth: "thin", scrollbarColor: "#374151 transparent", flexShrink: 0 }}>
        <div style={{ fontSize: "0.7rem", fontWeight: 700, color: "#4b5563", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8 }}>
          💾 Tóm tắt đã lưu
        </div>

        {summaries.length === 0 && (
          <div style={{ fontSize: "0.75rem", color: "#4b5563", textAlign: "center", padding: "12px 0" }}>Chưa có tóm tắt nào</div>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {summaries.map((item) => {
            const summaryId = item.id || item?.data?.id;
            return (
              <div
                key={item.id}
                onClick={() => setShowSummaryModal(item.data || item)}
                style={{ background: "#1f2937", border: "1px solid #374151", borderRadius: 10, padding: "9px 12px", cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, transition: "all 0.15s" }}
                onMouseEnter={(e) => { e.currentTarget.style.borderColor = "#4f46e5"; e.currentTarget.style.background = "#1e1040"; }}
                onMouseLeave={(e) => { e.currentTarget.style.borderColor = "#374151"; e.currentTarget.style.background = "#1f2937"; }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "#e5e7eb", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.title || "Tóm tắt"}</div>
                  <div style={{ fontSize: "0.68rem", color: "#6b7280", marginTop: 3 }}>{formatTimeAgo(item.createdAt)}</div>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); handleDeleteSummary(summaryId); }}
                  disabled={!summaryId}
                  style={{ background: "transparent", border: "none", cursor: summaryId ? "pointer" : "not-allowed", color: "#f87171", fontSize: "0.7rem", fontWeight: 600, padding: "2px 6px", borderRadius: 6, flexShrink: 0 }}
                >
                  Xóa
                </button>
              </div>
            );
          })}
        </div>
      </div>

      {/* Modals */}
      {showModalMap && <MindMapModal data={showModalMap} onClose={() => setShowModalMap(null)} />}
      {showSummaryModal && <SummaryModal data={showSummaryModal} onClose={() => setShowSummaryModal(null)} onSave={handleSaveSummary} />}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        .md\\:hidden { display: flex; }
        @media (min-width: 768px) { .md\\:hidden { display: none !important; } }
      `}</style>
    </div>
  );
}