// Evidence drawer — right-side sliding panel inside the mindmap overlay.
// Task 16: click a node, see the note + the actual chunk text(s) it was built
// from ("lề bằng chứng" pattern, mirrored from SidebarRight's citation margin).
//
// Fetched chunk text is cached in a COMPONENT-scoped ref (`cacheRef`, Fix Round 1)
// so re-clicking a node — or opening a different node that shares a chunk ref —
// never re-fetches within the drawer's lifetime, while still bounding staleness:
// the cache is dropped whenever the drawer instance itself is torn down (e.g. the
// mindmap overlay closes/reopens), which matters once chunk ids get reused across
// a re-ingest.
import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../ui/Icon";
import Spinner from "../ui/Spinner";
import MdSnippet from "../ui/Markdown";
import { fetchChunkText } from "../../utils/api";

const SNIPPET_MAX = 600;

const cutSnippet = (text) => {
  const s = String(text || "");
  if (s.length <= SNIPPET_MAX) return { snippet: s, truncated: false };
  return { snippet: s.slice(0, SNIPPET_MAX), truncated: true };
};

export default function EvidenceDrawer({ node, onClose, generating, onAskAbout }) {
  const [entries, setEntries] = useState([]); // [{ chunkId, snippet, truncated, loading, error }]
  const panelRef = useRef(null);
  const closeBtnRef = useRef(null);
  const cacheRef = useRef(new Map());
  // Always holds the latest `node` — read at Escape-keydown time so the listener
  // effect below doesn't need `node` in its own deps (see that effect's comment).
  const nodeRef = useRef(node);
  nodeRef.current = node;

  // Stable primitive key for the fetch effect below — `node` itself is a NEW
  // object identity on every poll tick while a mindmap is generating (see
  // MindmapView's `selectedDrawerNode`), even when the node's actual chunk refs
  // haven't changed. Joining into a string gives the effect something that only
  // changes value when the refs actually do (Fix Round 1, Fix 2).
  const chunkKey = useMemo(
    () => (Array.isArray(node?.chunkRefs) ? node.chunkRefs.filter((c) => c != null && c !== "").join(",") : ""),
    [node]
  );

  // Fetch each chunk ref once per node, reusing the drawer-lifetime cache.
  // Deps are [node?.id, chunkKey] — both primitives — so a poll tick that hands
  // us a new `node` object with the SAME id + same chunk refs does not re-run
  // this effect or rebuild `entries`.
  useEffect(() => {
    if (node?.id == null) { setEntries([]); return; }
    const refs = chunkKey ? chunkKey.split(",") : [];
    let cancelled = false;

    if (refs.length === 0) { setEntries([]); return; }

    setEntries(refs.map((chunkId) => {
      const cached = cacheRef.current.get(chunkId);
      return cached !== undefined
        ? { chunkId, ...cutSnippet(cached), loading: false, error: cached == null }
        : { chunkId, snippet: "", truncated: false, loading: true, error: false };
    }));

    refs.forEach(async (chunkId) => {
      if (cacheRef.current.has(chunkId)) return; // already resolved (hit or miss)
      try {
        const text = await fetchChunkText(chunkId);
        cacheRef.current.set(chunkId, text); // cache misses too — chunk id won't gain text mid-session
        if (cancelled) return;
        setEntries((prev) => prev.map((e) => (
          e.chunkId === chunkId ? { ...e, ...cutSnippet(text), loading: false, error: text == null } : e
        )));
      } catch {
        if (cancelled) return;
        setEntries((prev) => prev.map((e) => (
          e.chunkId === chunkId ? { ...e, loading: false, error: true } : e
        )));
      }
    });

    return () => { cancelled = true; };
  }, [node?.id, chunkKey]);

  // Focus the close button ONLY when the drawer newly opens or the selected
  // node actually changes (deps [node?.id]) — not on every re-render a poll
  // tick causes (Fix Round 1, Fix 1). Previously this ran on every `node`
  // identity change (every tick), yanking keyboard focus to the close button
  // even while the user was hovering/interacting elsewhere on the canvas.
  useEffect(() => {
    if (node?.id == null) return;
    closeBtnRef.current?.focus();
  }, [node?.id]);

  // Esc-to-close: a SEPARATE effect from the focus one above, deps [onClose]
  // alone. `onClose` is now a stable useCallback identity from MindmapView, so
  // this listener is attached exactly once and never torn down/rebuilt purely
  // because a poll tick re-rendered the parent. Whether the drawer is actually
  // open is checked at KEYDOWN time via `nodeRef` (always the latest `node`),
  // not baked into the effect's dependencies or setup/teardown timing.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape" && nodeRef.current) { e.stopPropagation(); onClose?.(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

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
            <MdSnippet text={node.note}
              className="font-reading text-[13.5px] leading-[1.6] text-text-secondary mb-4 pb-4 border-b border-border" />
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
                      <MdSnippet text={`${entry.snippet}${entry.truncated ? "…" : ""}`}
                        className="font-reading text-[13px] leading-[1.55] text-text-secondary" />
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
