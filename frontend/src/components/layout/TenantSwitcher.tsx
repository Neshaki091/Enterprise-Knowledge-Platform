import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Building2, Check, ChevronsUpDown, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/features/auth/auth-context";
import { authApi } from "@/lib/api";
import { ApiError } from "@/lib/api-client";
import type { MembershipSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { roleLabel } from "@/lib/labels";
import { Badge } from "@/components/ui/badge";

/** Bộ chuyển workspace đa tenant: liệt kê membership và cấp lại token khi đổi. */
export function TenantSwitcher() {
  const { me, switchTenant } = useAuth();
  const [open, setOpen] = useState(false);
  const [switchingId, setSwitchingId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const { data: memberships = [], isLoading } = useQuery({
    queryKey: ["auth", "memberships"],
    queryFn: authApi.memberships,
    staleTime: 60_000,
  });

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent): void => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const active = memberships.find((item) => item.tenant_id === me?.tenant_id);
  const currentName = active?.tenant_name ?? "Không gian làm việc";
  const currentRole = active?.role ?? me?.role ?? "";

  const handleSelect = async (membership: MembershipSummary): Promise<void> => {
    if (!membership.is_active || membership.tenant_id === me?.tenant_id) {
      setOpen(false);
      return;
    }
    setSwitchingId(membership.tenant_id);
    try {
      await switchTenant(membership.tenant_id);
      setOpen(false);
      toast.success(`Đã chuyển sang ${membership.tenant_name}`);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "Không thể chuyển workspace");
    } finally {
      setSwitchingId(null);
    }
  };

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex h-9 items-center gap-2 rounded-lg border bg-card px-2.5 text-left shadow-sm transition-colors hover:bg-accent"
      >
        <div className="flex h-6 w-6 items-center justify-center rounded-md bg-indigo-50 text-indigo-600">
          <Building2 className="h-3.5 w-3.5" />
        </div>
        <div className="hidden min-w-0 sm:block">
          <p className="max-w-[160px] truncate text-xs font-medium leading-tight">{currentName}</p>
          <p className="text-[10px] leading-tight text-muted-foreground">{roleLabel(currentRole)}</p>
        </div>
        <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      </button>

      {open ? (
        <div
          role="listbox"
          className="absolute right-0 z-40 mt-2 w-72 animate-fade-in overflow-hidden rounded-xl border bg-popover shadow-pop"
        >
          <div className="border-b px-3 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Doanh nghiệp của bạn
            </p>
          </div>
          <div className="max-h-72 overflow-y-auto p-1.5">
            {isLoading ? (
              <div className="flex items-center justify-center gap-2 px-3 py-6 text-xs text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Đang tải danh sách…
              </div>
            ) : memberships.length === 0 ? (
              <p className="px-3 py-4 text-xs text-muted-foreground">Chưa có membership nào.</p>
            ) : (
              memberships.map((membership) => {
                const isActive = membership.tenant_id === me?.tenant_id;
                return (
                  <button
                    key={membership.tenant_id}
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    disabled={!membership.is_active || switchingId !== null}
                    onClick={() => void handleSelect(membership)}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors",
                      isActive ? "bg-accent" : "hover:bg-muted",
                      !membership.is_active && "cursor-not-allowed opacity-50",
                    )}
                  >
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border bg-card text-xs font-semibold uppercase">
                      {membership.tenant_slug.slice(0, 2)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-xs font-medium">{membership.tenant_name}</p>
                      <p className="truncate text-[10px] text-muted-foreground">
                        {membership.tenant_slug} · {roleLabel(membership.role)}
                      </p>
                    </div>
                    {switchingId === membership.tenant_id ? (
                      <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                    ) : isActive ? (
                      <Check className="h-4 w-4 text-emerald-600" />
                    ) : !membership.is_active ? (
                      <Badge variant="destructive">Khóa</Badge>
                    ) : null}
                  </button>
                );
              })
            )}
          </div>
          <div className="border-t bg-muted/40 px-3 py-2 text-[10px] text-muted-foreground">
            Chuyển workspace sẽ cấp lại token và xóa bộ đệm dữ liệu cũ.
          </div>
        </div>
      ) : null}
    </div>
  );
}
