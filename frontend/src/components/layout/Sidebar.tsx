import { NavLink } from "react-router-dom";
import { BrainCircuit, LogOut } from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { NAV_SECTIONS } from "@/lib/navigation";
import { roleLabel } from "@/lib/labels";
import { cn, initialsOf } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface SidebarProps {
  className?: string;
  onNavigate?: () => void;
}

/** Thanh điều hướng dọc: logo, menu theo RBAC và thẻ người dùng hiện tại. */
export function Sidebar({ className, onNavigate }: SidebarProps) {
  const { me, isAdmin, isSuperuser, logout } = useAuth();

  const visibleSections = NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter(
      (item) => (!item.adminOnly || isAdmin) && (!item.superuserOnly || isSuperuser),
    ),
  })).filter((section) => section.items.length > 0);

  return (
    <aside className={cn("flex h-full flex-col bg-sidebar text-sidebar-foreground", className)}>
      <div className="flex h-16 items-center gap-2.5 border-b border-sidebar-border px-5">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-cyan-400 shadow-lg shadow-indigo-950/40">
          <BrainCircuit className="h-5 w-5 text-white" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold tracking-tight text-white">KnowledgeHub</p>
          <p className="truncate text-[10px] uppercase tracking-[0.16em] text-sidebar-muted">Enterprise AI</p>
        </div>
      </div>

      <nav className="scrollbar-thin flex-1 space-y-6 overflow-y-auto px-3 py-5">
        {visibleSections.map((section) => (
          <div key={section.title} className="space-y-1">
            <p className="px-2.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-sidebar-muted/80">
              {section.title}
            </p>
            {section.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    "group flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition-colors",
                    isActive
                      ? "bg-sidebar-accent text-white shadow-sm"
                      : "text-sidebar-foreground/80 hover:bg-white/5 hover:text-white",
                  )
                }
              >
                <item.icon className="h-4 w-4 shrink-0 opacity-80 group-hover:opacity-100" />
                <span className="truncate">{item.label}</span>
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <div className="border-t border-sidebar-border p-3">
        <div className="flex items-center gap-2.5 rounded-lg bg-white/5 p-2.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-[11px] font-semibold text-white">
            {initialsOf(me?.email)}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium text-white">{me?.email ?? "—"}</p>
            <p className="truncate text-[10px] text-sidebar-muted">{roleLabel(me?.role)}</p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 text-sidebar-muted hover:bg-white/10 hover:text-white"
            title="Đăng xuất"
            onClick={() => void logout()}
          >
            <LogOut className="h-3.5 w-3.5" />
          </Button>
        </div>
        {me?.is_superuser ? (
          <Badge variant="violet" className="mt-2 w-full justify-center bg-violet-500/15 text-violet-200">
            Platform Super-Admin
          </Badge>
        ) : null}
      </div>
    </aside>
  );
}
