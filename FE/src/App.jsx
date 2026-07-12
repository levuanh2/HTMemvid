import { Routes, Route, Navigate } from "react-router-dom";
import { useTheme } from "./hooks/useTheme";
import ProtectedRoute from "./auth/ProtectedRoute";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Workspace from "./pages/Workspace";

export default function App() {
  // Apply the persisted light/dark preference app-wide (every route inherits it).
  // MainLayout and Landing also call useTheme() for their own toggles; the shared
  // truth is the `.dark` class on <html>, so the instances stay in sync.
  useTheme();

  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        path="/app"
        element={
          <ProtectedRoute>
            <Workspace />
          </ProtectedRoute>
        }
      />
      <Route path="/app/chat" element={<Navigate to="/app" replace />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
