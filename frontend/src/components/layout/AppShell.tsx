import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Outlet, useLocation } from "react-router-dom";
import { X } from "lucide-react";
import { ErrorBoundary } from "@/components/feedback/error-boundary";
import { Button } from "@/components/ui/button";
import { systemApi } from "@/lib/api";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

/** Khung ứng dụng: sidebar cố định desktop, drawer mobile, topbar và vùng nội dung. */
export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const versionQuery = useQuery({
    queryKey: ["system", "version"],
    queryFn: systemApi.version,
    staleTime: Infinity,
    retry: 1,
  });
  const apiVersion = versionQuery.data
    ? `v${versionQuery.data.version}`
    : versionQuery.isError
      ? "không khả dụng"
      : "đang tải…";

  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [mobileOpen]);

  return (
    <div className="min-h-screen bg-background">
      <Sidebar className="fixed inset-y-0 left-0 z-30 hidden w-64 lg:flex" />

      {mobileOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 animate-fade-in bg-slate-950/50"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
          <div className="absolute inset-y-0 left-0 w-64 animate-slide-in shadow-pop">
            <Sidebar onNavigate={() => setMobileOpen(false)} />
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setMobileOpen(false)}
              className="absolute right-2 top-3 h-8 w-8 text-sidebar-muted hover:bg-white/10 hover:text-white"
              aria-label="Đóng menu"
            >
              <X />
            </Button>
          </div>
        </div>
      ) : null}

      <div className="flex min-h-screen flex-col lg:pl-64">
        <Topbar onOpenSidebar={() => setMobileOpen(true)} />
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-7xl">
            <ErrorBoundary>
              <Outlet />
            </ErrorBoundary>
          </div>
        </main>
        <footer className="border-t px-6 py-3">
          <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center justify-between gap-2 text-[10px] text-muted-foreground">
            <span title={versionQuery.data?.service}>
              Enterprise AI Knowledge Platform · API version {apiVersion}
            </span>
            <span>Multi-Tenant · PostgreSQL 16 RLS · FAISS mmap · 1.0 GB RAM budget</span>
          </div>
        </footer>
      </div>
    </div>
  );
}
