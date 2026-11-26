import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: ReactNode;
  hint?: string;
  icon?: LucideIcon;
  tone?: "indigo" | "emerald" | "sky" | "amber" | "violet" | "slate";
  className?: string;
}

const toneClasses: Record<NonNullable<StatCardProps["tone"]>, string> = {
  indigo: "bg-indigo-50 text-indigo-600",
  emerald: "bg-emerald-50 text-emerald-600",
  sky: "bg-sky-50 text-sky-600",
  amber: "bg-amber-50 text-amber-600",
  violet: "bg-violet-50 text-violet-600",
  slate: "bg-slate-100 text-slate-600",
};

export function StatCard({ label, value, hint, icon: Icon, tone = "indigo", className }: StatCardProps) {
  return (
    <div className={cn("rounded-xl border bg-card p-5 shadow-card", className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
          <p className="text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
        </div>
        {Icon ? (
          <div className={cn("flex h-9 w-9 items-center justify-center rounded-lg", toneClasses[tone])}>
            <Icon className="h-4 w-4" />
          </div>
        ) : null}
      </div>
      {hint ? <p className="mt-2 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
