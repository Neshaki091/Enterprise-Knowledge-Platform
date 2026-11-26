import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Building2, Eye, EyeOff, Lock, Mail, UserPlus, UserRound } from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { AuthLayout } from "@/features/auth/AuthLayout";
import { ApiError } from "@/lib/api-client";
import { slugify } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/feedback/alert";
import { Spinner } from "@/components/feedback/spinner";

const SLUG_PATTERN = /^[a-z0-9-]{2,100}$/;

/** Đăng ký tài khoản OWNER và khởi tạo doanh nghiệp (Tenant) mới, sau đó tự đăng nhập. */
export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [tenantName, setTenantName] = useState("");
  const [tenantSlug, setTenantSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!slugTouched) setTenantSlug(slugify(tenantName));
  }, [tenantName, slugTouched]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    setError(null);

    if (password !== confirmPassword) {
      setError("Mật khẩu xác nhận không khớp.");
      return;
    }
    if (password.length < 8) {
      setError("Mật khẩu cần tối thiểu 8 ký tự.");
      return;
    }
    if (!SLUG_PATTERN.test(tenantSlug)) {
      setError("Slug doanh nghiệp chỉ gồm chữ thường, số và dấu gạch nối (tối thiểu 2 ký tự).");
      return;
    }

    setSubmitting(true);
    try {
      await register({
        email: email.trim(),
        password,
        full_name: fullName.trim(),
        tenant_name: tenantName.trim(),
        tenant_slug: tenantSlug,
      });
      navigate("/workspace", { replace: true });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Không thể tạo tài khoản lúc này. Vui lòng thử lại sau.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AuthLayout>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Tạo doanh nghiệp trên nền tảng</CardTitle>
          <CardDescription>
            Tài khoản đầu tiên trở thành Chủ sở hữu (OWNER) của Tenant mới với gói khởi tạo FREE.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={(event) => void handleSubmit(event)} className="space-y-4">
            {error ? <Alert variant="destructive" title="Không thể đăng ký">{error}</Alert> : null}

            <div className="space-y-1.5">
              <Label htmlFor="fullName">Họ và tên</Label>
              <div className="relative">
                <UserRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="fullName"
                  required
                  minLength={2}
                  maxLength={255}
                  autoComplete="name"
                  placeholder="Nguyễn An"
                  className="pl-9"
                  value={fullName}
                  onChange={(event) => setFullName(event.target.value)}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="register-email">Email doanh nghiệp</Label>
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="register-email"
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="owner@congty.vn"
                  className="pl-9"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="register-password">Mật khẩu</Label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="register-password"
                    type={showPassword ? "text" : "password"}
                    required
                    minLength={8}
                    autoComplete="new-password"
                    placeholder="Tối thiểu 8 ký tự"
                    className="pl-9 pr-9"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((value) => !value)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:bg-muted"
                    aria-label={showPassword ? "Ẩn mật khẩu" : "Hiển thị mật khẩu"}
                  >
                    {showPassword ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                  </button>
                </div>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="confirm-password">Xác nhận</Label>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    id="confirm-password"
                    type={showPassword ? "text" : "password"}
                    required
                    autoComplete="new-password"
                    placeholder="Nhập lại"
                    className="pl-9"
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                  />
                </div>
              </div>
            </div>

            <div className="rounded-lg border bg-muted/40 p-3">
              <p className="mb-2.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                <Building2 className="h-3.5 w-3.5" /> Doanh nghiệp của bạn
              </p>
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="tenantName">Tên công ty</Label>
                  <Input
                    id="tenantName"
                    required
                    minLength={2}
                    maxLength={255}
                    placeholder="Công ty TNHH An Phát"
                    value={tenantName}
                    onChange={(event) => setTenantName(event.target.value)}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="tenantSlug">Slug định danh (URL)</Label>
                  <Input
                    id="tenantSlug"
                    required
                    minLength={2}
                    maxLength={100}
                    pattern="^[a-z0-9\-]+$"
                    placeholder="an-phat"
                    value={tenantSlug}
                    onChange={(event) => {
                      setSlugTouched(true);
                      setTenantSlug(event.target.value.toLowerCase());
                    }}
                  />
                  <p className="text-[10px] text-muted-foreground">
                    Định danh duy nhất toàn hệ thống. Tự sinh từ tên công ty, có thể chỉnh lại.
                  </p>
                </div>
              </div>
            </div>

            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? <Spinner /> : <UserPlus />}
              {submitting ? "Đang khởi tạo doanh nghiệp…" : "Đăng ký & tạo doanh nghiệp"}
            </Button>
          </form>

          <div className="mt-5 border-t pt-4 text-center text-xs text-muted-foreground">
            Đã có tài khoản?{" "}
            <Link to="/login" className="font-medium text-primary hover:underline">
              Đăng nhập
            </Link>
          </div>
        </CardContent>
      </Card>
    </AuthLayout>
  );
}
