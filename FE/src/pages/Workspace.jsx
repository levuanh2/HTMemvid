import { useState } from "react";
import MainLayout from "../components/Layout/MainLayout";

// Workspace — the authenticated app screen (mounted at /app).
// Holds `selectedSources` (lifted out of the old App.jsx) so the chosen files
// persist across the workspace session. Everything below MainLayout — chat,
// upload, summary, mindmap, conversation context, RQ — is unchanged.
export default function Workspace() {
  const [selectedSources, setSelectedSources] = useState([]);
  return (
    <MainLayout
      selectedSources={selectedSources}
      setSelectedSources={setSelectedSources}
    />
  );
}
