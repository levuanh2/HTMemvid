import { clearToken } from "./tokenStore";

// Wire the global `auth:unauthorized` signal (fired by apiFetch when a protected app
// API returns 401) to a client-side sign-out: clear the token, then notify the caller
// (e.g. drop the user so ProtectedRoute redirects to /login?next=<current>).
//
// Idempotent — clearing an already-empty session is a no-op, so repeated 401s never
// loop. Returns an uninstaller; safe no-op when there is no window (SSR/node tests).
export function installUnauthorizedHandler(onSignout) {
  if (typeof window === "undefined" || typeof window.addEventListener !== "function") {
    return () => {};
  }
  const handler = () => {
    clearToken();
    if (typeof onSignout === "function") onSignout();
  };
  window.addEventListener("auth:unauthorized", handler);
  return () => window.removeEventListener("auth:unauthorized", handler);
}
