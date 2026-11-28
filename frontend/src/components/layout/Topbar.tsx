import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LogOut, Menu, ServerCog } from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { systemApi } from "@/lib/api";
import { pageTitleFor } from "@/lib/navigation";
import { cn, initialsOf } from "@/lib/utils";
import { roleLabel } from "@/lib/labels";
import { useLocation } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { TenantSwitcher } from "./TenantSwitcher";

/** Chỉ báo sức khỏe API (CSDL + FAISS storage) tự làm mới mỗi 60 giây. */
function HealthIndicator() {
  const { data, isError } = useQuery({
    queryKey: ["system", "health"],
    queryFn: systemApi.health,
    refetchInterval: 60_000,
    retry: 1,
    staleTime: 30_000,
  });
  const healthy = !isError && data?.status === "healthy";
  return (
    <div
      className="hidden items-center gap-1.5 rounded-full border bg-card px-2.5 py-1 text-[10px] font-medium md:flex"
      title={data ? `CSDL: ${data.database} · FAISS: ${data.faiss_storage_ready ? "sẵn sàng" : "lỗi"} · API version v${data.version}` : "Chưa kết nối được API"}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", healthy ? "bg-emerald-500" : "bg-red-500")} />
      <span className={healthy ? "text-emerald-700" : "text-red-700"}>
        {healthy ? "Hệ thống ổn định" : "Mất kết nối API"}
      </span>
    </div>
  );
}

interface TopbarProps {
  onOpenSidebar: () => void;
}

/** Thanh trên cùng: điều hướng mobile, tiêu đề trang, trạng thái hệ thống, tenant switcher, menu người dùng. */
export function Topbar({ onOpenSidebar }: TopbarProps) {
  const { me, logout } = useAuth();
  const location = useLocation();
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const title = pageTitleFor(location.pathname);

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-card/85 px-4 backdrop-blur sm:px-6">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        onClick={onOpenSidebar}
        aria-label="Mở menu điều hướng"
      >
        <Menu />
      </Button>

      <div className="min-w-0 flex-1">
        <p className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          KnowledgeHub <span className="mx-1">/</span> {title}
        </p>
        <h2 className="truncate text-sm font-semibold leading-tight">{title}</h2>
      </div>

      <HealthIndicator />
      <TenantSwitcher />

      <div className="relative">
        <button
          type="button"
          onClick={() => setUserMenuOpen((value) => !value)}
          className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-[11px] font-semibold text-white ring-2 ring-white transition-transform hover:scale-105"
          aria-haspopup="menu"
          aria-expanded={userMenuOpen}
          aria-label="Mở menu người dùng"
        >
          {initialsOf(me?.email)}
        </button>
        {userMenuOpen ? (
          <>
            <button
              type="button"
              className="fixed inset-0 z-30 cursor-default"
              aria-hidden="true"
              tabIndex={-1}
              onClick={() => setUserMenuOpen(false)}
            />
            <div
              role="menu"
              className="absolute right-0 z-40 mt-2 w-64 animate-fade-in overflow-hidden rounded-xl border bg-popover shadow-pop"
            >
              <div className="border-b px-4 py-3">
                <p className="truncate text-xs font-medium">{me?.email}</p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">
                  {roleLabel(me?.role)} · {me?.is_superuser ? "Platform Super-Admin" : "Tenant Workspace"}
                </p>
              </div>
              <div className="p-1.5">
                <div className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[11px] text-muted-foreground">
                  <ServerCog className="h-3.5 w-3.5" />
                  Tenant: {me?.tenant_id.slice(0, 8)}…
                </div>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => void logout()}
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-medium text-red-600 transition-colors hover:bg-red-50"
                >
                  <LogOut className="h-3.5 w-3.5" /> Đăng xuất khỏi phiên này
                </button>
              </div>
            </div>
          </>
        ) : null}
      </div>
    </header>
  );
}
