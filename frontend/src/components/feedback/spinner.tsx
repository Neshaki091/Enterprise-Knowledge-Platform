import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin", className)} aria-hidden="true" />;
}

/** Màn hình chờ toàn trang trong lúc xác thực phiên đăng nhập. */
export function FullPageLoader({ label = "Đang xác thực phiên làm việc…" }: { label?: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-4">
        <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-slate-950 shadow-pop">
          <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
        </div>
        <p className="text-sm text-muted-foreground">{label}</p>
      </div>
    </div>
  );
}
