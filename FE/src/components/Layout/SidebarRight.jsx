import { useState, useEffect, useCallback } from "react";
import { FiTrash2, FiX, FiMap, FiFileText, FiClock } from "react-icons/fi";
import MindMapModal from "./MindMapModal";
import SummaryModal from "./SummaryModal";
import { apiFetch } from "../../utils/api";

const formatTimeAgo = (isoDate) => {
  if (!isoDate) return "Không xác định";
  const diff = (Date.now() - new Date(isoDate).getTime()) / 1000;
  if (isNaN(diff)) return "Không xác định";
  if (diff < 60) return `${Math.floor(diff)}s trước`;
  if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`;
  return `${Math.floor(diff / 86400)} ngày trước`;
};

// ── Spinner nhỏ ────────────────────────────────────────
const Spinner = ({ color }) => (
  <span style={{
    width: 13, height: 13, flexShrink: 0,
    border: `2px solid ${color}33`,
    borderTopColor: color,
    borderRadius: "50%",
    display: "inline-block",
    animation: "sr-spin 0.75s linear infinite",
  }} />
);

// ── Action button config ──────────────────────────────
const ACTION_BUTTONS = [
  { label: "Âm thanh",  icon: "🎧", key: "audio",   disabled: true },
  { label: "Video",     icon: "🎥", key: "video",    disabled: true },
  { label: "Sơ đồ tư duy", icon: "🧠", key: "mindmap" },
  { label: "Tóm tắt",  icon: "📝", key: "summary" },
  { label: "Báo cáo",  icon: "📊", key: "report",   disabled: true },
];

const BTN_COLORS = {
  audio:   { border: "#134e4a", bg: "#022c22", text: "#2dd4bf" },
  video:   { border: "#14532d", bg: "#052e16", text: "#4ade80" },
  mindmap: { border: "#6d1d5e", bg: "#2a0a24", text: "#f472b6" },
  summary: { border: "#3b1a8f", bg: "#180e42", text: "#a78bfa" },
  report:  { border: "#78350f", bg: "#1c1400", text: "#fbbf24" },
};

// ── Section header ─────────────────────────────────────
const SectionLabel = ({ icon, children }) => (
  <div style={{
    display: "flex", alignItems: "center", gap: 6,
    fontSize: "0.68rem", fontWeight: 700, color: "#4b5563",
    textTransform: "uppercase", letterSpacing: "0.1em",
    marginBottom: 8,
  }}>
    <span style={{ fontSize: "0.78rem" }}>{icon}</span>
    {children}
  </div>
);

// ── Card item (mindmap / summary) ─────────────────────
const ListCard = ({ title, meta, onOpen, onDelete, deleteLabel = "Xóa", accentHover = "#4f46e5" }) => {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onClick={onOpen}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: hovered ? "#1a1f2e" : "#1f2937",
        border: `1px solid ${hovered ? accentHover : "#2d3748"}`,
        borderRadius: 10,
        padding: "10px 12px",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        gap: 10,
        transition: "all 0.15s",
        boxShadow: hovered ? `0 0 0 1px ${accentHover}22` : "none",
      }}
    >
      {/* Text block */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: "0.78rem", fontWeight: 600, color: "#e2e8f0",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          marginBottom: 3,
        }}>
          {title}
        </div>
        <div style={{ fontSize: "0.67rem", color: "#4b5563", display: "flex", alignItems: "center", gap: 4 }}>
          <FiClock size={10} />
          {meta}
        </div>
      </div>

      {/* Delete button — stops propagation so it doesn't open the modal */}
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        title={deleteLabel}
        style={{
          background: "transparent",
          border: "1px solid transparent",
          borderRadius: 7,
          padding: "4px 6px",
          cursor: "pointer",
          color: "#6b7280",
          display: "flex",
          alignItems: "center",
          flexShrink: 0,
          transition: "all 0.15s",
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.color = "#f87171";
          e.currentTarget.style.borderColor = "#7f1d1d";
          e.currentTarget.style.background = "#1f0a0a";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.color = "#6b7280";
          e.currentTarget.style.borderColor = "transparent";
          e.currentTarget.style.background = "transparent";
        }}
      >
        <FiTrash2 size={13} />
      </button>
    </div>
  );
};

// ── Empty placeholder ──────────────────────────────────
const EmptyPlaceholder = ({ icon, text }) => (
  <div style={{ textAlign: "center", padding: "18px 8px", color: "#374151" }}>
    <div style={{ fontSize: "1.4rem", marginBottom: 5 }}>{icon}</div>
    <p style={{ fontSize: "0.72rem", lineHeight: 1.5, margin: 0 }}>{text}</p>
  </div>
);

// ── Main component ─────────────────────────────────────
export default function SidebarRight({ selectedSources, onClose }) {
  const [mindMaps, setMindMaps]           = useState([]);
  const [showModalMap, setShowModalMap]   = useState(null);
  const [showSummaryModal, setShowSummaryModal] = useState(null);
  const [loading, setLoading]             = useState(false);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [summaries, setSummaries]         = useState([]);

  // ── Fetchers (logic không đổi) ─────────────────────
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

  const pollMindmapJob = async (jobId, opts = {}) => {
    const { intervalMs = 1200, timeoutMs = 15 * 60 * 1000 } = opts;
    const start = Date.now();
    while (true) {
      if (Date.now() - start > timeoutMs) throw new Error("Quá thời gian chờ tạo Mind Map.");
      const res = await apiFetch(`/mindmap-status/${encodeURIComponent(jobId)}`, { method: "GET", headers: { "Content-Type": "application/json" } });
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
      const res = await apiFetch(`/generate-mindmap`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sources: selectedSources, q: "tóm tắt tài liệu" }) });
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

  const handleAction = (key) => {
    if (key === "mindmap") handleGenerateMindMap();
    else if (key === "summary") handleGenerateSummary();
  };

  // ── Render ─────────────────────────────────────────
  return (
    <div style={{
      display: "flex", flexDirection: "column",
      height: "100%", background: "#0f1623",
      fontFamily: "'Inter', 'Segoe UI', sans-serif",
      overflow: "hidden",
    }}>

      {/* ── HEADER ───────────────────────────────────── */}
      <div style={{
        padding: "13px 14px 11px",
        borderBottom: "1px solid #1a2535",
        display: "flex", alignItems: "center", justifyContent: "space-between",
        flexShrink: 0, background: "#111827",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{
            width: 6, height: 6, borderRadius: "50%",
            background: "linear-gradient(135deg, #f472b6, #a78bfa)",
            boxShadow: "0 0 6px #f472b680",
          }} />
          <span style={{
            fontSize: "0.72rem", fontWeight: 700, color: "#6b7280",
            textTransform: "uppercase", letterSpacing: "0.1em",
          }}>
            Công cụ AI
          </span>
        </div>
        {/* Nút đóng — chỉ hiện trên mobile */}
        <button
          onClick={onClose}
          className="sr-mobile-only"
          aria-label="Đóng"
          style={{
            background: "transparent", border: "1px solid #2d3748",
            borderRadius: 7, padding: "5px 7px",
            cursor: "pointer", color: "#6b7280",
            display: "flex", alignItems: "center",
            transition: "all 0.15s",
          }}
          onMouseEnter={(e) => { e.currentTarget.style.color = "#e5e7eb"; e.currentTarget.style.borderColor = "#4b5563"; }}
          onMouseLeave={(e) => { e.currentTarget.style.color = "#6b7280"; e.currentTarget.style.borderColor = "#2d3748"; }}
        >
          <FiX size={15} />
        </button>
      </div>

      {/* ── ACTION BUTTONS ────────────────────────────
          Layout: 2 cột đều nhau, nút cuối (lẻ) chiếm full width
      ─────────────────────────────────────────────── */}
      <div style={{ padding: "12px 12px 4px", flexShrink: 0 }}>
        <SectionLabel icon="⚡">Tạo mới</SectionLabel>

        {/* Hàng 1: audio + video */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 7, marginBottom: 7 }}>
          {ACTION_BUTTONS.slice(0, 2).map((btn) => (
            <ActionBtn key={btn.key} btn={btn} isLoading={false} onClick={handleAction} />
          ))}
        </div>

        {/* Hàng 2: mindmap + summary */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 7, marginBottom: 7 }}>
          {ACTION_BUTTONS.slice(2, 4).map((btn) => {
            const isLoading = (btn.key === "mindmap" && loading) || (btn.key === "summary" && summaryLoading);
            return <ActionBtn key={btn.key} btn={btn} isLoading={isLoading} onClick={handleAction} />;
          })}
        </div>

        {/* Hàng 3: report (full width) */}
        <div style={{ marginBottom: 4 }}>
          <ActionBtn btn={ACTION_BUTTONS[4]} isLoading={false} onClick={handleAction} fullWidth />
        </div>
      </div>

      {/* ── SCROLLABLE LISTS ─────────────────────────── */}
      <div style={{
        flex: 1, minHeight: 0,
        overflowY: "auto",
        padding: "8px 12px 12px",
        display: "flex", flexDirection: "column", gap: 16,
        scrollbarWidth: "thin",
        scrollbarColor: "#2d3748 transparent",
      }}>

        {/* MIND MAPS */}
        <div>
          <SectionLabel icon="🧠">Sơ đồ tư duy</SectionLabel>

          {initialLoading && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#4b5563", fontSize: "0.75rem", padding: "8px 0" }}>
              <Spinner color="#6b7280" /> Đang tải…
            </div>
          )}

          {!initialLoading && mindMaps.length === 0 && (
            <EmptyPlaceholder icon="🧠" text={"Chưa có sơ đồ tư duy.\nNhấn \"Sơ đồ tư duy\" để tạo!"} />
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {mindMaps.map((map) => (
              <ListCard
                key={map.id}
                title={map.title}
                meta={`${map.sources?.length || 0} tài liệu · ${formatTimeAgo(map.createdAt)}`}
                onOpen={() => setShowModalMap(map)}
                onDelete={() => handleDeleteMap(map.id)}
                deleteLabel="Xóa sơ đồ"
                accentHover="#7c3aed"
              />
            ))}
          </div>
        </div>

        {/* DIVIDER */}
        <div style={{ borderTop: "1px solid #1a2535" }} />

        {/* SUMMARIES */}
        <div>
          <SectionLabel icon="💾">Tóm tắt đã lưu</SectionLabel>

          {summaries.length === 0 && (
            <EmptyPlaceholder icon="📝" text="Chưa có tóm tắt nào được lưu." />
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
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
                  accentHover="#4f46e5"
                />
              );
            })}
          </div>
        </div>
      </div>

      {/* ── MODALS (fullscreen, z-index 9999) ────────── */}
      {showModalMap && (
        <MindMapModal data={showModalMap} onClose={() => setShowModalMap(null)} />
      )}
      {showSummaryModal && (
        <SummaryModal
          data={showSummaryModal}
          onClose={() => setShowSummaryModal(null)}
          onSave={handleSaveSummary}
        />
      )}

      {/* ── GLOBAL STYLES ─────────────────────────────
          sr-mobile-only: hiện trên mobile, ẩn trên desktop
      ─────────────────────────────────────────────── */}
      <style>{`
        @keyframes sr-spin { to { transform: rotate(360deg); } }

        /* Desktop: ẩn nút đóng sidebar */
        @media (min-width: 768px) {
          .sr-mobile-only { display: none !important; }
        }
        /* Mobile: hiện nút đóng sidebar */
        @media (max-width: 767px) {
          .sr-mobile-only { display: flex !important; }
        }
      `}</style>
    </div>
  );
}

// ── ActionBtn sub-component ────────────────────────────
function ActionBtn({ btn, isLoading, onClick, fullWidth = false }) {
  const c = BTN_COLORS[btn.key];
  const [hovered, setHovered] = useState(false);

  return (
    <button
      onClick={() => !btn.disabled && !isLoading && onClick(btn.key)}
      disabled={btn.disabled || isLoading}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        gridColumn: fullWidth ? "1 / -1" : undefined,
        width: "100%",
        background: hovered && !btn.disabled ? `${c.bg}ee` : c.bg,
        border: `1px solid ${hovered && !btn.disabled ? c.text + "55" : c.border}`,
        color: btn.disabled ? "#3d4a5c" : c.text,
        borderRadius: 9,
        /* Chiều cao cố định để không bị thay đổi khi loading */
        height: 44,
        padding: "0 10px",
        cursor: btn.disabled ? "not-allowed" : isLoading ? "wait" : "pointer",
        fontSize: "0.74rem",
        fontWeight: 600,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        transition: "all 0.15s",
        opacity: btn.disabled ? 0.4 : 1,
        boxShadow: hovered && !btn.disabled ? `0 0 12px ${c.text}22` : "none",
        whiteSpace: "nowrap",
        overflow: "hidden",
      }}
    >
      {isLoading ? (
        <>
          <Spinner color={c.text} />
          <span>Đang tạo…</span>
        </>
      ) : (
        <>
          <span style={{ fontSize: "0.9rem", lineHeight: 1 }}>{btn.icon}</span>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{btn.label}</span>
        </>
      )}
    </button>
  );
}