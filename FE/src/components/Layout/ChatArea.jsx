import { useState, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { apiFetch, apiUrl } from "../../utils/api";

// ── Error helpers (logic unchanged) ────────────────────
const QUERY_SSE_ERR_FALLBACK = "Loi khi xu ly truy van. Vui long thu lai.";
function stripInvisible(s) { return String(s ?? "").replace(/[\u200B-\u200D\uFEFF\u2060\u180E]/g, ""); }
function ensureErrMsg(msg, fb = QUERY_SSE_ERR_FALLBACK) {
  const base = fb != null && String(fb).trim() !== "" ? String(fb).trim() : QUERY_SSE_ERR_FALLBACK;
  const t = stripInvisible(msg).trim();
  return t.length > 0 ? t : base;
}
function pickQueryDisplayText(jobResult, streamedText) {
  const st = streamedText != null ? String(streamedText).trim() : "";
  if (st) return st;
  const p = jobResult && typeof jobResult === "object" ? jobResult.payload : null;
  if (p && typeof p === "object") {
    if (p.answer != null && String(p.answer).trim()) return String(p.answer).trim();
    if (p.error != null && String(p.error).trim()) return String(p.error).trim();
  }
  if (jobResult && typeof jobResult === "object" && jobResult.answer != null && String(jobResult.answer).trim()) return String(jobResult.answer).trim();
  return "";
}
function sseErrorToMessage(raw, fallback = QUERY_SSE_ERR_FALLBACK) {
  const fb = ensureErrMsg(fallback, QUERY_SSE_ERR_FALLBACK);
  if (raw == null) return fb;
  if (typeof raw === "string") { const t = stripInvisible(raw).trim(); return t.length > 0 ? t : fb; }
  if (typeof raw === "number" && Number.isFinite(raw)) return String(raw);
  if (typeof raw === "boolean") return String(raw);
  return fb;
}

// ── Icons ───────────────────────────────────────────
const SendIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" className="w-[17px] h-[17px]">
    <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
  </svg>
);
const StopIcon = () => (
  <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4">
    <rect x="4" y="4" width="16" height="16" rx="3" />
  </svg>
);

// ── Node progress labels ─────────────────────────────
const NODE_LABELS = {
  CheckSources: "Đang kiểm tra tài liệu…",
  CacheLookup: "Đang kiểm tra cache…",
  RetrieveMemory: "Đang truy vấn trí nhớ…",
  RetrieveFAISS: "Đang tìm kiếm trong chỉ mục…",
  ContextBuilder: "Đang ghép ngữ cảnh…",
  GenerateAnswer: "GenerateAnswer…",
  Evaluate: "Đang đánh giá câu trả lời…",
  FeedbackLoop: "Đang tinh chỉnh…",
  Finalize: "Hoàn tất.",
  Queued: "Đang xếp hàng…",
};

// ── Pill action definitions ──────────────────────────
const PILL_ACTIONS = [
  { key: "main_idea", label: "Ý chính",       icon: "📌" },
  { key: "mindmap",   label: "Sơ đồ tư duy",  icon: "🧠" },
  { key: "summary",   label: "Tóm tắt",        icon: "📄" },
  { key: "audio",     label: "Âm thanh",       icon: "🔊" },
];

// ── Markdown components ──────────────────────────────
const mdComponents = {
  p: ({ node, ...props }) => <p className="mb-2 last:mb-0 leading-7 text-[14px] text-text-primary" {...props} />,
  code: ({ inline, children, ...props }) =>
    inline ? (
      <code className="bg-surface-elevated border border-border px-1.5 py-0.5 rounded text-[12px] text-brand font-mono" {...props}>{children}</code>
    ) : (
      <pre className="bg-gray-50 border border-border rounded-lg p-3 overflow-x-auto my-2">
        <code className="text-[12px] text-emerald-700 font-mono" {...props}>{children}</code>
      </pre>
    ),
  ul: ({ node, ...props }) => <ul className="pl-5 my-2 list-disc text-[14px] text-text-primary" {...props} />,
  ol: ({ node, ...props }) => <ol className="pl-5 my-2 list-decimal text-[14px] text-text-primary" {...props} />,
  li: ({ node, ...props }) => <li className="mb-1 leading-7" {...props} />,
  strong: ({ node, ...props }) => <strong className="text-text-primary font-bold" {...props} />,
  h1: ({ node, ...props }) => <h1 className="text-base font-bold my-2 text-text-primary" {...props} />,
  h2: ({ node, ...props }) => <h2 className="text-[15px] font-bold my-2 text-text-primary" {...props} />,
  h3: ({ node, ...props }) => <h3 className="text-[14px] font-semibold my-2 text-text-secondary" {...props} />,
  blockquote: ({ node, ...props }) => <blockquote className="border-l-4 border-brand/40 pl-3 my-2 text-text-secondary italic" {...props} />,
  table: ({ node, ...props }) => <div className="overflow-x-auto my-2"><table className="w-full text-[12px] border-collapse" {...props} /></div>,
  th: ({ node, ...props }) => <th className="bg-surface-elevated px-2 py-1 text-left text-text-primary border-b border-border font-semibold" {...props} />,
  td: ({ node, ...props }) => <td className="px-2 py-1 text-text-secondary border-b border-border" {...props} />,
};

export default function ChatArea({ selectedSources, sources = [], onOpenLeft, onOpenRight }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [activePill, setActivePill] = useState(null);
  const [sessionId] = useState(() => {
    try { if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID(); } catch {}
    return null;
  });
  const [jobProgress, setJobProgress] = useState(0);
  const [jobNode, setJobNode] = useState("");
  const [streamingPreview, setStreamingPreview] = useState("");
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const abortControllerRef = useRef(null);
  const cancelledRef = useRef(false);
  const eventSourceRef = useRef(null);
  const streamAccRef = useRef("");

  const QUERY_TIMEOUT_MS = (() => {
    const raw = import.meta.env.VITE_QUERY_TIMEOUT_MS;
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return 10 * 60 * 1000;
    return Math.max(30_000, Math.min(60 * 60 * 1000, Math.floor(n)));
  })();

  // ── SSE streaming (logic unchanged) ─────────────────
  const streamQueryJob = (jobId, { timeoutMs = QUERY_TIMEOUT_MS } = {}) =>
    new Promise((resolve, reject) => {
      const start = Date.now();
      streamAccRef.current = "";
      setStreamingPreview("");
      if (eventSourceRef.current) { try { eventSourceRef.current.close(); } catch {} }
      const es = new EventSource(apiUrl(`/query-stream/${encodeURIComponent(jobId)}`));
      eventSourceRef.current = es;

      const tick = setInterval(() => {
        if (cancelledRef.current) { clearInterval(tick); try { es.close(); } catch {} reject(new Error("CANCELLED")); }
        if (Date.now() - start > timeoutMs) {
          clearInterval(tick); try { es.close(); } catch {}
          reject(new Error(`⚠️ Quá thời gian chờ phản hồi (~${Math.round(timeoutMs / 1000)}s). Vui lòng thử lại.`));
        }
      }, 500);

      es.onmessage = (e) => {
        try {
          const d = JSON.parse(e.data);
          if (d.type === "token" && d.content) { streamAccRef.current += d.content; setStreamingPreview(streamAccRef.current); }
          const isStatus = d.type === "status" || d.type == null;
          if (isStatus) {
            if (typeof d.progress === "number") setJobProgress(Math.max(0, Math.min(100, d.progress)));
            if (d.current_node) setJobNode(String(d.current_node));
            if (d.status === "done") {
              clearInterval(tick); try { es.close(); } catch {}
              const streamed = streamAccRef.current; streamAccRef.current = ""; setStreamingPreview("");
              resolve({ result: d.result, streamed });
            } else if (d.status === "error") {
              clearInterval(tick); try { es.close(); } catch {}
              streamAccRef.current = ""; setStreamingPreview("");
              const HARD = "Loi query SSE (status=error). Xem log server.";
              let msg = HARD;
              try { msg = ensureErrMsg(sseErrorToMessage(d.error), HARD); } catch { msg = HARD; }
              reject(new Error(String(msg || "").trim() || HARD));
            }
          }
        } catch {}
      };

      es.onerror = () => {
        clearInterval(tick); try { es.close(); } catch {}
        streamAccRef.current = ""; setStreamingPreview("");
        reject(new Error("⚠️ Mất kết nối realtime (SSE). Vui lòng thử lại."));
      };
    });

  const stemBaseLoose = (s) => String(s || "").trim().toLowerCase().replace(/_\d{8}_\d{6}$/, "");
  const hasIndexReadySources =
    selectedSources?.length > 0 &&
    sources.some((src) => {
      if (src.status !== "index_ready") return false;
      const key = src.video_stem || src.video;
      if (!selectedSources.includes(key)) return false;
      const b = stemBaseLoose(key);
      return !sources.some((o) => o.status === "ready" && (stemBaseLoose(o.video_stem || o.video) === b || (o.video_stem || o.video) === key));
    });

  const handleCancel = () => {
    cancelledRef.current = true;
    abortControllerRef.current?.abort();
    try { eventSourceRef.current?.close(); } catch {}
    setJobProgress(0); setJobNode(""); setStreamingPreview(""); streamAccRef.current = ""; setLoading(false);
    setMessages((prev) => {
      const withoutLast = prev.slice(0, -1);
      return [...withoutLast, { role: "cancelled", content: "Đã huỷ gửi tin nhắn." }];
    });
  };

  // ── Send (logic unchanged) ───────────────────────────
  const handleSend = async () => {
    if (!input.trim() || loading) return;
    const userMsg = { role: "user", content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true); setJobProgress(0); setJobNode("Queued"); setStreamingPreview("");
    cancelledRef.current = false;
    abortControllerRef.current = new AbortController();
    if (textareaRef.current) textareaRef.current.style.height = "44px";
    try {
      const payloadSources = Array.isArray(selectedSources)
        ? selectedSources.map((s) => (typeof s === "string" ? s : s.name || s.id || s))
        : undefined;
      const res = await apiFetch(`/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q: userMsg.content, sources: payloadSources?.length ? payloadSources : null, session_id: sessionId || undefined }),
        signal: abortControllerRef.current.signal,
      });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `HTTP ${res.status}`); }
      const startData = await res.json();
      if (!startData.job_id) throw new Error("⚠️ Không nhận được job_id từ server.");
      const jobOutcome = await streamQueryJob(startData.job_id);
      if (cancelledRef.current) return;
      const jobResult = jobOutcome?.result ?? jobOutcome;
      const streamedText = jobOutcome?.streamed ?? "";
      const data = jobResult?.payload || {};
      let aiContent = pickQueryDisplayText(jobResult, streamedText) || "⚠️ No response";
      if (data.processing_message) aiContent = `${data.processing_message}\n\n${aiContent}`;
      setMessages((prev) => [...prev, { role: "ai", content: aiContent }]);
    } catch (err) {
      if (cancelledRef.current || err.name === "AbortError" || err.message === "CANCELLED") return;
      console.error("Query error:", err);
      setMessages((prev) => [...prev, { role: "ai", content: ensureErrMsg(err?.message, "Loi khi goi AI. Vui long thu lai.") }]);
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  };

  const handleKeyDown = (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } };
  const handleInput = (e) => {
    setInput(e.target.value);
    const ta = e.target;
    ta.style.height = "44px";
    ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
  };

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, loading]);

  // ── Render ─────────────────────────────────────────
  return (
    <div className="flex flex-col h-full min-h-0" style={{ background: 'var(--bg-base)' }}>

      {/* Warning bar */}
      {hasIndexReadySources && (
        <div className="px-4 py-2 text-[12px] flex items-center gap-2 border-b border-amber-200 bg-amber-50 text-amber-700 flex-shrink-0">
          <span>💡</span>
          <span>Một số tài liệu đang được xử lý thêm, câu trả lời có thể chưa đầy đủ.</span>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 min-h-0 overflow-y-auto px-6 py-6 flex flex-col gap-4">

        {/* Empty state */}
        {messages.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center h-full gap-4 text-center pb-16 animate-fadeUp">
            <div className="w-16 h-16 rounded-2xl flex items-center justify-center text-2xl"
              style={{ background: "linear-gradient(135deg, #4f46e5, #6366f1)", boxShadow: "0 8px 24px rgba(79,70,229,0.25)" }}>
              🧠
            </div>
            <div>
              <p className="text-[17px] font-bold text-text-primary mb-1">Bắt đầu trò chuyện</p>
              <p className="text-[14px] text-text-secondary max-w-[320px]">
                {selectedSources?.length
                  ? `Đang dùng ${selectedSources.length} tài liệu. Đặt câu hỏi bên dưới!`
                  : "Chọn tài liệu bên trái hoặc đặt câu hỏi để bắt đầu."}
              </p>
            </div>
            {selectedSources?.length > 0 && (
              <div className="flex flex-wrap gap-2 justify-center max-w-sm">
                {["Tóm tắt tài liệu này?", "Các ý chính là gì?", "Giải thích khái niệm quan trọng"].map((q) => (
                  <button
                    key={q}
                    onClick={() => { setInput(q); textareaRef.current?.focus(); }}
                    className="text-[12px] px-3 py-1.5 rounded-full border border-brand/30 text-brand hover:bg-brand/5 transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Messages list */}
        {messages.map((msg, idx) =>
          msg.role === "cancelled" ? (
            <div key={idx} className="self-start max-w-[85%] bg-amber-50 border border-amber-200 rounded-2xl rounded-bl-sm px-4 py-2.5 text-amber-700 text-[13px] flex items-center gap-2">
              <span>🚫</span><span>{msg.content}</span>
            </div>
          ) : msg.role === "user" ? (
            <div key={idx} className="self-end max-w-[75%] flex flex-col items-end gap-1">
              <div
                className="px-4 py-3 text-[14px] leading-relaxed text-white rounded-2xl rounded-br-sm transition-theme"
                style={{ background: "linear-gradient(135deg, #4f46e5, #6366f1)", boxShadow: "0 2px 10px rgba(79,70,229,0.25)" }}
              >
                {msg.content}
              </div>
            </div>
          ) : (
            <div key={idx} className="self-start max-w-[85%] flex items-start gap-3">
              {/* AI avatar */}
              <div className="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center text-white text-[12px] font-bold mt-0.5"
                style={{ background: "linear-gradient(135deg, #4f46e5, #6366f1)" }}>
                AI
              </div>
              <div
                className="flex-1 min-w-0 bg-surface-card border border-border rounded-2xl rounded-tl-sm px-4 py-3 text-[14px] text-text-primary transition-theme"
                style={{ boxShadow: 'var(--shadow-card)' }}
              >
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={mdComponents}>
                  {msg.content}
                </ReactMarkdown>
              </div>
            </div>
          )
        )}

        {/* Loading state */}
        {loading && (
          <div className="self-start max-w-[85%] flex items-start gap-3">
            <div className="w-8 h-8 rounded-full flex-shrink-0 flex items-center justify-center text-white text-[12px] font-bold mt-0.5"
              style={{ background: "linear-gradient(135deg, #4f46e5, #6366f1)" }}>
              AI
            </div>
            <div className="flex-1 min-w-0 bg-surface-card border border-border rounded-2xl rounded-tl-sm px-4 py-3 transition-theme"
              style={{ boxShadow: 'var(--shadow-card)' }}>
              {/* Progress indicator */}
              <div className="flex items-center gap-3 mb-2">
                <div className="flex gap-1">
                  {[0, 150, 300].map((delay) => (
                    <span key={delay} className="w-2 h-2 rounded-full bg-brand animate-bounce" data-delay={delay} />
                  ))}
                </div>
                <span className="text-[13px] text-text-secondary flex-1">
                  {jobNode ? NODE_LABELS[jobNode] || `Đang xử lý: ${jobNode}` : "Đang tổng hợp…"}
                </span>
                <span className="text-[12px] text-text-muted tabular-nums">{Math.round(jobProgress)}%</span>
              </div>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${Math.max(0, Math.min(100, jobProgress))}%` }} />
              </div>
              {/* Streaming preview */}
              {streamingPreview && (
                <div className="mt-3 pt-3 border-t border-border text-[13px] text-text-primary leading-relaxed max-h-[40vh] overflow-y-auto">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{streamingPreview}</ReactMarkdown>
                  <span className="inline-block w-0.5 h-4 bg-brand animate-pulse ml-0.5 align-middle rounded-sm" aria-hidden />
                </div>
              )}
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* ── BOTTOM AREA ── */}
      <div className="flex-shrink-0 border-t border-border transition-theme" style={{ background: 'var(--bg-sidebar)' }}>

        {/* Pill actions row */}
        <div className="px-4 pt-3 pb-2 flex gap-2 overflow-x-auto scrollbar-none">
          {PILL_ACTIONS.map((pill) => (
            <button
              key={pill.key}
              onClick={() => setActivePill(activePill === pill.key ? null : pill.key)}
              className={`pill-action flex-shrink-0 transition-theme ${activePill === pill.key ? "pill-action-active" : ""}`}
            >
              <span>{pill.icon}</span>
              <span>{pill.label}</span>
            </button>
          ))}
        </div>

        {/* Input row */}
        <div className="px-4 pb-4 flex items-end gap-3">
          <div className="flex-1 relative">
            <textarea
              ref={textareaRef}
              rows={1}
              placeholder={loading ? "Đang chờ AI trả lời…" : "Nhập câu hỏi..."}
              value={input}
              onChange={handleInput}
              onKeyDown={handleKeyDown}
              disabled={loading}
              className="w-full border border-border rounded-2xl px-4 py-3 text-[14px] text-text-primary placeholder:text-text-muted outline-none resize-none min-h-[44px] max-h-[140px] transition-all disabled:opacity-60"
              style={{ background: 'var(--bg-elevated)', lineHeight: 1.55, fontFamily: "inherit", boxShadow: "inset 0 1px 2px rgba(0,0,0,0.04)", color: 'var(--text-primary)' }}
              onFocus={(e) => { e.target.style.borderColor = "rgba(79,70,229,0.45)"; e.target.style.boxShadow = "0 0 0 3px rgba(79,70,229,0.08)"; }}
              onBlur={(e) => { e.target.style.borderColor = ""; e.target.style.boxShadow = "inset 0 1px 2px rgba(0,0,0,0.04)"; }}
            />
            {/* Edit icon inside input */}
            <span className="absolute right-3 bottom-3 text-text-muted pointer-events-none">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} className="w-4 h-4">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </span>
          </div>

          {/* Send / Cancel button */}
          {loading ? (
            <button
              onClick={handleCancel}
              className="w-11 h-11 rounded-full bg-red-50 border border-red-200 text-red-500 inline-flex items-center justify-center transition-all hover:bg-red-100 active:scale-95 flex-shrink-0"
              aria-label="Huỷ" title="Huỷ"
            >
              <StopIcon />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className={[
                "w-11 h-11 rounded-full inline-flex items-center justify-center text-white flex-shrink-0",
                "transition-all active:scale-95",
                !input.trim()
                  ? "bg-surface-elevated border border-border text-text-muted cursor-not-allowed"
                  : "cursor-pointer",
              ].join(" ")}
              style={input.trim() ? { background: "linear-gradient(135deg, #4f46e5, #6366f1)", boxShadow: "0 2px 10px rgba(79,70,229,0.3)" } : {}}
              aria-label="Gửi"
            >
              <SendIcon />
            </button>
          )}
        </div>
      </div>

      <style>{`
        .scrollbar-none::-webkit-scrollbar { display: none; }
        .scrollbar-none { -ms-overflow-style: none; scrollbar-width: none; }
        @media (min-width: 768px) { .md\\:hidden { display: none !important; } }
      `}</style>
    </div>
  );
}