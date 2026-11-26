import { Link } from "react-router-dom";
import { ArrowLeft, ShieldX } from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { roleLabel } from "@/lib/labels";
import { Button } from "@/components/ui/button";

/** Trang 403: chặn truy cập khu vực ngoài quyền RBAC hiện tại. */
export function ForbiddenPage() {
  const { me } = useAuth();
  return (
    <div className="flex min-h-[65vh] flex-col items-center justify-center gap-5 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-red-100 bg-red-50">
        <ShieldX className="h-7 w-7 text-red-500" />
      </div>
      <div className="space-y-1.5">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-red-500">403 Forbidden</p>
        <h1 className="text-xl font-semibold tracking-tight">Bạn không có quyền truy cập khu vực này</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          Vai trò hiện tại của bạn là <span className="font-medium text-foreground">{roleLabel(me?.role)}</span>.
          Vui lòng liên hệ Quản trị viên doanh nghiệp nếu bạn cần được cấp thêm quyền.
        </p>
      </div>
      <div className="flex gap-2">
        <Button variant="outline" onClick={() => window.history.back()}>
          <ArrowLeft className="mr-1.5 h-4 w-4" /> Quay lại
        </Button>
        <Button onClick={() => { window.location.assign("/workspace"); }}>
          Về trang tổng quan
        </Button>
      </div>
      <Link to="/workspace" className="text-xs text-muted-foreground underline-offset-4 hover:underline">
        Hoặc quay về workspace của bạn
      </Link>
    </div>
  );
}
