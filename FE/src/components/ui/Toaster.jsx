// Toast nhẹ cho đường mindmap (KHÔNG thay alert() toàn app).
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

let _toasts = [];
let _nextId = 1;
const _subs = new Set();
const _timers = new Map();

const _emit = () => _subs.forEach((cb) => cb(_toasts));

export const subscribeToasts = (cb) => { _subs.add(cb); cb(_toasts); return () => _subs.delete(cb); };

export const dismissToast = (id) => {
  const t = _timers.get(id);
  if (t) { clearTimeout(t); _timers.delete(id); }
  _toasts = _toasts.filter((x) => x.id !== id);
  _emit();
};

export const toast = (message, { type = "info", duration = 5000 } = {}) => {
  const id = _nextId++;
  _toasts = [..._toasts, { id, message, type }];
  _emit();
  _timers.set(id, setTimeout(() => dismissToast(id), duration));
  return id;
};

export const _resetToasts = () => { // test-only
  _timers.forEach(clearTimeout); _timers.clear(); _toasts = []; _emit();
};

const COLORS = { info: "var(--slate)", success: "var(--ok)", error: "var(--err)" };

export default function Toaster() {
  const [items, setItems] = useState([]);
  useEffect(() => subscribeToasts(setItems), []);
  if (typeof document === "undefined" || !items.length) return null;
  return createPortal(
    <div className="fixed bottom-4 right-4 z-[1200] flex flex-col gap-2 max-w-xs">
      {items.map((t) => (
        <div key={t.id} role={t.type === "error" ? "alert" : "status"}
          onClick={() => dismissToast(t.id)}
          className="cursor-pointer rounded-[8px] border px-3 py-2 text-[13px]"
          style={{
            background: "var(--bg-card)", borderColor: "var(--border-strong)",
            boxShadow: "var(--shadow-card-hover)", color: "var(--text-primary)",
            borderLeft: `3px solid ${COLORS[t.type] || COLORS.info}`,
          }}>
          {t.message}
        </div>
      ))}
    </div>,
    document.body
  );
}
