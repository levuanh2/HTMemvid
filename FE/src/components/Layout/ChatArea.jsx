import { useState, useEffect, useRef, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { apiFetch, apiUrl, clearConversationContext, deleteConversation } from "../../utils/api";
import { newConversationId } from "../../utils/conversation";
import { Icon } from "../ui/Icon";
import { nodeLabel, processCitations, parseCiteHref, normStem } from "../../utils/evidence";

// ── Error helpers (logic unchanged) ────────────────────
const QUERY_SSE_ERR_FALLBACK = "Loi khi xu ly truy van. Vui long thu lai.";
const INVISIBLE_RE = new RegExp("[" + [0x200B, 0x200C, 0x200D, 0xFEFF, 0x2060, 0x180E].map((c) => String.fromCharCode(c)).join("") + "]", "g");
function stripInvisible(s) { return String(s ?? "").replace(INVISIBLE_RE, ""); }
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

// ── Quick question chips (fill the composer; functional, not decorative) ──
const SUGGESTIONS = [
  "Các ý chính của tài liệu là gì?",
  "Tóm tắt nội dung chính.",
  "Giải thích khái niệm quan trọng nhất.",
];

// ── Markdown components for the answer prose ──────────
// `a` handles citation chips ([n](#cite:stem:chunkId)); everything else is
// styled for serif reading.
function makeMdComponents({ highlight, onHighlight }) {
  return {
    p: ({ node, ...p }) => <p className="mb-2.5 last:mb-0 leading-[1.72] text-[15px] text-text-primary" {...p} />,
    code: ({ node, inline, children, ...props }) =>
      inline ? (
        <code className="bg-surface-elevated border border-border px-1.5 py-0.5 rounded text-[12.5px] font-mono text-text-secondary" {...props}>{children}</code>
      ) : (
        <pre className="bg-surface-elevated border border-border rounded-[7px] p-3 overflow-x-auto my-2.5">
          <code className="text-[12.5px] font-mono text-text-secondary" {...props}>{children}</code>
        </pre>
      ),
    ul: ({ node, ...p }) => <ul className="pl-5 my-2.5 list-disc marker:text-slate text-[15px] text-text-primary" {...p} />,
    ol: ({ node, ...p }) => <ol className="pl-5 my-2.5 list-decimal marker:text-slate text-[15px] text-text-primary" {...p} />,
    li: ({ node, ...p }) => <li className="mb-1.5 leading-[1.7]" {...p} />,
    strong: ({ node, ...p }) => <strong className="text-text-primary font-semibold" {...p} />,
    em: ({ node, ...p }) => <em className="italic" {...p} />,
    h1: ({ node, ...p }) => <h1 className="font-display text-[19px] font-semibold my-3 text-text-primary" {...p} />,
    h2: ({ node, ...p }) => <h2 className="font-display text-[17px] font-semibold my-2.5 text-text-primary" {...p} />,
    h3: ({ node, ...p }) => <h3 className="font-display text-[15px] font-semibold my-2 text-text-secondary" {...p} />,
    blockquote: ({ node, ...p }) => <blockquote className="border-l-2 border-brand/50 pl-3.5 my-2.5 text-text-secondary italic" {...p} />,
    table: ({ node, ...p }) => <div className="overflow-x-auto my-2.5"><table className="w-full text-[13px] border-collapse font-body" {...p} /></div>,
    th: ({ node, ...p }) => <th className="bg-surface-elevated px-2.5 py-1.5 text-left text-text-primary border border-border font-semibold" {...p} />,
    td: ({ node, ...p }) => <td className="px-2.5 py-1.5 text-text-secondary border border-border" {...p} />,
    a: ({ node, href, children, ...props }) => {
      const cite = parseCiteHref(href);
      if (cite) {
        const active = highlight && normStem(highlight.stem) === normStem(cite.stem) && String(highlight.chunkId) === String(cite.chunkId);
        return (
          <sup
            className={`cite-chip ${active ? "cite-chip--active" : ""}`}
            role="button"
            tabIndex={0}
            title={`Nguồn: ${cite.stem} · đoạn ${cite.chunkId}`}
            onMouseEnter={() => onHighlight?.(cite)}
            onMouseLeave={() => onHighlight?.(null)}
            onClick={() => onHighlight?.(cite)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onHighlight?.(cite); } }}
          >
            {children}
          </sup>
        );
      }
      return <a href={href} target="_blank" rel="noreferrer" className="text-brand underline underline-offset-2" {...props}>{children}</a>;
    },
  };
}

// ── Answer block (memo-light): runs citation pass once per content ──
function AnswerProse({ content, mdComponents }) {
  const { md } = useMemo(() => processCitations(content), [content]);
  return (
    <div className="font-reading">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={mdComponents}>{md}</ReactMarkdown>
    </div>
  );
}

export default function ChatArea({ selectedSources, sources = [], onEvidence, highlight, onHighlight, onOpenLeft, onOpenRight, askAboutDraft }) {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState(() => newConversationId());
  // Conversation controls: kebab menu, a transient notice, and whether the AI is
  // currently using prior turns as context (hidden right after Clear context).
  const [menuOpen, setMenuOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [contextCleared, setContextCleared] = useState(false);
  const [jobProgress, setJobProgress] = useState(0);
  const [jobNode, setJobNode] = useState("");
  const [seenNodes, setSeenNodes] = useState([]);
  const [streamingPreview, setStreamingPreview] = useState("");
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const abortControllerRef = useRef(null);
  const cancelledRef = useRef(false);
  const eventSourceRef = useRef(null);
  const streamAccRef = useRef("");

  const mdComponents = useMemo(() => makeMdComponents({ highlight, onHighlight }), [highlight, onHighlight]);

  const QUERY_TIMEOUT_MS = (() => {
    const raw = import.meta.env.VITE_QUERY_TIMEOUT_MS;
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return 10 * 60 * 1000;
    return Math.max(30_000, Math.min(60 * 60 * 1000, Math.floor(n)));
  })();

  // ── SSE streaming (logic unchanged; + node trace) ───
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
          reject(new Error(`Quá thời gian chờ phản hồi (~${Math.round(timeoutMs / 1000)}s). Vui lòng thử lại.`));
        }
      }, 500);

      es.onmessage = (e) => {
        try {
          const d = JSON.parse(e.data);
          if (d.type === "token" && d.content) { streamAccRef.current += d.content; setStreamingPreview(streamAccRef.current); }
          const isStatus = d.type === "status" || d.type == null;
          if (isStatus) {
            if (typeof d.progress === "number") setJobProgress(Math.max(0, Math.min(100, d.progress)));
            if (d.current_node) {
              const k = String(d.current_node);
              setJobNode(k);
              setSeenNodes((prev) => (prev[prev.length - 1] === k || prev.includes(k) ? prev : [...prev, k]));
            }
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
        reject(new Error("Mất kết nối realtime (SSE). Vui lòng thử lại."));
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

  const resetJobState = () => { setJobProgress(0); setJobNode(""); setSeenNodes([]); setStreamingPreview(""); streamAccRef.current = ""; };

  const handleCancel = () => {
    cancelledRef.current = true;
    abortControllerRef.current?.abort();
    try { eventSourceRef.current?.close(); } catch {}
    resetJobState(); setLoading(false);
    setMessages((prev) => {
      const withoutLast = prev.slice(0, -1);
      return [...withoutLast, { role: "cancelled", content: "Đã huỷ truy vấn." }];
    });
  };

  // ── Conversation controls ───────────────────────────
  // Guarded while a query streams so we never rotate/clear mid-answer.
  const showNotice = (msg) => { setNotice(msg); setTimeout(() => setNotice(""), 4000); };

  const handleNewChat = () => {
    if (loading) return;
    setMenuOpen(false);
    setSessionId(newConversationId());   // fresh thread, empty context (keeps selected sources)
    setMessages([]);
    setContextCleared(false);
    onEvidence?.(null); onHighlight?.(null);
    showNotice("Đã mở cuộc trò chuyện mới.");
  };

  const handleClearContext = async () => {
    if (loading) return;
    setMenuOpen(false);
    setContextCleared(true);            // messages stay visible; AI stops using old turns
    try { await clearConversationContext(sessionId); } catch {}
    showNotice("Đã xóa ngữ cảnh. Câu hỏi tiếp theo sẽ được xử lý như chủ đề mới.");
  };

  const handleDeleteHistory = async () => {
    if (loading) return;
    setMenuOpen(false);
    if (!window.confirm("Xóa toàn bộ lịch sử chat của cuộc trò chuyện này? Hành động này không thể hoàn tác.")) return;
    try { await deleteConversation(sessionId); } catch {}
    setMessages([]);
    setContextCleared(false);
    onEvidence?.(null); onHighlight?.(null);
    showNotice("Đã xóa lịch sử chat.");
  };

  const usingContext = !contextCleared && messages.some((m) => m.role === "ai");

  // ── Send (logic unchanged; + evidence capture) ──────
  const handleSend = async () => {
    if (!input.trim() || loading) return;
    setContextCleared(false);          // a new question resumes using conversation context
    const userMsg = { role: "user", content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true); resetJobState(); setJobNode("Queued"); setSeenNodes(["Queued"]);
    onHighlight?.(null);
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
      if (!startData.job_id) throw new Error("Không nhận được job_id từ server.");
      const jobOutcome = await streamQueryJob(startData.job_id);
      if (cancelledRef.current) return;
      const jobResult = jobOutcome?.result ?? jobOutcome;
      const streamedText = jobOutcome?.streamed ?? "";
      const data = jobResult?.payload || {};
      let aiContent = pickQueryDisplayText(jobResult, streamedText) || "Không có phản hồi.";
      if (data.processing_message) aiContent = `${data.processing_message}\n\n${aiContent}`;
      const evidence = {
        sources: Array.isArray(data.sources) ? data.sources : [],
        chunks: Array.isArray(data.chunks) ? data.chunks : [],
      };
      setMessages((prev) => [...prev, { role: "ai", content: aiContent, evidence }]);
      onEvidence?.(evidence);
    } catch (err) {
      if (cancelledRef.current || err.name === "AbortError" || err.message === "CANCELLED") return;
      console.error("Query error:", err);
      setMessages((prev) => [...prev, { role: "ai", content: ensureErrMsg(err?.message, "Loi khi goi AI. Vui long thu lai."), evidence: null }]);
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
  const useSuggestion = (q) => { setInput(q); textareaRef.current?.focus(); };

  // Task 16 — "Hỏi về đoạn này" (mindmap EvidenceDrawer) prefills the composer.
  // Keyed on the whole draft object (not just `.text`) so asking about the
  // same snippet twice in a row still re-focuses/re-fills.
  useEffect(() => {
    if (!askAboutDraft?.text) return;
    setInput(askAboutDraft.text);
    const ta = textareaRef.current;
    if (ta) {
      ta.style.height = "44px";
      ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
      ta.focus();
    }
  }, [askAboutDraft]);

  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, loading, streamingPreview]);

  const apparatusSteps = seenNodes.length ? seenNodes : ["Queued"];

  // ── Render ─────────────────────────────────────────
  return (
    <div className="flex flex-col h-full min-h-0" style={{ background: "var(--bg-base)" }}>

      {/* Conversation toolbar: context indicator + controls (New chat / Clear / Delete) */}
      <div className="flex items-center gap-2 px-5 sm:px-8 h-9 border-b flex-shrink-0"
        style={{ borderColor: "var(--border-color)", background: "var(--bg-sidebar)" }}>
        {usingContext && (
          <span className="inline-flex items-center gap-1.5 text-[11px] font-mono text-text-muted">
            <Icon name="MessageSquare" size={12} className="text-brand" />
            Đang dùng ngữ cảnh cuộc trò chuyện
          </span>
        )}
        <div className="flex-1" />
        <button
          onClick={handleNewChat} disabled={loading}
          className="pill-action !py-1 !text-[12px] disabled:opacity-40"
          title="Bắt đầu cuộc trò chuyện mới">
          <Icon name="Plus" size={13} /> Chat mới
        </button>
        <div className="relative">
          <button
            onClick={() => setMenuOpen((v) => !v)} disabled={loading}
            className="icon-btn w-8 h-8 disabled:opacity-40"
            aria-label="Tùy chọn cuộc trò chuyện" aria-expanded={menuOpen}>
            <Icon name="MoreVertical" size={16} />
          </button>
          {menuOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} aria-hidden />
              <div className="absolute right-0 top-9 z-20 min-w-[190px] rounded-[8px] border py-1 shadow-lg"
                style={{ borderColor: "var(--border-color)", background: "var(--bg-elevated)" }}>
                <button onClick={handleClearContext} className="menu-item">
                  <Icon name="Eraser" size={14} /> Xóa ngữ cảnh
                </button>
                <button onClick={handleDeleteHistory} className="menu-item menu-item--danger">
                  <Icon name="Trash2" size={14} /> Xóa lịch sử chat
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Transient feedback (New chat / Clear context / Delete history) */}
      {notice && (
        <div className="px-5 sm:px-8 py-1.5 text-[12px] flex items-center gap-2 border-b flex-shrink-0"
          style={{ borderColor: "var(--border-color)", background: "color-mix(in srgb, var(--brand) 8%, transparent)", color: "var(--text-secondary)" }}>
          <Icon name="Info" size={13} className="text-brand" />
          <span>{notice}</span>
        </div>
      )}

      {/* Notice: sources still indexing */}
      {hasIndexReadySources && (
        <div className="px-5 py-2 text-[12px] flex items-center gap-2 border-b flex-shrink-0"
          style={{ borderColor: "var(--border-color)", background: "color-mix(in srgb, var(--warn) 10%, transparent)", color: "var(--warn)" }}>
          <Icon name="Info" size={14} />
          <span>Một số tài liệu vẫn đang lập chỉ mục — câu trả lời có thể chưa đầy đủ.</span>
        </div>
      )}

      {/* Reading session */}
      <div className="flex-1 min-h-0 overflow-y-auto px-5 sm:px-8 py-7 flex flex-col gap-6">

        {/* Empty state — the reading-room thesis */}
        {messages.length === 0 && !loading && (
          <div className="flex flex-col items-center justify-center h-full text-center pb-16 animate-fadeUp max-w-[440px] mx-auto">
            <div className="font-mono text-[11px] tracking-[0.2em] uppercase text-text-muted mb-4">Phòng đọc</div>
            <h1 className="font-display text-[26px] sm:text-[30px] leading-[1.2] font-semibold text-text-primary mb-3">
              Hỏi tài liệu của bạn — <span className="text-brand">kèm dẫn chứng</span>.
            </h1>
            <p className="font-reading text-[15px] leading-[1.7] text-text-secondary mb-6">
              Mỗi câu trả lời được truy hồi từ tài liệu đã chọn và gắn nguồn ở lề phải. Chọn tài liệu bên trái, rồi đặt câu hỏi.
            </p>
            {selectedSources?.length > 0 ? (
              <div className="flex flex-wrap gap-2 justify-center">
                {SUGGESTIONS.map((q) => (
                  <button key={q} onClick={() => useSuggestion(q)} className="pill-action">{q}</button>
                ))}
              </div>
            ) : (
              <button onClick={onOpenLeft} className="md:hidden pill-action">
                <Icon name="Menu" size={14} /> Mở thư mục nguồn
              </button>
            )}
          </div>
        )}

        {/* Messages */}
        {messages.map((msg, idx) =>
          msg.role === "cancelled" ? (
            <div key={idx} className="self-center text-[12px] font-mono text-text-muted flex items-center gap-2 py-1">
              <Icon name="Ban" size={13} /> {msg.content}
            </div>
          ) : msg.role === "user" ? (
            <div key={idx} className="self-end max-w-[78%]">
              <div className="px-4 py-2.5 text-[14.5px] leading-relaxed rounded-[10px] rounded-br-[3px] border transition-theme"
                style={{ background: "var(--bg-elevated)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}>
                {msg.content}
              </div>
            </div>
          ) : (
            <div key={idx} className="self-start w-full max-w-[760px] flex flex-col gap-2 animate-fadeUp">
              <div className="flex items-center gap-2 text-[11px] font-mono uppercase tracking-[0.14em] text-text-muted">
                <Icon name="BookOpen" size={13} className="text-brand" /> Trả lời
                {msg.evidence?.sources?.length ? (
                  <span className="text-text-muted">· {msg.evidence.sources.length} nguồn</span>
                ) : null}
              </div>
              <div className="text-text-primary">
                <AnswerProse content={msg.content} mdComponents={mdComponents} />
              </div>
            </div>
          )
        )}

        {/* Loading — the retrieval apparatus */}
        {loading && (
          <div className="self-start w-full max-w-[760px] animate-fadeUp">
            <div className="surface-card !p-4">
              <div className="flex items-center justify-between text-[11px] font-mono uppercase tracking-[0.14em] text-text-muted mb-3">
                <span>Bộ máy truy hồi</span>
                <span className="tabular-nums text-text-secondary">{Math.round(jobProgress)}%</span>
              </div>
              <div className="flex flex-col">
                {apparatusSteps.map((key, i) => {
                  const isActive = i === apparatusSteps.length - 1;
                  return (
                    <div key={`${key}-${i}`} className="apparatus-step">
                      <span className={`apparatus-dot ${isActive ? "apparatus-dot--active" : "apparatus-dot--done"}`} />
                      <span className={`text-[12.5px] ${isActive ? "text-text-primary font-medium" : "text-text-secondary"}`}>{nodeLabel(key)}</span>
                    </div>
                  );
                })}
              </div>
              {streamingPreview && (
                <div className="mt-3 pt-3 border-t border-border font-reading text-[14.5px] text-text-primary leading-[1.7] max-h-[42vh] overflow-y-auto">
                  <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{streamingPreview}</ReactMarkdown>
                  <span className="inline-block w-0.5 h-4 bg-brand animate-pulse ml-0.5 align-middle rounded-sm" aria-hidden />
                </div>
              )}
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* ── COMPOSER ── */}
      <div className="flex-shrink-0 border-t border-border transition-theme" style={{ background: "var(--bg-sidebar)" }}>
        {/* Follow-up suggestions */}
        {messages.length > 0 && !loading && (
          <div className="px-4 sm:px-8 pt-3 pb-1 flex gap-2 overflow-x-auto scrollbar-none">
            {SUGGESTIONS.map((q) => (
              <button key={q} onClick={() => useSuggestion(q)} className="pill-action flex-shrink-0">{q}</button>
            ))}
          </div>
        )}

        <div className="px-4 sm:px-8 py-4 flex items-end gap-3">
          <div className="flex-1 relative">
            <textarea
              ref={textareaRef}
              rows={1}
              placeholder={loading ? "Đang chờ phản hồi…" : "Đặt câu hỏi về tài liệu đã chọn…"}
              value={input}
              onChange={handleInput}
              onKeyDown={handleKeyDown}
              disabled={loading}
              className="w-full input-surface text-[14.5px] resize-none min-h-[46px] max-h-[140px] disabled:opacity-60"
              style={{ lineHeight: 1.55 }}
            />
          </div>

          {loading ? (
            <button
              onClick={handleCancel}
              className="btn-danger w-11 h-11 !p-0 rounded-[9px] inline-flex items-center justify-center flex-shrink-0"
              aria-label="Huỷ truy vấn" title="Huỷ"
            >
              <Icon name="Square" size={15} />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              className="btn-primary w-11 h-11 !p-0 rounded-[9px] inline-flex items-center justify-center flex-shrink-0"
              aria-label="Gửi câu hỏi"
            >
              <Icon name="Send" size={16} strokeWidth={2} />
            </button>
          )}
        </div>
      </div>

      <style>{`
        .scrollbar-none::-webkit-scrollbar { display: none; }
        .scrollbar-none { -ms-overflow-style: none; scrollbar-width: none; }
        @media (min-width: 768px) { .md\\:hidden { display: none !important; } }
        .menu-item { display: flex; align-items: center; gap: 8px; width: 100%; padding: 7px 12px;
          font-size: 13px; color: var(--text-primary); background: transparent; text-align: left; }
        .menu-item:hover { background: color-mix(in srgb, var(--brand) 10%, transparent); }
        .menu-item--danger { color: var(--err); }
        .menu-item--danger:hover { background: color-mix(in srgb, var(--err) 12%, transparent); }
      `}</style>
    </div>
  );
}
