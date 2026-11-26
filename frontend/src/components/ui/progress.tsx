import { cn } from "@/lib/utils";

interface ProgressProps {
  value: number;
  className?: string;
  indicatorClassName?: string;
  label?: string;
}

/** Thanh tiến trình với ngưỡng màu cảnh báo (xanh < 70%, hổ phách < 90%, đỏ ≥ 90%). */
export function Progress({ value, className, indicatorClassName, label }: ProgressProps) {
  const clamped = Math.min(100, Math.max(0, value));
  const tone =
    indicatorClassName ??
    (clamped >= 90 ? "bg-red-500" : clamped >= 70 ? "bg-amber-500" : "bg-emerald-500");
  return (
    <div
      className={cn("h-2 w-full overflow-hidden rounded-full bg-muted", className)}
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label ?? "Tiến trình"}
    >
      <div
        className={cn("h-full rounded-full transition-all duration-500", tone)}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
