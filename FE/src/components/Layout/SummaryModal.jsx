import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import Modal from "../ui/Modal";
import Button from "../ui/Button";
import Spinner from "../ui/Spinner";

const mdComponents = {
  p: ({ node, ...p }) => <p className="mb-2.5 last:mb-0 text-[15px] leading-[1.72] text-text-primary" {...p} />,
  ul: ({ node, ...p }) => <ul className="pl-5 my-2.5 list-disc marker:text-slate text-[15px] text-text-primary" {...p} />,
  ol: ({ node, ...p }) => <ol className="pl-5 my-2.5 list-decimal marker:text-slate text-[15px] text-text-primary" {...p} />,
  li: ({ node, ...p }) => <li className="mb-1.5 leading-[1.7]" {...p} />,
  strong: ({ node, ...p }) => <strong className="text-text-primary font-semibold" {...p} />,
  em: ({ node, ...p }) => <em className="italic" {...p} />,
  h1: ({ node, ...p }) => <h1 className="font-display text-[19px] font-semibold my-3 text-text-primary" {...p} />,
  h2: ({ node, ...p }) => <h2 className="font-display text-[17px] font-semibold my-2.5 text-text-primary" {...p} />,
  h3: ({ node, ...p }) => <h3 className="font-display text-[15px] font-semibold my-2 text-text-secondary" {...p} />,
  code: ({ node, inline, children, ...props }) => inline
    ? <code className="bg-surface-elevated border border-border px-1.5 py-0.5 rounded text-[12.5px] font-mono text-text-secondary" {...props}>{children}</code>
    : <pre className="bg-surface-elevated border border-border rounded-[7px] p-3 overflow-x-auto my-2.5"><code className="text-[12.5px] font-mono text-text-secondary" {...props}>{children}</code></pre>,
  blockquote: ({ node, ...p }) => <blockquote className="border-l-2 border-brand/50 pl-3.5 my-2.5 text-text-secondary italic" {...p} />,
};

export default function SummaryModal({ data, onClose, onSave }) {
  const [title, setTitle] = useState(() => data?.title || "Tóm tắt tài liệu");
  const [saving, setSaving] = useState(false);

  if (!data) return null;
  const { summary, base_summary, sources = [] } = data;

  const handleSave = async () => {
    if (!title.trim()) return;
    try { setSaving(true); await onSave({ title: title.trim(), data, sources }); }
    catch (err) { console.error(err); }
    finally { setSaving(false); }
  };

  return (
    <Modal
      title="Tóm tắt tài liệu"
      subtitle={sources?.length ? `${sources.length} tài liệu` : undefined}
      onClose={onClose}
      maxWidth={840}
      footer={onSave ? (
        <div className="flex gap-2 items-center">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="input-surface flex-1 !py-2.5 text-[14px]"
            placeholder="Nhập tiêu đề để lưu…"
            aria-label="Tiêu đề tóm tắt"
          />
          <Button variant="primary" onClick={handleSave} disabled={saving} className="!py-2.5">
            {saving ? <><Spinner size={14} /> Đang lưu…</> : "Lưu tóm tắt"}
          </Button>
        </div>
      ) : null}
    >
      <div className="p-5">
        <div className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted mb-2">Bản tóm tắt</div>
        <div className="surface-card font-reading">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={mdComponents}>
            {summary || "Không có tóm tắt."}
          </ReactMarkdown>
        </div>

        {base_summary && base_summary !== summary && (
          <details className="mt-4">
            <summary className="cursor-pointer select-none text-[12px] text-text-secondary hover:text-text-primary">
              Xem tóm tắt cơ bản (trước khi xử lý)
            </summary>
            <div className="mt-3 surface-card font-reading">
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={mdComponents}>
                {base_summary}
              </ReactMarkdown>
            </div>
          </details>
        )}
      </div>
    </Modal>
  );
}
