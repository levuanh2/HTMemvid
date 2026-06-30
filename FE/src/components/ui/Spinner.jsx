/** Inline loading spinner that inherits currentColor. */
export default function Spinner({ size = 14, className = "" }) {
  return (
    <span
      role="status"
      aria-label="Đang tải"
      className={`inline-block flex-shrink-0 rounded-full border-2 border-current border-t-transparent animate-spin ${className}`}
      style={{ width: size, height: size, opacity: 0.85 }}
    />
  );
}
