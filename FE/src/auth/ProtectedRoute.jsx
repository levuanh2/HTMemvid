import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./useAuth";
import { getAuthRedirectState } from "./authRedirect";
import Spinner from "../components/ui/Spinner";

// Gate for /app: shows a spinner while the session is being restored, redirects
// unauthenticated users to /login?next=<path>, else renders the workspace.
export default function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  const state = getAuthRedirectState({ loading, user, path: location.pathname + location.search });

  if (state.action === "loading") {
    return (
      <div className="h-screen flex items-center justify-center text-brand" style={{ background: "var(--bg-base)" }}>
        <Spinner size={22} />
      </div>
    );
  }
  if (state.action === "redirect") {
    return <Navigate to={state.to} replace />;
  }
  return children;
}
