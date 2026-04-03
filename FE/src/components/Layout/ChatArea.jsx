import { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";  // npm i remark-breaks nếu chưa

export default function ChatArea({ selectedSources, sources = [] }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const pollQueryJob = async (jobId, { intervalMs = 1000, timeoutMs = 5 * 60 * 1000 } = {}) => {
    const start = Date.now();
    // eslint-disable-next-line no-constant-condition
    while (true) {
      if (Date.now() - start > timeoutMs) {
        throw new Error("⚠️ Quá thời gian chờ phản hồi (timeout). Vui lòng thử lại.");
      }

      const res = await fetch(`/api/query-status/${encodeURIComponent(jobId)}`, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.error || `HTTP ${res.status}`);
      }

      const data = await res.json();
      if (data.status === "done") return data.result;
      if (data.status === "error") {
        throw new Error(data.error || "⚠️ Lỗi khi xử lý truy vấn.");
      }

      await new Promise((r) => setTimeout(r, intervalMs));
    }
  };

  // Check xem có source nào trong selectedSources đang ở status index_ready không
  const hasIndexReadySources = selectedSources?.length > 0 && sources.some(
    (src) =>
      src.status === "index_ready" &&
      selectedSources.includes(src.video_stem || src.video)
  );

  const handleSend = async () => {
    if (!input.trim() || loading) return;
    const userMsg = { role: "user", content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    console.log("Selected sources being sent:", selectedSources);
    try {
      const payloadSources = Array.isArray(selectedSources)
        ? selectedSources.map((s) => (typeof s === "string" ? s : s.name || s.id || s))
        : undefined;

      const res = await fetch(`/api/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          q: userMsg.content,
          sources: payloadSources && payloadSources.length ? payloadSources : null,
        }),
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.error || `HTTP ${res.status}`);
      }

      const startData = await res.json();
      const jobId = startData.job_id;
      if (!jobId) {
        throw new Error("⚠️ Không nhận được job_id từ server.");
      }

      const jobResult = await pollQueryJob(jobId, { intervalMs: 1000, timeoutMs: 5 * 60 * 1000 });
      const data = jobResult?.payload || {};

      // Nếu có processing_message, thêm vào content
      let aiContent = data.answer || "⚠️ No response";
      if (data.processing_message) {
        aiContent = `${data.processing_message}\n\n${aiContent}`;
      }

      const aiMsg = { role: "ai", content: aiContent };
      setMessages((prev) => [...prev, aiMsg]);
    } catch (err) {
      console.error("Query error:", err);
      const errorMsg = err.message?.includes("gặp lỗi")
        ? err.message
        : "⚠️ Lỗi khi gọi AI. Vui lòng thử lại.";
      setMessages((prev) => [...prev, { role: "ai", content: errorMsg }]);
    }
    setLoading(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Header */}
      <div className="p-4 border-b bg-white shadow-sm fade-in">
        <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 to-purple-600 bg-clip-text text-transparent">MemVid AI</h1>
        <p className="text-sm text-gray-600 mt-1">
          {selectedSources?.length
            ? `Đang sử dụng ${selectedSources.length} tài liệu`
            : "Đặt câu hỏi hoặc chọn tài liệu bên trái để bắt đầu"}
        </p>
        {/* Note khi có index_ready sources */}
        {hasIndexReadySources && (
          <div className="mt-3 p-2.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800 flex items-center gap-2 slide-up">
            <span className="text-base">💡</span>
            <span>Một số tài liệu đang được xử lý thêm, câu trả lời có thể chưa đầy đủ.</span>
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4 flex flex-col">
        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`p-4 rounded-2xl break-words prose max-w-none md:max-w-2xl shadow-sm slide-up ${msg.role === "user"
                ? "bg-gradient-to-br from-blue-500 to-blue-600 text-white self-end"
                : "bg-white border border-gray-200 self-start"
              }`}
          >
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkBreaks]}
              components={{
                p: ({ node, ...props }) => (
                  <p
                    className={`prose prose-sm max-w-none break-words ${msg.role === "user" ? "text-white" : "text-gray-800"
                      }`}
                    {...props}
                  />
                ),
              }}
            >
              {msg.content}
            </ReactMarkdown>
          </div>
        ))}

        {loading && (
          <div className="flex items-center gap-3 p-4 bg-gradient-to-r from-purple-50 to-blue-50 rounded-2xl border border-purple-200 shadow-sm slide-up pulse-glow">
            <div className="flex space-x-1">
              <div className="w-2 h-2 bg-purple-500 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></div>
              <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></div>
              <div className="w-2 h-2 bg-purple-500 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></div>
            </div>
            <span className="text-sm font-medium text-gray-700 thinking-dots">AI đang suy nghĩ</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-4 border-t bg-white shadow-lg flex gap-3">
        <textarea
          rows={1}
          placeholder={loading ? "Đang chờ AI trả lời..." : "Nhập câu hỏi của bạn tại đây..."}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
          className="flex-1 border-2 border-gray-300 rounded-xl px-4 py-3 resize-none disabled:bg-gray-50 focus:border-blue-500 focus:ring-2 focus:ring-blue-200 transition-all duration-200"
        />
        <button
          onClick={handleSend}
          disabled={loading}
          className={`px-6 py-3 rounded-xl font-medium transition-all duration-200 ${loading
              ? "bg-gray-300 cursor-not-allowed text-gray-500"
              : "bg-gradient-to-r from-blue-600 to-blue-500 text-white hover:from-blue-700 hover:to-blue-600 shadow-md hover:shadow-lg"
            }`}
        >
          {loading ? "Đang gửi..." : "Gửi"}
        </button>
      </div>
    </div>
  );
}
