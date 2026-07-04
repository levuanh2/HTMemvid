// Evidence drawer — right-side sliding panel inside the mindmap overlay.
// Task 16: click a node, see the note + the actual chunk text(s) it was built
// from ("lề bằng chứng" pattern, mirrored from SidebarRight's citation margin).
//
// Fetched chunk text is cached in a MODULE-level Map (not component state) so
// re-clicking a node — or opening a different node that shares a chunk ref —
// never re-fetches. This cache is intentionally never invalidated within a
// session: chunk text is immutable once ingested.
import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../ui/Icon";
import Spinner from "../ui/Spinner";
import { fetchChunkText } from "../../utils/api";

const SNIPPET_MAX = 600;

const cutSnippet = (text) => {
  const s = String(text || "");
  if (s.length <= SNIPPET_MAX) return { snippet: s, truncated: false };
  return { snippet: s.slice(0, SNIPPET_MAX), truncated: true };
};

// Module-level — shared by every drawer instance for the lifetime of the tab.
const chunkTextCache = new Map();

export default function EvidenceDrawer({ node, onClose, generating, onAskAbout }) {
  const [entries, setEntries] = useState([]); // [{ chunkId, snippet, truncated, loading, error }]
  const panelRef = useRef(null);
  const closeBtnRef = useRef(null);

  const chunkRefs = useMemo(
    () => (Array.isArray(node?.chunkRefs) ? node.chunkRefs.filter((c) => c != null && c !== "") : []),
    [node]
  );

  // Fetch each chunk ref once per node, reusing the module cache across nodes.
  useEffect(() => {
    if (!node) return;
    let cancelled = false;

    if (chunkRefs.length === 0) { setEntries([]); return; }

    setEntries(chunkRefs.map((chunkId) => {
      const cached = chunkTextCache.get(String(chunkId));
      return cached !== undefined
        ? { chunkId, ...cutSnippet(cached), loading: false, error: cached == null }
        : { chunkId, snippet: "", truncated: false, loading: true, error: false };
    }));

    chunkRefs.forEach(async (chunkId) => {
      const key = String(chunkId);
      if (chunkTextCache.has(key)) return; // already resolved (hit or miss)
      try {
        const text = await fetchChunkText(chunkId);
        chunkTextCache.set(key, text); // cache misses too — chunk id won't gain text mid-session
        if (cancelled) return;
        setEntries((prev) => prev.map((e) => (
          String(e.chunkId) === key ? { ...e, ...cutSnippet(text), loading: false, error: text == null } : e
        )));
      } catch {
        if (cancelled) return;
        setEntries((prev) => prev.map((e) => (
          String(e.chunkId) === key ? { ...e, loading: false, error: true } : e
        )));
      }
    });

    return () => { cancelled = true; };
  }, [node, chunkRefs]);

  // Esc + outside-click close; focus the close button on open (keyboard reachable).
  useEffect(() => {
    if (!node) return;
    closeBtnRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); onClose?.(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [node, onClose]);

  if (!node) return null;

  const emptyMessage = generating
    ? "Chưa có bằng chứng — đang làm giàu"
    : "Nhánh này chưa gắn trích đoạn";

  return (
    <div
      className="absolute inset-0 z-20 flex justify-end"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.(); }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="false"
        aria-label={`Bằng chứng cho ${node.title || "nhánh"}`}
        className="evidence-drawer h-full w-full max-w-[380px] flex flex-col border-l border-border shadow-card-hover animate-drawerIn"
        style={{ background: "var(--bg-sidebar)" }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-2 px-4 py-3.5 border-b border-border flex-shrink-0">
          <div className="min-w-0 flex-1">
            <div className="text-[10px] font-mono uppercase tracking-[0.12em] text-text-muted mb-1">Bằng chứng</div>
            <h3 className="font-display font-semibold text-text-primary text-[14px] truncate" title={node.title}>
              {node.title || "Nhánh"}
            </h3>
          </div>
          <button
            ref={closeBtnRef}
            onClick={onClose}
            className="icon-btn w-8 h-8 flex-shrink-0"
            aria-label="Đóng lề bằng chứng"
            title="Đóng (Esc)"
          >
            <Icon name="X" size={15} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3.5">
          {node.note && (
            <p className="font-reading text-[13.5px] leading-[1.6] text-text-secondary mb-4 pb-4 border-b border-border">
              {node.note}
            </p>
          )}

          {entries.length === 0 ? (
            <div className="text-center px-2 pt-8 text-text-muted">
              <Icon name="Quote" size={22} className="mx-auto mb-2.5 opacity-60" />
              <p className="text-[12.5px] leading-[1.6] text-text-secondary">{emptyMessage}</p>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {entries.map((entry, i) => (
                <div key={`${entry.chunkId}-${i}`} className="evidence-frame p-3">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span
                      className="w-5 h-5 rounded-[4px] inline-flex items-center justify-center text-[11px] font-mono font-semibold flex-shrink-0"
                      style={{ color: "var(--accent)", border: "1px solid color-mix(in srgb, var(--accent) 35%, transparent)" }}
                    >
                      {i + 1}
                    </span>
                    <span className="coord truncate flex-1">đoạn {entry.chunkId}</span>
                  </div>

                  {entry.loading ? (
                    <div className="flex items-center gap-2 text-[12px] text-text-muted py-1"><Spinner size={12} /> Đang tải…</div>
                  ) : entry.error ? (
                    <p className="text-[12.5px] text-text-muted italic">Không tải được trích đoạn này.</p>
                  ) : (
                    <>
                      <p className="font-reading text-[13px] leading-[1.55] text-text-secondary whitespace-pre-wrap">
                        {entry.snippet}{entry.truncated ? "…" : ""}
                      </p>
                      {typeof onAskAbout === "function" && (
                        <button
                          onClick={() => onAskAbout(entry.snippet)}
                          className="mt-2 inline-flex items-center gap-1.5 text-[11.5px] font-medium text-brand hover:underline"
                        >
                          <Icon name="MessageCircleQuestion" size={12} /> Hỏi về đoạn này
                        </button>
                      )}
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <style>{`
        @keyframes drawerIn { from { transform: translateX(16px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
        .animate-drawerIn { animation: drawerIn 180ms ease-out both; }
        @media (prefers-reduced-motion: reduce) { .animate-drawerIn { animation: none !important; } }
      `}</style>
    </div>
  );
}
