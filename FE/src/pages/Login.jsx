import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Icon } from "../components/ui/Icon";
import { isValidEmail, isValidPassword } from "../auth/validate";

// Login — Phase 1 VISUAL PLACEHOLDER.
// No real authentication yet: on a valid-looking submit we just navigate to /app.
// Backend auth (POST /auth/login) + AuthContext land in Phase 2/3. Do not treat
// this as secure.
export default function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const onSubmit = (e) => {
    e.preventDefault();
    if (!isValidEmail(email)) return setError("Email chưa hợp lệ.");
    if (!isValidPassword(password)) return setError("Mật khẩu cần ít nhất 8 ký tự.");
    setError("");
    // Phase 1: no backend — go straight to the workspace.
    navigate("/app");
  };

  return (
    <div className="h-screen overflow-y-auto flex items-center justify-center px-5 py-10"
      style={{ background: "var(--bg-base)" }}>
      <div className="w-full max-w-[400px]">
        <Link to="/" className="inline-flex items-center gap-1.5 text-[13px] text-text-muted hover:text-brand mb-6 transition-theme">
          <Icon name="ArrowLeft" size={14} /> Về trang chủ
        </Link>

        <div className="surface-card !p-7">
          <div className="mb-6">
            <div className="font-mono text-[11px] tracking-[0.2em] uppercase text-text-muted mb-2">Phòng đọc</div>
            <h1 className="font-display text-[24px] font-semibold text-text-primary">Đăng nhập</h1>
            <p className="text-[13.5px] text-text-secondary mt-1">Tiếp tục làm việc với tài liệu của bạn.</p>
          </div>

          <form onSubmit={onSubmit} className="flex flex-col gap-3.5">
            <label className="flex flex-col gap-1.5">
              <span className="text-[12.5px] font-medium text-text-secondary">Email</span>
              <input type="email" autoComplete="email" value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="ban@vidu.com" className="input-surface text-[14px]" />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-[12.5px] font-medium text-text-secondary">Mật khẩu</span>
              <input type="password" autoComplete="current-password" value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••" className="input-surface text-[14px]" />
            </label>

            {error && (
              <div className="text-[12.5px] flex items-center gap-1.5" style={{ color: "var(--err)" }}>
                <Icon name="AlertCircle" size={13} /> {error}
              </div>
            )}

            <button type="submit" className="btn-seal w-full mt-1">Đăng nhập</button>
          </form>

          <p className="text-[13px] text-text-secondary text-center mt-5">
            Chưa có tài khoản?{" "}
            <Link to="/register" className="text-brand font-medium hover:underline">Tạo tài khoản</Link>
          </p>
        </div>

        <p className="text-[11px] text-text-muted text-center mt-4 font-mono">
          Bản xem trước · xác thực thật sẽ có ở giai đoạn sau
        </p>
      </div>
    </div>
  );
}
