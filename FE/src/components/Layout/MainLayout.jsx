import { useState } from "react";
import SidebarLeft from "./SidebarLeft";
import ChatArea from "./ChatArea";
import SidebarRight from "./SidebarRight";

export default function MainLayout({ selectedSources, setSelectedSources }) {
  // State để lưu sources từ SidebarLeft, truyền xuống ChatArea
  const [sources, setSources] = useState([]);

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar trái */}
      <div className="w-1/5 border-r bg-white">
        <SidebarLeft
          selectedSources={selectedSources}
          setSelectedSources={setSelectedSources}
          onSourcesChange={setSources}
        />
      </div>

      {/* Chat Area */}
      <div className="flex flex-1 flex-col min-h-0">
        <ChatArea selectedSources={selectedSources} sources={sources} />
      </div>

      {/* Sidebar phải */}
      <div className="w-1/4 border-l bg-white">
        <SidebarRight selectedSources={selectedSources} />
      </div>
    </div>
  );
}
