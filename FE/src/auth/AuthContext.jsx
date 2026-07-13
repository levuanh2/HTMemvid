import { useCallback, useEffect, useState } from "react";
import { registerUser, loginUser, logoutUser, getCurrentUser } from "../utils/api";
import { getToken, setToken, clearToken } from "./tokenStore";
import { installUnauthorizedHandler } from "./authEvents";
import { AuthContext } from "./context";

// Map backend error codes → friendly Vietnamese messages for the forms.
function friendlyError(err) {
  const code = err?.code || "";
  if (code === "invalid_credentials") return "Email hoặc mật khẩu không đúng.";
  if (code === "email_exists") return "Email này đã được đăng ký.";
  if (code === "invalid_email") return "Email chưa hợp lệ.";
  if (code === "weak_password") return "Mật khẩu cần ít nhất 8 ký tự.";
  if (code === "rate_limited") return "Bạn thử quá nhiều lần. Vui lòng đợi một chút.";
  if (err?.status === 0 || err?.name === "TypeError") return "Không kết nối được máy chủ.";
  return "Đã có lỗi xảy ra. Vui lòng thử lại.";
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Restore session on mount: if a token exists, validate it via /auth/me.
  useEffect(() => {
    let alive = true;
    (async () => {
      if (!getToken()) {
        if (alive) setLoading(false);
        return;
      }
      try {
        const u = await getCurrentUser(); // null on 401 (clears token)
        if (alive) setUser(u || null);
      } catch {
        clearToken();
        if (alive) setUser(null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  // Global sign-out signal: apiFetch fires `auth:unauthorized` when a protected app
  // API returns 401 (expired/invalid token) → clear the token and drop the user so
  // ProtectedRoute redirects to /login?next=<current>.
  useEffect(() => installUnauthorizedHandler(() => setUser(null)), []);

  const login = useCallback(async (email, password) => {
    setError("");
    try {
      const { token, user: u } = await loginUser({ email, password });
      setToken(token);
      setUser(u);
      return u;
    } catch (err) {
      setError(friendlyError(err));
      throw err;
    }
  }, []);

  const register = useCallback(async ({ email, password, display_name }) => {
    setError("");
    try {
      const { token, user: u } = await registerUser({ email, password, display_name });
      setToken(token);
      setUser(u);
      return u;
    } catch (err) {
      setError(friendlyError(err));
      throw err;
    }
  }, []);

  const logout = useCallback(async () => {
    await logoutUser();       // best-effort server call
    clearToken();
    setUser(null);
    setError("");
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const u = await getCurrentUser();
      setUser(u || null);
      return u;
    } catch {
      setUser(null);
      return null;
    }
  }, []);

  const value = { user, loading, error, setError, login, register, logout, refreshUser };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
