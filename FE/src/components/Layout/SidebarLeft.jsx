import { useState, useEffect, useRef } from "react";
import { FiMoreVertical, FiAlertCircle } from "react-icons/fi";

// ============================================
// Helper functions: Status → UI mapping
// ============================================

/**
 * Get UI config cho một status
 * @param {string} status - "processing" | "index_ready" | "ready" | "error"
 * @param {string} substatus - Optional substatus
 * @returns {object} UI config
 */
const getStatusConfig = (status, substatus) => {
  switch (status) {
    case "processing":
      return {
        mainText: "Đang phân tích tài liệu…",
        showProgress: true,
        checkboxEnabled: false,
        badge: null,
        borderColor: "",
        bgColor: "",
      };

    case "index_ready":
      return {
        mainText: "Có thể sử dụng",
        subText: substatus === "building_memory_tree"
          ? "Đang tối ưu thêm nội dung"
          : "Đang tối ưu thêm nội dung",
        badge: "Sẵn sàng tra cứu",
        showProgress: true,
        checkboxEnabled: true,
        borderColor: "",
        bgColor: "",
      };

    case "ready":
      return {
        mainText: "Sẵn sàng",
        badge: "Hoàn tất",
        showProgress: false,
        checkboxEnabled: true,
        borderColor: "",
        bgColor: "",
      };

    case "error":
      return {
        mainText: "Lỗi xử lý tài liệu",
        showProgress: false,
        checkboxEnabled: false,
        badge: null,
        borderColor: "border-red-300",
        bgColor: "bg-red-50",
        showErrorIcon: true,
      };

    default:
      return {
        mainText: "Không xác định",
        showProgress: false,
        checkboxEnabled: false,
        badge: null,
        borderColor: "",
        bgColor: "",
      };
  }
};

export default function SidebarLeft({ selectedSources, setSelectedSources, onSourcesChange }) {
  // Source state: { source_id, filename, status, progress, video_stem?, substatus?, capabilities?, error? }
  const [sources, setSources] = useState([]);
  const [menuOpen, setMenuOpen] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [deletingFile, setDeletingFile] = useState(null);
  const fileInputRef = useRef(null);
  const pollingIntervalsRef = useRef({}); // Track polling intervals per source_id

  // Poll status cho một source
  const pollSourceStatus = (sourceId) => {
    if (pollingIntervalsRef.current[sourceId]) {
      return; // Already polling
    }

    const poll = async () => {
      try {
        const res = await fetch(`/api/sources/${sourceId}/status`);
        if (!res.ok) {
          // Source not found hoặc error -> stop polling
          stopPolling(sourceId);
          return;
        }
        const data = await res.json();

        // Update source với tất cả fields từ API: status, progress, substatus, capabilities, error
        setSources((prev) =>
          prev.map((s) =>
            s.source_id === sourceId
              ? {
                ...s,
                status: data.status,
                progress: data.progress ?? s.progress, // Giữ progress cũ nếu không có
                substatus: data.substatus,
                capabilities: data.capabilities,
                error: data.error
              }
              : s
          )
        );

        // Stop polling nếu ready hoặc error
        // index_ready: tiếp tục poll để chờ ready (Memory Tree đang build)
        if (data.status === "ready" || data.status === "error") {
          stopPolling(sourceId);
          // Nếu ready, refresh sources từ backend để sync
          if (data.status === "ready") {
            setTimeout(() => fetchSourcesFromBackend(), 500);
          }
        }
        // index_ready: không stop polling, tiếp tục poll để chờ ready
      } catch (err) {
        console.error(`Error polling status for ${sourceId}:`, err);
        stopPolling(sourceId);
      }
    };

    // Poll ngay lập tức, sau đó mỗi 1.5s
    poll();
    pollingIntervalsRef.current[sourceId] = setInterval(poll, 1500);
  };

  const stopPolling = (sourceId) => {
    if (pollingIntervalsRef.current[sourceId]) {
      clearInterval(pollingIntervalsRef.current[sourceId]);
      delete pollingIntervalsRef.current[sourceId];
    }
  };

  // Fetch sources từ backend (legacy sources đã ready)
  const fetchSourcesFromBackend = () => {
    fetch(`/api/list-indexed`)
      .then((res) => res.json())
      .then((data) => {
        const backendSources = data.sources || [];

        // Merge với sources đang processing/index_ready
        setSources((prev) => {
          const activeSources = prev.filter((s) =>
            s.status === "processing" || s.status === "index_ready"
          );
          const readySources = backendSources.map((s) => ({
            source_id: null, // Legacy source không có source_id
            filename: formatFileName(s.video),
            video_stem: s.video,
            status: "ready",
            progress: 1.0,
            substatus: "memory_tree_ready",
            capabilities: { chunk_query: true, memory_query: true },
            num_chunks: s.num_chunks,
          }));

          // Combine: active sources (processing/index_ready) + ready sources (loại bỏ duplicate)
          const combined = [...activeSources];
          readySources.forEach((rs) => {
            const exists = combined.some(
              (ps) => ps.video_stem === rs.video_stem || ps.filename === rs.filename
            );
            if (!exists) {
              combined.push(rs);
            }
          });

          return combined;
        });

        // Clean up selected sources nếu không còn tồn tại
        setSelectedSources((prev) =>
          prev.filter((p) => {
            const exists = backendSources.some((s) => s.video === p);
            return exists;
          })
        );
      })
      .catch((err) => console.error("Error fetching sources:", err));
  };

  useEffect(() => {
    fetchSourcesFromBackend();
  }, []);

  // Notify parent component khi sources thay đổi
  useEffect(() => {
    if (onSourcesChange) {
      onSourcesChange(sources);
    }
  }, [sources, onSourcesChange]);

  // Cleanup polling intervals khi unmount
  useEffect(() => {
    return () => {
      Object.values(pollingIntervalsRef.current).forEach((interval) =>
        clearInterval(interval)
      );
    };
  }, []);

  const handleAddFiles = async (e) => {
    const files = e.target.files;
    if (!files.length) return;

    setUploading(true);
    const uploadedSources = [];

    // Upload từng file (optimistic UI)
    for (let file of files) {
      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch(`/api/upload`, {
          method: "POST",
          body: formData,
        });

        if (!res.ok) {
          throw new Error(`Upload failed for ${file.name}`);
        }

        const data = await res.json();
        const newSource = {
          source_id: data.source_id,
          filename: data.filename,
          status: data.status || "processing",
          progress: 0.0,
        };

        // Optimistic UI: Thêm vào list ngay
        setSources((prev) => [...prev, newSource]);
        uploadedSources.push(data.source_id);

        // Bắt đầu polling status
        pollSourceStatus(data.source_id);
      } catch (err) {
        console.error(`Error uploading ${file.name}:`, err);
        // Thêm source với status error
        setSources((prev) => [
          ...prev,
          {
            source_id: null,
            filename: file.name,
            status: "error",
            progress: 0.0,
            error: err.message,
          },
        ]);
      }
    }

    setUploading(false);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleSelectAll = (checked) => {
    if (checked) {
      // Chọn sources đã index_ready hoặc ready
      const readySources = sources
        .filter((s) => (s.status === "ready" || s.status === "index_ready") && s.video_stem)
        .map((s) => s.video_stem);
      setSelectedSources(readySources);
    } else {
      setSelectedSources([]);
    }
  };

  const toggleSelect = (source) => {
    // Cho phép select sources đã index_ready hoặc ready
    if ((source.status !== "ready" && source.status !== "index_ready") || !source.video_stem) {
      return;
    }

    setSelectedSources((prev) =>
      prev.includes(source.video_stem)
        ? prev.filter((v) => v !== source.video_stem)
        : [...prev, source.video_stem]
    );
  };

  const handleDeleteSource = async (source) => {
    const videoStem = source.video_stem || source.video;
    if (!videoStem) return;

    setDeletingFile(videoStem);
    setMenuOpen(null);

    // Stop polling nếu đang processing
    if (source.source_id) {
      stopPolling(source.source_id);
    }

    try {
      await fetch(`/api/delete-source`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video: videoStem }),
      });

      // Remove từ local state
      setSources((prev) => prev.filter((s) => s.video_stem !== videoStem));
      setSelectedSources((prev) => prev.filter((v) => v !== videoStem));
    } catch (err) {
      console.error("Error deleting source:", err);
    }

    setDeletingFile(null);
  };

  // 👉 helper format tên file
  const formatFileName = (videoPath) => {
    if (!videoPath) return "";
    const rawName = videoPath.split("/").pop().replace(/\.mp4$/, "");
    const parts = rawName.split("_");

    const timePart = parts[parts.length - 1];
    let displayTime = "";
    if (timePart && timePart.length === 6) {
      displayTime = `${timePart.slice(0, 2)}:${timePart.slice(2, 4)}:${timePart.slice(4, 6)}`;
    }

    return parts.slice(0, -1).join("_") + (displayTime ? ` (${displayTime})` : "");
  };

  return (
    <div className="p-4 flex flex-col h-full relative bg-white">
      <h2 className="text-xl font-bold mb-6 text-gray-800">Tài liệu</h2>

      {/* Select all */}
      <label className="flex items-center space-x-3 mb-3 px-2">
        <input
          type="checkbox"
          checked={
            selectedSources.length === sources.length && sources.length > 0
          }
          onChange={(e) => handleSelectAll(e.target.checked)}
          className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-2 focus:ring-blue-500"
        />
        <span className="text-sm font-medium text-gray-700">Chọn tất cả</span>
      </label>

      {/* Add button */}
      <label
        className={`px-4 py-2.5 rounded-lg mb-4 text-center cursor-pointer flex items-center justify-center space-x-2 font-medium transition-all duration-200 ${uploading
          ? "bg-gray-400 cursor-not-allowed"
          : "bg-gradient-to-r from-blue-600 to-blue-500 text-white hover:from-blue-700 hover:to-blue-600 shadow hover:shadow-md"
          }`}
      >
        {uploading ? (
          <>
            <svg
              className="animate-spin h-4 w-4 text-white"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
                fill="none"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v8z"
              />
            </svg>
            <span>Uploading...</span>
          </>
        ) : (
          <span>+ Thêm</span>
        )}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleAddFiles}
          disabled={uploading}
        />

      </label>

      {/* Sources list */}
      <div className="flex-1 overflow-auto space-y-2">
        {sources.map((src, idx) => {
          const displayName = src.filename || formatFileName(src.video || "");
          const isDeleting = deletingFile === (src.video_stem || src.video);
          const isSelected = selectedSources.includes(src.video_stem || src.video);

          // Get UI config từ status
          const statusConfig = getStatusConfig(src.status, src.substatus);
          const showProgress = statusConfig.showProgress && src.status !== "ready";
          const checkboxEnabled = statusConfig.checkboxEnabled && !isDeleting;

          return (
            <div
              key={src.source_id || src.video_stem || idx}
              onClick={() => checkboxEnabled && toggleSelect(src)}
              className={`p-3 bg-white border border-gray-200 rounded-xl shadow-sm flex flex-col transition-all duration-200 ${isDeleting ? "opacity-50" : "card-hover"
                } ${isSelected ? "ring-2 ring-blue-500 border-blue-500 bg-blue-50" : ""
                } ${statusConfig.borderColor} ${statusConfig.bgColor} ${checkboxEnabled ? "cursor-pointer" : "cursor-default"
                }`}
            >
              <div className="flex justify-between items-start">
                <div className="flex items-start space-x-3 flex-1 min-w-0">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={(e) => {
                      e.stopPropagation();
                      toggleSelect(src);
                    }}
                    disabled={!checkboxEnabled}
                    className="mt-1 w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-2 focus:ring-blue-500 cursor-pointer"
                  />
                  <div className="flex-1 min-w-0">
                    {/* Filename */}
                    <div className="flex items-center gap-2">
                      <div
                        className="text-sm font-semibold text-gray-800 truncate"
                        title={displayName}
                      >
                        {displayName}
                      </div>
                      {/* Error icon */}
                      {statusConfig.showErrorIcon && (
                        <FiAlertCircle className="text-red-500 flex-shrink-0" size={16} />
                      )}
                    </div>

                    {/* Status text và badge */}
                    <div className="mt-1.5 flex items-center gap-2 flex-wrap">
                      <span className="text-xs text-gray-600 font-medium">
                        {statusConfig.mainText}
                      </span>
                      {statusConfig.badge && (
                        <span className="text-xs px-2.5 py-0.5 bg-gradient-to-r from-blue-50 to-blue-100 text-blue-700 rounded-full font-medium">
                          {statusConfig.badge}
                        </span>
                      )}
                    </div>

                    {/* Sub text (cho index_ready) */}
                    {statusConfig.subText && (
                      <div className="text-xs text-gray-500 mt-1">
                        {statusConfig.subText}
                      </div>
                    )}

                    {/* Progress bar - chỉ hiển thị khi showProgress = true và status != ready */}
                    {showProgress && (
                      <div className="mt-2">
                        <div className="w-full bg-gray-200 rounded-full h-1.5 overflow-hidden">
                          <div
                            className="bg-gradient-to-r from-blue-500 to-blue-600 h-1.5 rounded-full transition-all duration-500 ease-out relative"
                            style={{ width: `${(src.progress || 0) * 100}%` }}
                          >
                            <div className="absolute inset-0 bg-white/30 animate-pulse"></div>
                          </div>
                        </div>
                        <div className="text-xs text-gray-600 font-medium mt-1">
                          {Math.round((src.progress || 0) * 100)}%
                        </div>
                      </div>
                    )}

                    {/* Ready status với num_chunks */}
                    {src.status === "ready" && src.num_chunks && (
                      <div className="text-xs text-gray-500 mt-1 font-medium">
                        📄 {src.num_chunks} chunks
                      </div>
                    )}

                    {/* Error message */}
                    {src.status === "error" && src.error && (
                      <div className="text-xs text-red-600 mt-1 bg-red-50 p-2 rounded">
                        {src.error}
                      </div>
                    )}
                  </div>
                </div>

                {/* Menu */}
                <div className="relative ml-2" onClick={(e) => e.stopPropagation()}>
                  {isDeleting ? (
                    <svg
                      className="animate-spin h-5 w-5 text-gray-400"
                      viewBox="0 0 24 24"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                        fill="none"
                      />
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8v8z"
                      />
                    </svg>
                  ) : (
                    <>
                      <button
                        onClick={() =>
                          setMenuOpen(menuOpen === idx ? null : idx)
                        }
                        className="p-1.5 hover:bg-gray-100 rounded-lg transition-colors duration-150"
                      >
                        <FiMoreVertical className="text-gray-600" />
                      </button>

                      {menuOpen === idx && (
                        <div className="absolute right-0 top-8 bg-white border border-gray-200 rounded-lg shadow-lg text-sm z-10 min-w-[120px]">
                          <button
                            onClick={() => handleDeleteSource(src)}
                            className="block px-4 py-2 hover:bg-red-50 text-red-600 w-full text-left rounded-lg transition-colors duration-150 font-medium"
                          >
                            Xóa
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
