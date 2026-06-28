import { useState } from "react";
import SidebarLeft from "./SidebarLeft";
import ChatArea from "./ChatArea";
import SidebarRight from "./SidebarRight";
import { useTheme } from "../../hooks/useTheme";

// ── Icons ────────────────────────────────────────
const SearchIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4 text-text-muted flex-shrink-0">
    <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
  </svg>
);
const MenuIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="w-5 h-5">
    <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
  </svg>
);
const ToolsIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="w-5 h-5">
    <circle cx="12" cy="12" r="3" /><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14" />
  </svg>
);

export default function MainLayout({ selectedSources, setSelectedSources }) {
  const [sources, setSources] = useState([]);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const { isDark, setLight, setDark } = useTheme();

  return (
    <div className="flex flex-col h-screen overflow-hidden font-body transition-theme" style={{ background: 'var(--bg-base)', color: 'var(--text-primary)' }}>

      {/* ── TOP HEADER ── */}
      <header
        className="flex items-center gap-4 px-5 h-[56px] border-b border-border flex-shrink-0 transition-theme"
        style={{ background: 'var(--bg-sidebar)', boxShadow: '0 1px 0 var(--border-color)' }}>

        {/* Mobile left sidebar toggle */}
        <button
          onClick={() => setLeftOpen(true)}
          className="md:hidden icon-btn w-9 h-9 border-0 shadow-none bg-transparent"
          aria-label="Mở danh sách tài liệu"
        >
          <MenuIcon />
        </button>

        {/* Logo */}
        <div className="flex items-center gap-2 flex-shrink-0">
          <div className="w-8 h-8 rounded-[9px] flex items-center justify-center text-white text-sm font-bold flex-shrink-0"
            style={{ background: "linear-gradient(135deg, #4f46e5, #6366f1)" }}>
            M
          </div>
          <span className="font-display font-bold text-[15px] text-text-primary hidden sm:block">MemVid AI</span>
        </div>

        {/* Search bar (center) */}
        <div className="flex-1 flex justify-center px-2">
          <label className="header-search cursor-text">
            <SearchIcon />
            <span className="text-text-muted text-[14px]">Tìm kiếm tài liệu...</span>
          </label>
        </div>

        {/* Right actions */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Mobile right sidebar toggle */}
          <button
            onClick={() => setRightOpen(true)}
            className="md:hidden icon-btn w-9 h-9"
            aria-label="Mở công cụ"
          >
            <ToolsIcon />
          </button>
          {/* Theme toggle — actual logic */}
          <div className="hidden sm:flex theme-toggle">
            <button
              onClick={setLight}
              title="Light mode"
              className={`theme-toggle-btn ${!isDark ? "theme-toggle-btn-active" : ""}`}
              aria-label="Light mode"
            >
              ☀️
            </button>
            <button
              onClick={setDark}
              title="Dark mode"
              className={`theme-toggle-btn ${isDark ? "theme-toggle-btn-active" : ""}`}
              aria-label="Dark mode"
            >
              🌙
            </button>
          </div>
        </div>
      </header>

      {/* ── BODY (3 columns) ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Mobile overlay */}
        {(leftOpen || rightOpen) && (
          <div
            className="fixed inset-0 bg-black/30 z-30 md:hidden backdrop-blur-sm"
            onClick={() => { setLeftOpen(false); setRightOpen(false); }}
          />
        )}

        {/* ── LEFT SIDEBAR ── */}
        <aside
          className={`
            fixed top-[56px] left-0 h-[calc(100vh-56px)] z-40 transition-transform duration-300 ease-in-out
            md:static md:top-auto md:h-auto md:translate-x-0 md:z-auto
            w-[240px] shrink-0 bg-surface-sidebar border-r border-border
            ${leftOpen ? "translate-x-0" : "-translate-x-full"}
          `}
        >
          <SidebarLeft
            selectedSources={selectedSources}
            setSelectedSources={setSelectedSources}
            onSourcesChange={setSources}
            onClose={() => setLeftOpen(false)}
          />
        </aside>

        {/* ── MAIN CHAT ── */}
        <main className="flex flex-1 flex-col min-w-0 min-h-0">
          <ChatArea
            selectedSources={selectedSources}
            sources={sources}
            onOpenLeft={() => setLeftOpen(true)}
            onOpenRight={() => setRightOpen(true)}
          />
        </main>

        {/* ── RIGHT SIDEBAR ── */}
        <aside
          className={`
            fixed top-[56px] right-0 h-[calc(100vh-56px)] z-40 transition-transform duration-300 ease-in-out
            md:static md:top-auto md:h-auto md:translate-x-0 md:z-auto
            w-[300px] shrink-0 bg-surface-sidebar border-l border-border
            ${rightOpen ? "translate-x-0" : "translate-x-full"}
          `}
        >
          <SidebarRight
            selectedSources={selectedSources}
            onClose={() => setRightOpen(false)}
          />
        </aside>
      </div>
    </div>
  );
}
