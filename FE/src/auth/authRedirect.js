// Pure routing decision for ProtectedRoute (no React) — unit-testable.
//
// action: "loading" | "redirect" | "render"
export function getAuthRedirectState({ loading, user, path }) {
  if (loading) return { action: "loading" };
  if (!user) return { action: "redirect", to: `/login?next=${encodeURIComponent(path || "/app")}` };
  return { action: "render" };
}

// Where a public-only page (login/register) should send an already-authed user.
// Uses the `next` query param when it is a safe in-app path, else /app.
export function safeNext(nextParam) {
  if (typeof nextParam === "string" && nextParam.startsWith("/") && !nextParam.startsWith("//")) {
    return nextParam;
  }
  return "/app";
}
