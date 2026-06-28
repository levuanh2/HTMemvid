import { useState } from "react";
import { createPortal } from "react-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";

const mdComponents = {
  p: ({ node, ...props }) => <p className="mb-2 last:mb-0 text-[14px] leading-7 text-text-primary" {...props} />,
  ul: ({ node, ...props }) => <ul className="pl-5 my-2 list-disc text-[14px] text-text-primary" {...props} />,
  ol: ({ node, ...props }) => <ol className="pl-5 my-2 list-decimal text-[14px] text-text-primary" {...props} />,
  li: ({ node, ...props }) => <li className="mb-1 leading-7" {...props} />,
  strong: ({ node, ...props }) => <strong className="text-brand-light font-bold" {...props} />,
  h1: ({ node, ...props }) => <h1 className="text-base font-bold my-2 text-text-primary" {...props} />,
  h2: ({ node, ...props }) => <h2 className="text-[15px] font-bold my-2 text-text-primary" {...props} />,
  h3: ({ node, ...props }) => <h3 className="text-[14px] font-semibold my-2 text-text-secondary" {...props} />,
  code: ({ inline, children, ...props }) => inline
    ? <code className="bg-white/10 border border-white/10 px-1.5 py-0.5 rounded text-[12px] text-brand-light" {...props}>{children}</code>
    : <pre className="bg-surface-sidebar border border-white/10 rounded-lg p-3 overflow-x-auto my-2"><code className="text-[12px] text-emerald-300" {...props}>{children}</code></pre>,
  blockquote: ({ node, ...props }) => <blockquote className="border-l-4 border-brand pl-3 my-2 text-text-secondary italic" {...props} />,
};

export default function SummaryModal({ data, onClose, onSave }) {
  const [title, setTitle] = useState(() => data?.title || "Tóm tắt tài liệu");
  const [saving, setSaving] = useState(false);

  if (!data) return null;
  const { summary, base_summary, sources = [] } = data;

  const handleSave = async () => {
    if (!title.trim()) return;
    try {
      setSaving(true);
      await onSave({ title: title.trim(), data, sources });
    } catch (err) { console.error(err); }
    finally { setSaving(false); }
  };

  if (typeof document === "undefined") return null;

  return createPortal((
    <div className="fixed inset-0 z-50 p-4 bg-black/70 backdrop-blur-sm flex items-center justify-center" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="w-full max-w-[860px] max-h-[92vh] flex flex-col overflow-hidden rounded-2xl border border-border bg-surface-card shadow-[0_24px_60px_rgba(0,0,0,0.6)]">
        {/* Header */}
        <div className="px-5 py-4 border-b border-border bg-gradient-to-br from-brand/15 to-brand-pink/10">
          <div className="flex items-center gap-3 mb-3">
            <span className="text-xl">📝</span>
            <span className="font-display font-bold text-text-primary flex-1">Tóm tắt tài liệu</span>
            <button onClick={onClose} className="btn-secondary px-3 py-2 text-sm">
              ✕ Đóng
            </button>
          </div>
          <div className="flex gap-2 items-center">
            {onSave && (
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="input-surface flex-1 py-2.5"
                placeholder="Nhập tiêu đề để lưu…"
              />
            )}
            {onSave && (
              <button onClick={handleSave} disabled={saving} className="btn-primary px-4 py-2.5 text-sm disabled:opacity-70">
                {saving ? "Đang lưu…" : "💾 Lưu"}
              </button>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5">
          <div className="mb-5">
            <div className="text-[13px] font-bold text-brand-light mb-2">Bản tóm tắt</div>
            <div className="bg-surface-sidebar border border-border rounded-xl p-4">
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={mdComponents}>
                {summary || "Không có tóm tắt"}
              </ReactMarkdown>
            </div>
          </div>

          {base_summary && base_summary !== summary && (
            <details className="mb-2">
              <summary className="cursor-pointer select-none text-[12px] text-text-secondary hover:text-text-primary">
                Xem tóm tắt cơ bản (trước khi xử lý)
              </summary>
              <div className="mt-3 bg-surface-sidebar border border-white/10 rounded-xl p-4">
                <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={mdComponents}>
                  {base_summary}
                </ReactMarkdown>
              </div>
            </details>
          )}
        </div>
      </div>
    </div>
  ), document.body);
}