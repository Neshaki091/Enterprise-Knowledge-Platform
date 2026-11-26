import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./auth-context";
import { FullPageLoader } from "@/components/feedback/spinner";

/** Chặn truy cập khi chưa đăng nhập; giữ vị trí ban đầu để quay lại sau khi đăng nhập. */
export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") return <FullPageLoader />;
  if (status === "unauthenticated") {
    return <Navigate to="/login" replace state={{ from: `${location.pathname}${location.search}` }} />;
  }
  return <Outlet />;
}

/** Chỉ OWNER/ADMIN (hoặc Super-Admin) được vào khu quản trị doanh nghiệp. */
export function RequireAdmin() {
  const { me } = useAuth();
  const role = me?.role.toUpperCase() ?? "";
  const allowed = Boolean(me?.is_superuser) || role === "OWNER" || role === "ADMIN";
  if (!allowed) return <Navigate to="/forbidden" replace />;
  return <Outlet />;
}

/** Chỉ Platform Super-Admin được vào khu quản trị nền tảng. */
export function RequireSuperuser() {
  const { me } = useAuth();
  if (!me?.is_superuser) return <Navigate to="/forbidden" replace />;
  return <Outlet />;
}

/** Chuyển hướng người đã đăng nhập khỏi trang /login, /register. */
export function PublicOnly() {
  const { status } = useAuth();
  if (status === "loading") return <FullPageLoader label="Đang kiểm tra phiên đăng nhập…" />;
  if (status === "authenticated") return <Navigate to="/workspace" replace />;
  return <Outlet />;
}
