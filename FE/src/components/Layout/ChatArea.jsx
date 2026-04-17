import { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { apiFetch } from "../../utils/api";

// ── Icons ──────────────────────────────────────────────
const SendIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" style={{ width: 18, height: 18 }}>
    <line x1="22" y1="2" x2="11" y2="13" />
    <polygon points="22 2 15 22 11 13 2 9 22 2" />
  </svg>
);
const StopIcon = () => (
  <svg viewBox="0 0 24 24" fill="currentColor" style={{ width: 16, height: 16 }}>
    <rect x="4" y="4" width="16" height="16" rx="3" />
  </svg>
);
const MenuIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" style={{ width: 20, height: 20 }}>
    <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
  </svg>
);
const ToolsIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" style={{ width: 20, height: 20 }}>
    <circle cx="12" cy="12" r="3" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14" />
  </svg>
);

// ── Styles ─────────────────────────────────────────────
const S = {
  header: {
    padding: "12px 16px",
    borderBottom: "1px solid #1e2d3d",
    background: "#111827",
    display: "flex",
    alignItems: "center",
    gap: 12,
    flexShrink: 0,
  },
  mobileBtn: {
    background: "transparent",
    border: "1px solid #374151",
    borderRadius: 8,
    padding: "6px 8px",
    cursor: "pointer",
    color: "#9ca3af",
    display: "flex",
    alignItems: "center",
    transition: "all 0.15s",
  },
  title: {
    fontSize: "1rem",
    fontWeight: 700,
    background: "linear-gradient(90deg, #60a5fa, #a78bfa)",
    WebkitBackgroundClip: "text",
    WebkitTextFillColor: "transparent",
    flex: 1,
    minWidth: 0,
  },
  badge: {
    fontSize: "0.7rem",
    padding: "2px 8px",
    borderRadius: 99,
    background: "#1e3a5f",
    color: "#60a5fa",
    fontWeight: 600,
    whiteSpace: "nowrap",
    flexShrink: 0,
  },
  messagesWrap: {
    flex: 1,
    minHeight: 0,
    overflowY: "auto",
    padding: "20px 16px",
    display: "flex",
    flexDirection: "column",
    gap: 12,
    scrollbarWidth: "thin",
    scrollbarColor: "#374151 transparent",
  },
  userBubble: {
    alignSelf: "flex-end",
    maxWidth: "80%",
    background: "linear-gradient(135deg, #2563eb, #4f46e5)",
    color: "#fff",
    borderRadius: "18px 18px 4px 18px",
    padding: "12px 16px",
    fontSize: "0.875rem",
    lineHeight: 1.6,
    boxShadow: "0 4px 12px rgba(37,99,235,0.3)",
  },
  aiBubble: {
    alignSelf: "flex-start",
    maxWidth: "85%",
    background: "#1f2937",
    border: "1px solid #374151",
    borderRadius: "18px 18px 18px 4px",
    padding: "12px 16px",
    fontSize: "0.875rem",
    lineHeight: 1.6,
    color: "#e5e7eb",
  },
  cancelledBubble: {
    alignSelf: "flex-start",
    maxWidth: "85%",
    background: "#1f1a00",
    border: "1px solid #92400e",
    borderRadius: "18px 18px 18px 4px",
    padding: "10px 14px",
    fontSize: "0.8rem",
    color: "#fbbf24",
    display: "flex",
    alignItems: "center",
    gap: 6,
  },
  thinkingBubble: {
    alignSelf: "flex-start",
    background: "#1f2937",
    border: "1px solid #374151",
    borderRadius: 18,
    padding: "12px 20px",
    display: "flex",
    alignItems: "center",
    gap: 10,
  },
  inputArea: {
    padding: "12px 16px",
    borderTop: "1px solid #1e2d3d",
    background: "#111827",
    display: "flex",
    gap: 8,
    alignItems: "flex-end",
    flexShrink: 0,
  },
  textarea: {
    flex: 1,
    background: "#1f2937",
    border: "1.5px solid #374151",
    borderRadius: 14,
    padding: "10px 14px",
    color: "#e5e7eb",
    fontSize: "0.875rem",
    lineHeight: 1.5,
    resize: "none",
    outline: "none",
    transition: "border-color 0.2s",
    fontFamily: "inherit",
    minHeight: 44,
    maxHeight: 140,
  },
  sendBtn: {
    background: "linear-gradient(135deg, #2563eb, #4f46e5)",
    border: "none",
    borderRadius: 12,
    padding: "10px 16px",
    cursor: "pointer",
    color: "#fff",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    height: 44,
    width: 44,
    transition: "all 0.2s",
    boxShadow: "0 4px 12px rgba(37,99,235,0.3)",
  },
  cancelBtn: {
    background: "#7f1d1d",
    border: "1px solid #991b1b",
    borderRadius: 12,
    padding: "10px 16px",
    cursor: "pointer",
    color: "#fca5a5",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
    height: 44,
    width: 44,
    transition: "all 0.2s",
    boxShadow: "0 4px 12px rgba(127,29,29,0.4)",
  },
  sendBtnDisabled: {
    background: "#374151",
    cursor: "not-allowed",
    boxShadow: "none",
    color: "#6b7280",
  },
  warningBar: {
    margin: "0 16px 8px",
    padding: "8px 12px",
    background: "#1f1a00",
    border: "1px solid #92400e",
    borderRadius: 10,
    fontSize: "0.75rem",
    color: "#fbbf24",
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexShrink: 0,
  },
  emptyState: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    gap: 16,
    color: "#6b7280",
    padding: "40px 24px",
    textAlign: "center",
  },
};

export default function ChatArea({ selectedSources, sources = [], onOpenLeft, onOpenRight }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  // AbortController ref để cancel request
  const abortControllerRef = useRef(null);
  const cancelledRef = useRef(false);

  const pollQueryJob = async (jobId, { intervalMs = 1000, timeoutMs = 5 * 60 * 1000 } = {}) => {
    const start = Date.now();
    while (true) {
      // Kiểm tra nếu đã bị cancel
      if (cancelledRef.current) throw new Error("CANCELLED");
      if (Date.now() - start > timeoutMs) throw new Error("⚠️ Quá thời gian chờ phản hồi (timeout). Vui lòng thử lại.");
      const res = await apiFetch(`/api/query-status/${encodeURIComponent(jobId)}`, {
        method: "GET",
        headers: { "Content-Type": "application/json" },
        signal: abortControllerRef.current?.signal,
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `HTTP ${res.status}`); }
      const data = await res.json();
      if (data.status === "done") return data.result;
      if (data.status === "error") throw new Error(data.error || "⚠️ Lỗi khi xử lý truy vấn.");
      await new Promise((r) => setTimeout(r, intervalMs));
    }
  };

  const hasIndexReadySources = selectedSources?.length > 0 && sources.some(
    (src) => src.status === "index_ready" && selectedSources.includes(src.video_stem || src.video)
  );

  const handleCancel = () => {
    cancelledRef.current = true;
    abortControllerRef.current?.abort();
    setLoading(false);
    // Xóa tin nhắn user vừa gửi (tin cuối) và thêm thông báo đã huỷ
    setMessages((prev) => {
      const withoutLast = prev.slice(0, -1); // bỏ tin user vừa gửi
      return [...withoutLast, { role: "cancelled", content: "Đã huỷ gửi tin nhắn." }];
    });
  };

  const handleSend = async () => {
    if (!input.trim() || loading) return;
    const userMsg = { role: "user", content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    cancelledRef.current = false;
    abortControllerRef.current = new AbortController();
    if (textareaRef.current) textareaRef.current.style.height = "44px";
    try {
      const payloadSources = Array.isArray(selectedSources)
        ? selectedSources.map((s) => (typeof s === "string" ? s : s.name || s.id || s))
        : undefined;
      const res = await apiFetch(`/api/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q: userMsg.content, sources: payloadSources?.length ? payloadSources : null }),
        signal: abortControllerRef.current.signal,
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `HTTP ${res.status}`); }
      const startData = await res.json();
      if (!startData.job_id) throw new Error("⚠️ Không nhận được job_id từ server.");
      const jobResult = await pollQueryJob(startData.job_id);
      if (cancelledRef.current) return; // đã cancel trong lúc poll
      const data = jobResult?.payload || {};
      let aiContent = data.answer || "⚠️ No response";
      if (data.processing_message) aiContent = `${data.processing_message}\n\n${aiContent}`;
      setMessages((prev) => [...prev, { role: "ai", content: aiContent }]);
    } catch (err) {
      if (cancelledRef.current || err.name === "AbortError" || err.message === "CANCELLED") {
        // Đã xử lý trong handleCancel, không làm gì thêm
        return;
      }
      console.error("Query error:", err);
      const errorMsg = err.message?.includes("gặp lỗi") ? err.message : "⚠️ Lỗi khi gọi AI. Vui lòng thử lại.";
      setMessages((prev) => [...prev, { role: "ai", content: errorMsg }]);
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
  };

  const handleInput = (e) => {
    setInput(e.target.value);
    const ta = e.target;
    ta.style.height = "44px";
    ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
  };

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, loading]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* Header */}
      <header style={S.header}>
        <button style={S.mobileBtn} onClick={onOpenLeft} className="md:hidden" aria-label="Mở danh sách tài liệu">
          <MenuIcon />
        </button>

        <div style={S.title}>MemVid AI</div>

        {selectedSources?.length > 0 && (
          <span style={S.badge}>📚 {selectedSources.length} tài liệu</span>
        )}

        <button style={S.mobileBtn} onClick={onOpenRight} className="md:hidden" aria-label="Mở công cụ">
          <ToolsIcon />
        </button>
      </header>

      {/* Warning bar */}
      {hasIndexReadySources && (
        <div style={S.warningBar}>
          <span>💡</span>
          <span>Một số tài liệu đang được xử lý thêm, câu trả lời có thể chưa đầy đủ.</span>
        </div>
      )}

      {/* Messages */}
      <div style={S.messagesWrap}>
        {messages.length === 0 && !loading && (
          <div style={S.emptyState}>
            <div style={{ fontSize: "2.5rem" }}>🧠</div>
            <div>
              <p style={{ color: "#9ca3af", fontSize: "1rem", fontWeight: 600, marginBottom: 6 }}>Bắt đầu trò chuyện</p>
              <p style={{ color: "#6b7280", fontSize: "0.8rem" }}>
                {selectedSources?.length
                  ? `Đang dùng ${selectedSources.length} tài liệu. Đặt câu hỏi bên dưới!`
                  : "Chọn tài liệu bên trái hoặc đặt câu hỏi để bắt đầu."}
              </p>
            </div>
          </div>
        )}

        {messages.map((msg, idx) =>
          msg.role === "cancelled" ? (
            <div key={idx} style={S.cancelledBubble}>
              <span>🚫</span>
              <span>{msg.content}</span>
            </div>
          ) : (
            <div key={idx} style={msg.role === "user" ? S.userBubble : S.aiBubble}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm, remarkBreaks]}
                components={{
                  p: ({ node, ...props }) => (
                    <p style={{ margin: "0 0 8px", color: msg.role === "user" ? "#fff" : "#e5e7eb", fontSize: "0.875rem", lineHeight: 1.6 }} {...props} />
                  ),
                  code: ({ inline, children, ...props }) =>
                    inline ? (
                      <code style={{ background: "#374151", padding: "1px 5px", borderRadius: 4, fontSize: "0.8rem", color: "#93c5fd" }} {...props}>{children}</code>
                    ) : (
                      <pre style={{ background: "#0f172a", border: "1px solid #374151", borderRadius: 8, padding: "12px", overflowX: "auto", margin: "8px 0" }}>
                        <code style={{ fontSize: "0.8rem", color: "#86efac" }} {...props}>{children}</code>
                      </pre>
                    ),
                  ul: ({ node, ...props }) => <ul style={{ paddingLeft: 20, margin: "6px 0", color: msg.role === "user" ? "#fff" : "#e5e7eb" }} {...props} />,
                  ol: ({ node, ...props }) => <ol style={{ paddingLeft: 20, margin: "6px 0", color: msg.role === "user" ? "#fff" : "#e5e7eb" }} {...props} />,
                  li: ({ node, ...props }) => <li style={{ marginBottom: 4, fontSize: "0.875rem", lineHeight: 1.6 }} {...props} />,
                  strong: ({ node, ...props }) => <strong style={{ color: msg.role === "user" ? "#fff" : "#93c5fd", fontWeight: 700 }} {...props} />,
                  h1: ({ node, ...props }) => <h1 style={{ fontSize: "1.1rem", fontWeight: 700, margin: "8px 0 4px", color: msg.role === "user" ? "#fff" : "#e5e7eb" }} {...props} />,
                  h2: ({ node, ...props }) => <h2 style={{ fontSize: "1rem", fontWeight: 700, margin: "8px 0 4px", color: msg.role === "user" ? "#fff" : "#e5e7eb" }} {...props} />,
                  h3: ({ node, ...props }) => <h3 style={{ fontSize: "0.9rem", fontWeight: 600, margin: "6px 0 2px", color: msg.role === "user" ? "#fff" : "#a5b4fc" }} {...props} />,
                  blockquote: ({ node, ...props }) => <blockquote style={{ borderLeft: "3px solid #4f46e5", paddingLeft: 12, margin: "8px 0", color: "#9ca3af", fontStyle: "italic" }} {...props} />,
                  table: ({ node, ...props }) => <div style={{ overflowX: "auto", margin: "8px 0" }}><table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.8rem" }} {...props} /></div>,
                  th: ({ node, ...props }) => <th style={{ background: "#374151", padding: "6px 10px", textAlign: "left", color: "#e5e7eb", borderBottom: "1px solid #4b5563" }} {...props} />,
                  td: ({ node, ...props }) => <td style={{ padding: "5px 10px", borderBottom: "1px solid #374151", color: "#d1d5db" }} {...props} />,
                }}
              >
                {msg.content}
              </ReactMarkdown>
            </div>
          )
        )}

        {loading && (
          <div style={S.thinkingBubble}>
            <div style={{ display: "flex", gap: 5 }}>
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  style={{
                    width: 7, height: 7, borderRadius: "50%",
                    background: delay === 150 ? "#60a5fa" : "#818cf8",
                    animation: "bounce 1.2s infinite ease-in-out",
                    animationDelay: `${delay}ms`,
                    display: "inline-block",
                  }}
                />
              ))}
            </div>
            <span style={{ fontSize: "0.8rem", color: "#9ca3af" }}>AI đang suy nghĩ…</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div style={S.inputArea}>
        <textarea
          ref={textareaRef}
          rows={1}
          placeholder={loading ? "Đang chờ AI trả lời…" : "Nhập câu hỏi (Enter để gửi, Shift+Enter xuống dòng)"}
          value={input}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          disabled={loading}
          style={{
            ...S.textarea,
            borderColor: input ? "#4f46e5" : "#374151",
            opacity: loading ? 0.6 : 1,
          }}
          onFocus={(e) => { e.target.style.borderColor = "#4f46e5"; e.target.style.boxShadow = "0 0 0 3px rgba(79,70,229,0.15)"; }}
          onBlur={(e) => { e.target.style.borderColor = input ? "#4f46e5" : "#374151"; e.target.style.boxShadow = "none"; }}
        />

        {/* Nút Cancel khi đang loading, nút Send khi bình thường */}
        {loading ? (
          <button
            onClick={handleCancel}
            style={S.cancelBtn}
            aria-label="Huỷ gửi"
            title="Huỷ"
            onMouseEnter={(e) => { e.currentTarget.style.background = "#991b1b"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "#7f1d1d"; }}
          >
            <StopIcon />
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!input.trim()}
            style={{
              ...S.sendBtn,
              ...(!input.trim() ? S.sendBtnDisabled : {}),
            }}
            aria-label="Gửi"
          >
            <SendIcon />
          </button>
        )}
      </div>

      <style>{`
        @keyframes bounce {
          0%, 80%, 100% { transform: translateY(0); }
          40% { transform: translateY(-6px); }
        }
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        .md\\:hidden { display: flex; }
        @media (min-width: 768px) { .md\\:hidden { display: none !important; } }
      `}</style>
    </div>
  );
}