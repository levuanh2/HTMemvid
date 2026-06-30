import { createPortal } from "react-dom";
import { useEffect } from "react";
import { Icon } from "./Icon";

/**
 * Shared portal modal frame. Backdrop click + Escape close.
 * `fullBleed` skips internal padding/scroll for canvas content (e.g. ReactFlow).
 */
export default function Modal({
  open = true,
  title,
  subtitle,
  onClose,
  children,
  footer,
  maxWidth = 920,
  fullBleed = false,
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6" role="dialog" aria-modal="true" aria-label={title || "Hộp thoại"}>
      <div className="absolute inset-0 bg-black/45 backdrop-blur-sm" onClick={onClose} />
      <div
        className="relative flex flex-col w-full max-h-[90vh] rounded-[10px] border overflow-hidden animate-fadeUp"
        style={{ maxWidth, background: "var(--bg-card)", borderColor: "var(--border-strong)", boxShadow: "var(--shadow-card-hover)" }}
      >
        {(title || onClose) && (
          <div className="flex items-center gap-3 px-5 py-3.5 border-b flex-shrink-0" style={{ borderColor: "var(--border-color)" }}>
            <div className="flex-1 min-w-0">
              {title && <h2 className="font-display text-[16px] font-semibold text-text-primary truncate">{title}</h2>}
              {subtitle && <p className="text-[12px] text-text-muted truncate mt-0.5">{subtitle}</p>}
            </div>
            {onClose && (
              <button onClick={onClose} className="icon-btn w-8 h-8 flex-shrink-0" aria-label="Đóng hộp thoại">
                <Icon name="X" size={16} />
              </button>
            )}
          </div>
        )}
        <div className={fullBleed ? "flex-1 min-h-0" : "flex-1 min-h-0 overflow-auto"}>{children}</div>
        {footer && (
          <div className="flex-shrink-0 border-t px-5 py-3" style={{ borderColor: "var(--border-color)" }}>
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
