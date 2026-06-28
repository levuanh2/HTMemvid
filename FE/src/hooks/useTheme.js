import { useState, useEffect } from "react";

const STORAGE_KEY = "memvid-theme";

/**
 * useTheme — toggle dark/light mode.
 * - Đọc preference từ localStorage (persist qua reload)
 * - Fallback về system preference (prefers-color-scheme)
 * - Toggle class "dark" trên <html> element
 */
export function useTheme() {
  const [isDark, setIsDark] = useState(() => {
    // 1. Check localStorage first
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved === "dark") return true;
      if (saved === "light") return false;
    } catch {}
    // 2. Fallback: system preference
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });

  // Apply class to <html> whenever isDark changes
  useEffect(() => {
    const html = document.documentElement;
    if (isDark) {
      html.classList.add("dark");
    } else {
      html.classList.remove("dark");
    }
    // Persist preference
    try {
      localStorage.setItem(STORAGE_KEY, isDark ? "dark" : "light");
    } catch {}
  }, [isDark]);

  const toggleTheme = () => setIsDark((prev) => !prev);
  const setLight = () => setIsDark(false);
  const setDark  = () => setIsDark(true);

  return { isDark, toggleTheme, setLight, setDark };
}
