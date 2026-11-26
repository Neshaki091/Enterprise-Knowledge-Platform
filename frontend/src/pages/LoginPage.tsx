import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Eye, EyeOff, Lock, LogIn, Mail } from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { AuthLayout } from "@/features/auth/AuthLayout";
import { ApiError } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/feedback/alert";
import { Spinner } from "@/components/feedback/spinner";

/** Đăng nhập email/mật khẩu; chống dò mật khẩu phía backend trả 429 kèm Retry-After. */
export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const from = (location.state as { from?: string } | null)?.from ?? "/workspace";

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Không thể kết nối máy chủ. Vui lòng kiểm tra đường truyền và thử lại.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthLayout>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Đăng nhập workspace</CardTitle>
          <CardDescription>
            Sử dụng tài khoản doanh nghiệp của bạn để truy cập kho tri thức được phân quyền.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={(event) => void handleSubmit(event)} className="space-y-4">
            {error ? <Alert variant="destructive" title="Đăng nhập thất bại">{error}</Alert> : null}

            <div className="space-y-1.5">
              <Label htmlFor="email">Email doanh nghiệp</Label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  placeholder="ban@congty.vn"
                  className="pl-9"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password">Mật khẩu</Label>
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  placeholder="••••••••"
                  className="pl-9 pr-10"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((value) => !value)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  aria-label={showPassword ? "Ẩn mật khẩu" : "Hiển thị mật khẩu"}
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? <Spinner /> : <LogIn />}
              {submitting ? "Đang xác thực…" : "Đăng nhập"}
            </Button>
          </form>

          <div className="mt-5 border-t pt-4 text-center text-xs text-muted-foreground">
            Chưa có tài khoản doanh nghiệp?{" "}
            <Link to="/register" className="font-medium text-primary hover:underline">
              Đăng ký và tạo Tenant
            </Link>
          </div>
        </CardContent>
      </Card>

      <p className="mt-4 text-center text-[10px] leading-relaxed text-muted-foreground">
        Hệ thống ghi nhận mọi lần đăng nhập thất bại. Sau 5 lần sai trên cùng email/IP trong 5 phút,
        tài khoản tạm bị chặn theo chính sách chống dò mật khẩu.
      </p>
    </AuthLayout>
  );
}
