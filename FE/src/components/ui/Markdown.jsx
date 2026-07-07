// Markdown render cho TRÍCH ĐOẠN bằng chứng (EvidenceDrawer, lề bằng chứng) —
// chữ nhỏ hơn answer prose, không citation-chip. Chat answer vẫn dùng
// makeMdComponents riêng trong ChatArea (có citation logic + highlight state).
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import { unescapeMd } from "../../utils/evidence";

const SM = {
  p: (p) => <p className="mb-1.5 last:mb-0 leading-[1.6]" {...p} />,
  ul: (p) => <ul className="pl-4 my-1.5 list-disc marker:text-slate" {...p} />,
  ol: (p) => <ol className="pl-4 my-1.5 list-decimal marker:text-slate" {...p} />,
  li: (p) => <li className="mb-1 leading-[1.55]" {...p} />,
  strong: (p) => <strong className="font-semibold text-text-primary" {...p} />,
  em: (p) => <em className="italic" {...p} />,
  code: ({ inline, children, ...props }) =>
    <code className="font-mono text-[11px] bg-surface-elevated px-1 rounded" {...props}>{children}</code>,
  // heading trong snippet hạ cấp thành đoạn đậm — trích đoạn không cần cấp bậc to
  h1: (p) => <p className="font-semibold mb-1.5" {...p} />,
  h2: (p) => <p className="font-semibold mb-1.5" {...p} />,
  h3: (p) => <p className="font-semibold mb-1.5" {...p} />,
  a: ({ children }) => <span>{children}</span>, // snippet không cần link sống
};

export function MdSnippet({ text, className = "" }) {
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={SM}>
        {unescapeMd(text)}
      </ReactMarkdown>
    </div>
  );
}

// Prose cỡ đọc (SummaryModal) — chuyển từ mdComponents inline của SummaryModal
// vào đây để mọi text dài render qua MỘT đường (bài học "một đường render").
const PROSE = {
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

export function MdProse({ text, className = "" }) {
  return (
    <div className={className}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]} components={PROSE}>
        {unescapeMd(text)}
      </ReactMarkdown>
    </div>
  );
}

export default MdSnippet;
