import { useState } from "react";
import SidebarLeft from "./SidebarLeft";
import ChatArea from "./ChatArea";
import SidebarRight from "./SidebarRight";

export default function MainLayout({ selectedSources, setSelectedSources }) {
  const [sources, setSources] = useState([]);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "#0f172a", fontFamily: "'Inter', sans-serif" }}>

      {/* ── Mobile overlay ── */}
      {(leftOpen || rightOpen) && (
        <div
          className="fixed inset-0 bg-black/60 z-30 md:hidden backdrop-blur-sm"
          onClick={() => { setLeftOpen(false); setRightOpen(false); }}
        />
      )}

      {/* ── LEFT SIDEBAR ── */}
      <aside
        className={`
          fixed top-0 left-0 h-full z-40 transition-transform duration-300 ease-in-out
          md:static md:translate-x-0 md:z-auto
          ${leftOpen ? "translate-x-0" : "-translate-x-full"}
        `}
        style={{ width: 280, background: "#111827", borderRight: "1px solid #1e2d3d", flexShrink: 0 }}
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
          fixed top-0 right-0 h-full z-40 transition-transform duration-300 ease-in-out
          md:static md:translate-x-0 md:z-auto
          ${rightOpen ? "translate-x-0" : "translate-x-full"}
        `}
        style={{ width: 300, background: "#111827", borderLeft: "1px solid #1e2d3d", flexShrink: 0 }}
      >
        <SidebarRight
          selectedSources={selectedSources}
          onClose={() => setRightOpen(false)}
        />
      </aside>
    </div>
  );
}
