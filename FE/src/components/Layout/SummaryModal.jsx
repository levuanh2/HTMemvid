import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";

export default function SummaryModal({ data, onClose, onSave }) {
  const [activeTab, setActiveTab] = useState("summary");
  const [title, setTitle] = useState(() => data?.title || "Tóm tắt tài liệu");
  const [saving, setSaving] = useState(false);

  if (!data) return null;

  const {
    summary,
    base_summary,
    entities = [],
    structured = null,
    fact_check = null,
    metadata = {},
    sources = []
  } = data;

  const tabs = [
    { id: "summary", label: "Tóm tắt", icon: "📄" },
    { id: "structured", label: "Cấu trúc", icon: "📊" },
    { id: "entities", label: "Thực thể", icon: "🔑" },
    { id: "factcheck", label: "Kiểm chứng", icon: "✅" },
    { id: "metadata", label: "Thông tin", icon: "ℹ️" }
  ];

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-4xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b bg-gradient-to-r from-purple-50 to-blue-50">
          <div className="flex flex-col gap-2 w-full">
            <div className="flex items-center gap-3">
              <h2 className="text-xl font-bold text-gray-800 flex-1">Tóm tắt tài liệu</h2>
              <button
                onClick={onClose}
                className="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600 transition"
              >
                Đóng
              </button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="flex-1 min-w-[200px] border rounded px-3 py-2 text-sm"
                placeholder="Nhập tiêu đề để lưu"
              />
              {onSave && (
                <button
                  onClick={async () => {
                    if (!title.trim()) return;
                    try {
                      setSaving(true);
                      await onSave({
                        title: title.trim(),
                        data,
                        sources,
                      });
                      setSaving(false);
                    } catch (err) {
                      console.error(err);
                      setSaving(false);
                    }
                  }}
                  className="px-4 py-2 bg-purple-600 text-white rounded hover:bg-purple-700 transition disabled:opacity-60"
                  disabled={saving}
                >
                  {saving ? "Đang lưu..." : "Lưu tóm tắt"}
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b bg-gray-50 overflow-x-auto">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2 whitespace-nowrap border-b-2 transition ${
                activeTab === tab.id
                  ? "border-purple-500 text-purple-600 font-semibold bg-white"
                  : "border-transparent text-gray-600 hover:text-gray-800 hover:bg-gray-100"
              }`}
            >
              <span className="mr-2">{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-6">
          {/* Tab: Summary */}
          {activeTab === "summary" && (
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold mb-2 text-gray-800">Bản tóm tắt chính</h3>
                <div className="prose max-w-none bg-gray-50 p-4 rounded-lg border">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                    {summary || "Không có tóm tắt"}
                  </ReactMarkdown>
                </div>
              </div>
              {base_summary && base_summary !== summary && (
                <div>
                  <h3 className="text-lg font-semibold mb-2 text-gray-800">Tóm tắt cơ bản (trước khi xử lý)</h3>
                  <div className="prose max-w-none bg-blue-50 p-4 rounded-lg border border-blue-200">
                    <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                      {base_summary}
                    </ReactMarkdown>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Tab: Structured */}
          {activeTab === "structured" && (
            <div className="space-y-4">
              {structured ? (
                <div className="space-y-4">
                  {structured.title && (
                    <div>
                      <h3 className="text-lg font-semibold mb-2 text-gray-800">Tiêu đề</h3>
                      <p className="text-lg font-medium text-gray-700 bg-gray-50 p-3 rounded">
                        {structured.title}
                      </p>
                    </div>
                  )}
                  {structured.keyPoints && structured.keyPoints.length > 0 && (
                    <div>
                      <h3 className="text-lg font-semibold mb-2 text-gray-800">Các ý chính</h3>
                      <ul className="list-disc list-inside space-y-2 bg-gray-50 p-4 rounded">
                        {structured.keyPoints.map((point, idx) => (
                          <li key={idx} className="text-gray-700">
                            {typeof point === "string" ? point : JSON.stringify(point)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {structured.formulas && structured.formulas.length > 0 && (
                    <div>
                      <h3 className="text-lg font-semibold mb-2 text-gray-800">Công thức/Quy trình</h3>
                      <ul className="list-disc list-inside space-y-2 bg-yellow-50 p-4 rounded border border-yellow-200">
                        {structured.formulas.map((formula, idx) => (
                          <li key={idx} className="text-gray-700 font-mono">
                            {typeof formula === "string" ? formula : JSON.stringify(formula)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {structured.applications && structured.applications.length > 0 && (
                    <div>
                      <h3 className="text-lg font-semibold mb-2 text-gray-800">Ứng dụng</h3>
                      <ul className="list-disc list-inside space-y-2 bg-green-50 p-4 rounded border border-green-200">
                        {structured.applications.map((app, idx) => (
                          <li key={idx} className="text-gray-700">
                            {typeof app === "string" ? app : JSON.stringify(app)}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {structured.summary && (
                    <div>
                      <h3 className="text-lg font-semibold mb-2 text-gray-800">Tóm tắt đầy đủ</h3>
                      <div className="prose max-w-none bg-gray-50 p-4 rounded-lg border">
                        <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
                          {structured.summary}
                        </ReactMarkdown>
                      </div>
                    </div>
                  )}
                  <div className="mt-4">
                    <h3 className="text-lg font-semibold mb-2 text-gray-800">Dữ liệu JSON đầy đủ</h3>
                    <pre className="bg-gray-900 text-green-400 p-4 rounded-lg overflow-x-auto text-sm">
                      {JSON.stringify(structured, null, 2)}
                    </pre>
                  </div>
                </div>
              ) : (
                <div className="text-center text-gray-500 py-8">
                  <p>Chưa có dữ liệu cấu trúc</p>
                  <p className="text-sm mt-2">Có thể do phương pháp Structured Extraction chưa được kích hoạt</p>
                </div>
              )}
            </div>
          )}

          {/* Tab: Entities */}
          {activeTab === "entities" && (
            <div className="space-y-4">
              {entities && entities.length > 0 ? (
                <div>
                  <h3 className="text-lg font-semibold mb-4 text-gray-800">
                    Thực thể quan trọng ({entities.length})
                  </h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {entities.map((entity, idx) => (
                      <div
                        key={idx}
                        className="bg-purple-50 border border-purple-200 rounded-lg p-3 hover:bg-purple-100 transition"
                      >
                        <span className="text-purple-700 font-medium">{entity}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="text-center text-gray-500 py-8">
                  <p>Chưa có thực thể nào được trích xuất</p>
                  <p className="text-sm mt-2">Có thể do phương pháp Entity Chain Planning chưa được kích hoạt</p>
                </div>
              )}
            </div>
          )}

          {/* Tab: Fact Check */}
          {activeTab === "factcheck" && (
            <div className="space-y-4">
              {fact_check ? (
                <div className="space-y-4">
                  <div>
                    <h3 className="text-lg font-semibold mb-2 text-gray-800">Trạng thái kiểm chứng</h3>
                    <div
                      className={`p-4 rounded-lg font-semibold ${
                        fact_check.status === "CONSISTENT"
                          ? "bg-green-100 text-green-800 border border-green-300"
                          : "bg-red-100 text-red-800 border border-red-300"
                      }`}
                    >
                      {fact_check.status === "CONSISTENT" ? (
                        <span>✅ NHẤT QUÁN - Bản tóm tắt khớp với văn bản nguồn</span>
                      ) : (
                        <span>⚠️ KHÔNG NHẤT QUÁN - Có vấn đề về tính chính xác</span>
                      )}
                    </div>
                  </div>
                  {fact_check.issues && fact_check.issues.length > 0 && (
                    <div>
                      <h3 className="text-lg font-semibold mb-2 text-gray-800">
                        Các vấn đề phát hiện ({fact_check.issues.length})
                      </h3>
                      <div className="space-y-3">
                        {fact_check.issues.map((issue, idx) => (
                          <div
                            key={idx}
                            className="bg-red-50 border border-red-200 rounded-lg p-4 space-y-2"
                          >
                            <div>
                              <span className="font-semibold text-red-700">Đoạn trong tóm tắt:</span>
                              <p className="text-gray-700 bg-white p-2 rounded mt-1">
                                {issue.summary_span || "N/A"}
                              </p>
                            </div>
                            <div>
                              <span className="font-semibold text-red-700">Đoạn trong văn bản nguồn:</span>
                              <p className="text-gray-700 bg-white p-2 rounded mt-1">
                                {issue.source_span || "N/A"}
                              </p>
                            </div>
                            {issue.reason && (
                              <div>
                                <span className="font-semibold text-red-700">Lý do:</span>
                                <p className="text-gray-700 bg-white p-2 rounded mt-1">{issue.reason}</p>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-center text-gray-500 py-8">
                  <p>Chưa có kết quả kiểm chứng</p>
                  <p className="text-sm mt-2">Có thể do phương pháp FactCC chưa được kích hoạt</p>
                </div>
              )}
            </div>
          )}

          {/* Tab: Metadata */}
          {activeTab === "metadata" && (
            <div className="space-y-4">
              <h3 className="text-lg font-semibold mb-4 text-gray-800">Thông tin xử lý</h3>
              <div className="bg-gray-50 p-4 rounded-lg space-y-2">
                <div className="flex justify-between">
                  <span className="font-medium text-gray-700">Độ dài văn bản gốc:</span>
                  <span className="text-gray-600">{metadata.text_length?.toLocaleString() || "N/A"} ký tự</span>
                </div>
                <div className="flex justify-between">
                  <span className="font-medium text-gray-700">Độ dài bản tóm tắt:</span>
                  <span className="text-gray-600">{metadata.summary_length?.toLocaleString() || "N/A"} ký tự</span>
                </div>
                <div className="flex justify-between">
                  <span className="font-medium text-gray-700">Tỷ lệ nén:</span>
                  <span className="text-gray-600">
                    {metadata.text_length && metadata.summary_length
                      ? `${((1 - metadata.summary_length / metadata.text_length) * 100).toFixed(1)}%`
                      : "N/A"}
                  </span>
                </div>
                <div className="border-t pt-2 mt-2">
                  <h4 className="font-semibold text-gray-800 mb-2">Phương pháp đã sử dụng:</h4>
                  <div className="grid grid-cols-2 gap-2">
                    <div className={`p-2 rounded ${metadata.used_dancer ? "bg-green-100" : "bg-gray-200"}`}>
                      {metadata.used_dancer ? "✅" : "❌"} DANCER
                    </div>
                    <div className={`p-2 rounded ${metadata.used_entity_chain ? "bg-green-100" : "bg-gray-200"}`}>
                      {metadata.used_entity_chain ? "✅" : "❌"} Entity Chain
                    </div>
                    <div className={`p-2 rounded ${metadata.used_cod ? "bg-green-100" : "bg-gray-200"}`}>
                      {metadata.used_cod ? "✅" : "❌"} Chain of Density
                    </div>
                    <div className={`p-2 rounded ${metadata.used_structured ? "bg-green-100" : "bg-gray-200"}`}>
                      {metadata.used_structured ? "✅" : "❌"} Structured
                    </div>
                    <div className={`p-2 rounded ${metadata.used_fact_check ? "bg-green-100" : "bg-gray-200"}`}>
                      {metadata.used_fact_check ? "✅" : "❌"} FactCC
                    </div>
                  </div>
                </div>
              </div>
              <div className="mt-4">
                <h4 className="font-semibold text-gray-800 mb-2">Metadata đầy đủ (JSON):</h4>
                <pre className="bg-gray-900 text-green-400 p-4 rounded-lg overflow-x-auto text-sm">
                  {JSON.stringify(metadata, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

