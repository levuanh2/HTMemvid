import { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";  // npm i remark-breaks nếu chưa

export default function ChatArea({ selectedSources, sources = [] }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);

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

      const res = await fetch("http://localhost:5000/query", {
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

      const data = await res.json();
      
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
      <div className="p-4 border-b">
        <h1 className="text-xl font-bold">NotebookLM Clone</h1>
        <p className="text-sm text-gray-500">
          {selectedSources?.length
            ? `Đang dùng ${selectedSources.length} nguồn`
            : "Hỏi tự do hoặc chọn nguồn ở panel bên trái"}
        </p>
        {/* Note khi có index_ready sources */}
        {hasIndexReadySources && (
          <div className="mt-2 p-2 bg-yellow-50 border border-yellow-200 rounded text-xs text-yellow-800">
            💡 Một số tài liệu đang được hoàn thiện, câu trả lời có thể chưa đầy đủ.
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3 flex flex-col">
        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`p-3 rounded-lg break-words prose max-w-none md:max-w-2xl ${
              msg.role === "user"
                ? "bg-blue-100 self-end"
                : "bg-gray-100 self-start"
            }`}
          >
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkBreaks]}
              components={{
                p: ({ node, ...props }) => (
                  <p
                    className="prose prose-sm prose-indigo max-w-none break-words"
                    {...props}
                  />
                ),
              }}
            >
              {msg.content}
            </ReactMarkdown>
          </div>
        ))}

        {loading && <div className="italic text-gray-500">AI is thinking...</div>}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="p-4 border-t flex gap-2">
        <textarea
          rows={1}
          placeholder={loading ? "Waiting for AI..." : "Type your question here..."}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
          className="flex-1 border rounded px-3 py-2 resize-none disabled:bg-gray-100"
        />
        <button
          onClick={handleSend}
          disabled={loading}
          className={`px-4 py-2 rounded text-white ${
            loading
              ? "bg-gray-400 cursor-not-allowed"
              : "bg-blue-500 hover:bg-blue-600"
          }`}
        >
          Send
        </button>
      </div>
    </div>
  );
}
