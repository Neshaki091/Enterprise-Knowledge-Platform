import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Trang 404 cho đường dẫn không tồn tại trong SPA. */
export function NotFoundPage() {
  return (
    <div className="flex min-h-[65vh] flex-col items-center justify-center gap-5 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl border bg-muted/60">
        <Compass className="h-7 w-7 text-muted-foreground" />
      </div>
      <div className="space-y-1.5">
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">404 Not Found</p>
        <h1 className="text-xl font-semibold tracking-tight">Không tìm thấy trang bạn yêu cầu</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          Đường dẫn có thể đã thay đổi hoặc bạn không còn quyền truy cập tài nguyên này.
        </p>
      </div>
      <Button onClick={() => { window.location.assign("/workspace"); }}>
        Về trang tổng quan
      </Button>
      <Link to="/chat" className="text-xs text-muted-foreground underline-offset-4 hover:underline">
        Đến trợ lý tri thức
      </Link>
    </div>
  );
}
