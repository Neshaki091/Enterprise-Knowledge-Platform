import type { ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/utils";

type AlertVariant = "info" | "success" | "warning" | "destructive";

interface AlertProps {
  variant?: AlertVariant;
  title?: string;
  children: ReactNode;
  className?: string;
}

const variantStyles: Record<AlertVariant, { wrapper: string; icon: typeof Info }> = {
  info: { wrapper: "border-sky-200 bg-sky-50 text-sky-900", icon: Info },
  success: { wrapper: "border-emerald-200 bg-emerald-50 text-emerald-900", icon: CheckCircle2 },
  warning: { wrapper: "border-amber-200 bg-amber-50 text-amber-900", icon: TriangleAlert },
  destructive: { wrapper: "border-red-200 bg-red-50 text-red-900", icon: AlertCircle },
};

export function Alert({ variant = "info", title, children, className }: AlertProps) {
  const { wrapper, icon: Icon } = variantStyles[variant];
  return (
    <div role="alert" className={cn("flex gap-2.5 rounded-lg border px-3.5 py-2.5 text-xs", wrapper, className)}>
      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="space-y-0.5 leading-relaxed">
        {title ? <p className="font-semibold">{title}</p> : null}
        <div>{children}</div>
      </div>
    </div>
  );
}
