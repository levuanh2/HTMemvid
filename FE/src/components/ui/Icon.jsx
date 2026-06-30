import * as Lucide from "lucide-react";

/**
 * Thin wrapper over lucide-react so every icon shares one stroke weight and
 * default size — replaces the emoji-as-icon grab-bag (🧠📂📝🔊…) with a
 * consistent line set. Usage: <Icon name="Search" size={16} />
 */
export function Icon({ name, size = 16, strokeWidth = 1.75, className = "", ...rest }) {
  const Cmp = Lucide[name];
  if (!Cmp) {
    if (import.meta.env?.DEV) console.warn(`[Icon] unknown icon: ${name}`);
    return null;
  }
  return <Cmp size={size} strokeWidth={strokeWidth} className={className} aria-hidden {...rest} />;
}

export default Icon;
