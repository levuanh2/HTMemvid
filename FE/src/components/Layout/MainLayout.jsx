import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import SidebarLeft from "./SidebarLeft";
import ChatArea from "./ChatArea";
import SidebarRight from "./SidebarRight";
import { useTheme } from "../../hooks/useTheme";
import { useAuth } from "../../auth/useAuth";
import { Icon } from "../ui/Icon";
import Toaster from "../ui/Toaster";

export default function MainLayout({ selectedSources, setSelectedSources }) {
  const [sources, setSources] = useState([]);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const { isDark, setLight, setDark } = useTheme();
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const handleLogout = async () => { await logout(); navigate("/"); };

  // ── Signature state: evidence margin ⇄ citation chips ──
  // `evidence` = provenance of the latest answer ({ sources, chunks }).
  // `highlight` = the citation a user is pointing at, shared so a chip in the
  // answer and its source frame light up together (either direction).
  const [evidence, setEvidence] = useState(null);
  const [highlight, setHighlight] = useState(null); // { stem, chunkId } | null
  const onHighlight = useCallback((c) => setHighlight(c), []);

  // Task 16 — "Hỏi về đoạn này" (EvidenceDrawer, inside the mindmap overlay)
  // prefills + focuses the chat composer with the evidence snippet. `nonce`
  // forces ChatArea's effect to re-fire even if the same snippet is asked
  // about twice in a row (object identity, not just text, changes).
  const [askAboutDraft, setAskAboutDraft] = useState(null); // { text, nonce } | null
  const onAskAbout = useCallback((snippet) => {
    const text = String(snippet || "").trim();
    if (!text) return;
    setAskAboutDraft({ text: `Về đoạn này: "${text}" — hãy giải thích thêm.`, nonce: Date.now() });
  }, []);

  return (
    <div className="flex flex-col h-screen overflow-hidden font-body transition-theme" style={{ background: "var(--bg-base)", color: "var(--text-primary)" }}>

      {/* ── TOP HEADER ── */}
      <header
        className="flex items-center gap-4 px-4 sm:px-5 h-[58px] border-b border-border flex-shrink-0 transition-theme"
        style={{ background: "var(--bg-sidebar)" }}
      >
        {/* Mobile: open source library */}
        <button onClick={() => setLeftOpen(true)} className="md:hidden icon-btn w-9 h-9" aria-label="Mở thư mục nguồn">
          <Icon name="Menu" size={18} />
        </button>

        {/* Wordmark — a stamped seal + serif name */}
        <div className="flex items-center gap-2.5 flex-shrink-0 select-none">
          <span
            className="w-[30px] h-[30px] rounded-[6px] inline-flex items-center justify-center font-display text-[16px] font-semibold flex-shrink-0"
            style={{ color: "var(--accent)", border: "1.5px solid var(--accent)", transform: "rotate(-4deg)" }}
            aria-hidden
          >
            M
          </span>
          <span className="font-display font-semibold text-[17px] tracking-tight text-text-primary hidden sm:block">
            MemVid<span className="text-brand">X</span>
          </span>
        </div>

        {/* Center eyebrow — the thesis, not a dead search box */}
        <div className="flex-1 flex justify-center px-2 min-w-0">
          <span className="hidden md:block text-[12px] tracking-[0.14em] uppercase text-text-muted font-mono truncate">
            Đọc · Truy hồi · Dẫn chứng
          </span>
        </div>

        {/* Right actions */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {/* Mobile: open evidence margin */}
          <button onClick={() => setRightOpen(true)} className="md:hidden icon-btn w-9 h-9" aria-label="Mở lề bằng chứng">
            <Icon name="PanelRight" size={18} />
          </button>
          {/* Theme toggle */}
          <div className="hidden sm:flex theme-toggle" role="group" aria-label="Chế độ sáng/tối">
            <button onClick={setLight} title="Nền sáng" aria-pressed={!isDark} className={`theme-toggle-btn ${!isDark ? "theme-toggle-btn-active" : ""}`} aria-label="Nền sáng">
              <Icon name="Sun" size={15} />
            </button>
            <button onClick={setDark} title="Nền tối" aria-pressed={isDark} className={`theme-toggle-btn ${isDark ? "theme-toggle-btn-active" : ""}`} aria-label="Nền tối">
              <Icon name="Moon" size={15} />
            </button>
          </div>

          {/* Account: user identity + logout */}
          {user && (
            <div className="flex items-center gap-1.5">
              <span className="hidden md:inline text-[12.5px] text-text-secondary max-w-[160px] truncate" title={user.email}>
                {user.display_name || user.email}
              </span>
              <button onClick={handleLogout} className="icon-btn w-9 h-9" aria-label="Đăng xuất" title="Đăng xuất">
                <Icon name="LogOut" size={16} />
              </button>
            </div>
          )}
        </div>
      </header>

      {/* ── BODY (3 columns) ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">

        {/* Mobile overlay */}
        {(leftOpen || rightOpen) && (
          <div
            className="fixed inset-0 bg-black/40 z-30 md:hidden backdrop-blur-sm"
            onClick={() => { setLeftOpen(false); setRightOpen(false); }}
          />
        )}

        {/* ── LEFT — Thư mục nguồn ── */}
        <aside
          className={`
            fixed top-[58px] left-0 h-[calc(100vh-58px)] z-40 transition-transform duration-300 ease-in-out
            md:static md:top-auto md:h-auto md:translate-x-0 md:z-auto
            w-[252px] shrink-0 bg-surface-sidebar border-r border-border
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

        {/* ── CENTER — Phiên đọc ── */}
        <main className="flex flex-1 flex-col min-w-0 min-h-0">
          <ChatArea
            selectedSources={selectedSources}
            sources={sources}
            onEvidence={setEvidence}
            highlight={highlight}
            onHighlight={onHighlight}
            onOpenLeft={() => setLeftOpen(true)}
            onOpenRight={() => setRightOpen(true)}
            askAboutDraft={askAboutDraft}
          />
        </main>

        {/* ── RIGHT — Lề bằng chứng ── */}
        <aside
          className={`
            fixed top-[58px] right-0 h-[calc(100vh-58px)] z-40 transition-transform duration-300 ease-in-out
            md:static md:top-auto md:h-auto md:translate-x-0 md:z-auto
            w-[326px] shrink-0 bg-surface-sidebar border-l border-border
            ${rightOpen ? "translate-x-0" : "translate-x-full"}
          `}
        >
          <SidebarRight
            selectedSources={selectedSources}
            evidence={evidence}
            highlight={highlight}
            onHighlight={onHighlight}
            onClose={() => setRightOpen(false)}
            onAskAbout={onAskAbout}
          />
        </aside>
      </div>

      {/* ── TOAST STACK ── */}
      <Toaster />
    </div>
  );
}
