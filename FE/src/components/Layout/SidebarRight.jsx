import { useState, useEffect, useCallback } from "react";
import { FiMoreVertical } from "react-icons/fi";
import MindMapModal from "./MindMapModal";
import SummaryModal from "./SummaryModal";

export default function SidebarRight({ selectedSources }) {
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
      const list = Array.isArray(data?.mindmaps) ? data.mindmaps : [];
      setMindMaps(list);
    } catch (err) {
      console.error("Mind map fetch error:", err);
      setMindMaps([]);
    } finally {
      setInitialLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMindMaps();
    fetchSummaries();
  }, [fetchMindMaps]);

  const pollMindmapJob = async (jobId, { intervalMs = 1200, timeoutMs = 15 * 60 * 1000 } = {}) => {
    const start = Date.now();
    // eslint-disable-next-line no-constant-condition
    while (true) {
      if (Date.now() - start > timeoutMs) {
        throw new Error("Quá thời gian chờ tạo Mind Map.");
      }
      const res = await fetch(`/api/mindmap-status/${encodeURIComponent(jobId)}`, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) {
        const errBody = await res.json().catch(() => ({}));
        throw new Error(errBody.error || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (data.status === "done") return data.result;
      if (data.status === "error") {
        throw new Error(data.error || "Lỗi khi tạo Mind Map.");
      }
      await new Promise((r) => setTimeout(r, intervalMs));
    }
  };

  const handleGenerateMindMap = async () => {
    console.log("handleGenerateMindMap called with:", selectedSources);
    if (!selectedSources || selectedSources.length === 0) {
      alert("Vui lòng chọn ít nhất một file để tạo Mind Map!");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`/api/generate-mindmap`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sources: selectedSources, q: "tóm tắt tài liệu" }),
      });
      console.log("▶️ POST /generate-mindmap status:", res.status);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const startData = await res.json();
      console.log("▶️ MindMap job started:", startData);
      if (startData.error) throw new Error(startData.error);

      const jobId = startData.job_id;
      if (!jobId) throw new Error("Server không trả job_id.");

      const data = await pollMindmapJob(jobId, { intervalMs: 1200, timeoutMs: 15 * 60 * 1000 });
      console.log("▶️ MindMap result:", data);

      const record = {
        id: data.id || Date.now().toString(),
        title: data.title || "Mind Map mới",
        nodes: Array.isArray(data.nodes) ? data.nodes : [],
        sources: Array.isArray(data.sources) ? data.sources : selectedSources,
        createdAt: data.createdAt || new Date().toISOString(),
      };

      setMindMaps(prev => [record, ...prev.filter(item => item.id !== record.id)]);
      await fetchMindMaps();
    } catch (err) {
      console.error("Mind Map Error:", err);
      alert("Không tạo được Mind Map, kiểm tra console!");
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteMap = async (id) => {
    if (!window.confirm("Xóa mind map này?")) return;
    try {
      const res = await fetch(`/api/mindmaps/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setMindMaps(prev => prev.filter(m => m.id !== id));
      await fetchMindMaps();
    } catch (err) {
      console.error("Mind Map delete error:", err);
      alert("Không xóa được mind map, xem console!");
    }
  };

  const handleGenerateSummary = async () => {
    console.log("handleGenerateSummary called with:", selectedSources);
    if (!selectedSources || selectedSources.length === 0) {
      alert("Vui lòng chọn ít nhất một file để tóm tắt!");
      return;
    }
    setSummaryLoading(true);
    try {
      const res = await fetch(`/api/summarize-documents`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sources: selectedSources,
          use_dancer: true,
          use_entity_chain: true,
          use_cod: true,
          use_structured: true,
          use_fact_check: true
        }),
      });
      console.log("▶️ POST /summarize-documents status:", res.status);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      console.log("▶️ Summary response data:", data);
      if (data.error) throw new Error(data.error);

      setShowSummaryModal({ ...data, sources: selectedSources });
    } catch (err) {
      console.error("Summary Error:", err);
      alert("Không tạo được tóm tắt, kiểm tra console!");
    } finally {
      setSummaryLoading(false);
    }
  };

  const fetchSummaries = useCallback(async () => {
    try {
      const res = await fetch(`/api/summaries`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const list = Array.isArray(data?.summaries) ? data.summaries : [];
      setSummaries(list);
    } catch (err) {
      console.error("Summary fetch error:", err);
      setSummaries([]);
    }
  }, []);

  const handleSaveSummary = async (payload) => {
    try {
      const res = await fetch(`/api/summaries`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await fetchSummaries();
    } catch (err) {
      console.error("Save summary error:", err);
      alert("Không lưu được tóm tắt, kiểm tra console!");
    }
  };

  const handleDeleteSummary = async (id) => {
    const summaryId = id;
    if (!summaryId) {
      alert("Không xác định được ID tóm tắt để xóa");
      return;
    }
    if (!window.confirm("Xóa tóm tắt này?")) return;
    try {
      const res = await fetch(`/api/summaries/${summaryId}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw new Error(`HTTP ${res.status}`);
      await fetchSummaries();
    } catch (err) {
      console.error("Delete summary error:", err);
      alert("Không xóa được tóm tắt, kiểm tra console!");
    }
  };

  const formatTimeAgo = (isoDate) => {
    if (!isoDate) return "Không xác định";
    const timestamp = new Date(isoDate).getTime();
    if (Number.isNaN(timestamp)) return "Không xác định";
    const diff = (Date.now() - timestamp) / 1000;
    if (diff < 60) return `${Math.floor(diff)} giây trước`;
    if (diff < 3600) return `${Math.floor(diff / 60)} phút trước`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} giờ trước`;
    return `${Math.floor(diff / 86400)} ngày trước`;
  };

  return (
    <div className="flex flex-col h-full border-l bg-white page-transition">
      {/* Nút chức năng */}
      <div className="p-4 grid grid-cols-2 gap-3">
        <button className="bg-gradient-to-br from-blue-100 to-blue-200 p-3 rounded-xl hover:from-blue-200 hover:to-blue-300 transition-all duration-200 shadow-sm hover:shadow font-medium text-blue-800 text-sm">🎧 Tổng quan Âm thanh</button>
        <button className="bg-gradient-to-br from-green-100 to-green-200 p-3 rounded-xl hover:from-green-200 hover:to-green-300 transition-all duration-200 shadow-sm hover:shadow font-medium text-green-800 text-sm">🎥 Tổng quan Video</button>
        <button
          onClick={handleGenerateMindMap}
          className="bg-gradient-to-br from-pink-100 to-pink-200 p-3 rounded-xl hover:from-pink-200 hover:to-pink-300 transition-all duration-200 shadow-sm hover:shadow flex items-center justify-center gap-2 font-medium text-pink-800 text-sm"
        >
          {loading ? (
            <>
              <div className="w-1.5 h-1.5 bg-pink-600 rounded-full animate-bounce"></div>
              <span>Đang tạo...</span>
            </>
          ) : (
            <>🧠 Sơ đồ Tư duy</>
          )}
        </button>
        <button
          onClick={handleGenerateSummary}
          className="bg-gradient-to-br from-purple-100 to-purple-200 p-3 rounded-xl hover:from-purple-200 hover:to-purple-300 transition-all duration-200 shadow-sm hover:shadow flex items-center justify-center gap-2 font-medium text-purple-800 text-sm"
        >
          {summaryLoading ? (
            <>
              <div className="w-1.5 h-1.5 bg-purple-600 rounded-full animate-bounce"></div>
              <span>Đang tạo...</span>
            </>
          ) : (
            <>📝 Tóm tắt</>
          )}
        </button>
        <button className="bg-gradient-to-br from-amber-100 to-amber-200 p-3 rounded-xl hover:from-amber-200 hover:to-amber-300 transition-all duration-200 shadow-sm hover:shadow font-medium text-amber-800 text-sm">📊 Báo cáo</button>
      </div>

      {/* Danh sách mind map */}
      <div className="flex-1 overflow-auto p-3 space-y-2 border-t">
        {initialLoading && (
          <div className="text-sm text-gray-500 italic flex items-center gap-2">
            <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"></div>
            Đang tải sơ đồ tư duy...
          </div>
        )}
        {!initialLoading && mindMaps.length === 0 && (
          <div className="text-sm text-gray-500 text-center py-6">
            <div className="text-2xl mb-2">🧠</div>
            Chưa có sơ đồ tư duy nào.<br />Hãy tạo mới!
          </div>
        )}
        {mindMaps.map((map) => (
          <div
            key={map.id}
            onClick={() => setShowModalMap(map)}
            className="flex items-start justify-between border border-gray-200 rounded-xl p-3 cursor-pointer hover:bg-gradient-to-r hover:from-blue-50 hover:to-purple-50 transition-all duration-200 shadow-sm hover:shadow card-hover"
          >
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-sm text-gray-800 truncate">{map.title}</div>
              <div className="text-xs text-gray-500 mt-1">
                📄 {(map.sources?.length || 0)} tài liệu · {formatTimeAgo(map.createdAt)}
              </div>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation();
                handleDeleteMap(map.id);
              }}
              className="p-1.5 rounded-lg hover:bg-red-100 transition-colors duration-150"
            >
              <FiMoreVertical size={16} className="text-gray-600" />
            </button>
          </div>
        ))}
      </div>

      {/* Danh sách tóm tắt đã lưu */}
      <div className="border-t p-3 space-y-2 max-h-64 overflow-auto bg-gradient-to-b from-white to-gray-50">
        <div className="text-xs font-bold text-gray-700 uppercase tracking-wide">💾 Tóm tắt đã lưu</div>
        {summaries.length === 0 && (
          <div className="text-xs text-gray-500 text-center py-4">
            Chưa có tóm tắt nào
          </div>
        )}
        {summaries.map((item) => {
          const summaryId = item.id || item?.data?.id;
          return (
            <div
              key={item.id}
              className="border border-gray-200 rounded-xl p-3 hover:bg-gradient-to-r hover:from-purple-50 hover:to-blue-50 cursor-pointer flex items-start justify-between gap-2 transition-all duration-200 shadow-sm hover:shadow"
              onClick={() => setShowSummaryModal(item.data || item)}
            >
              <div className="min-w-0 flex-1">
                <div className="font-semibold text-sm truncate text-gray-800">{item.title || "Tóm tắt"}</div>
                <div className="text-xs text-gray-500 mt-1">{formatTimeAgo(item.createdAt)}</div>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleDeleteSummary(summaryId);
                }}
                className="px-2 py-1 rounded-lg hover:bg-red-100 text-red-600 text-xs font-medium transition-colors duration-150"
                disabled={!summaryId}
              >
                Xóa
              </button>
            </div>
          );
        })}
      </div>

      {/* Modal mind map */}
      {showModalMap && (
        <MindMapModal data={showModalMap} onClose={() => setShowModalMap(null)} />
      )}

      {/* Modal tóm tắt */}
      {showSummaryModal && (
        <SummaryModal
          data={showSummaryModal}
          onClose={() => setShowSummaryModal(null)}
          onSave={handleSaveSummary}
        />
      )}
    </div>
  );
}
