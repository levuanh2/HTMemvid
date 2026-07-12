// Bearer token storage for the auth MVP.
//
// Kept in localStorage["memvid-token"] so the session survives a reload. This is
// an intentional MVP tradeoff (XSS exposure); the upgrade path is an httpOnly
// cookie + same-origin proxy. All access is guarded so a blocked/unavailable
// localStorage (private mode, SSR) never throws.

const KEY = "memvid-token";

export function getToken() {
  try {
    return localStorage.getItem(KEY) || null;
  } catch {
    return null;
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(KEY, token);
  } catch {
    /* localStorage unavailable — ignore */
  }
}

export function clearToken() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* localStorage unavailable — ignore */
  }
}
