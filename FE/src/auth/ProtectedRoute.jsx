// ProtectedRoute — Phase 1 PASS-THROUGH.
//
// For now this renders its children unconditionally so /app keeps working while
// the product shell lands. Real auth gating (loading spinner + redirect to
// /login?next=... when unauthenticated) is wired in Phase 3 once AuthContext
// exists. Keeping the component here now means App.jsx's route table is already
// in its final shape.
export default function ProtectedRoute({ children }) {
  return children;
}
