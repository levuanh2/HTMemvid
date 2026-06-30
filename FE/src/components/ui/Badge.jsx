const TONE = {
  ready: "badge-ready",
  processing: "badge-processing",
  error: "badge-error",
};

/** Worded status tag — conveys state by text + color (never color alone). */
export default function Badge({ tone = "ready", children, className = "" }) {
  return <span className={`${TONE[tone] || TONE.ready} ${className}`}>{children}</span>;
}
