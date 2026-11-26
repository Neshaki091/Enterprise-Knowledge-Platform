import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
}

/** Chặn lỗi render cục bộ để không trắng toàn bộ ứng dụng SPA. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Lỗi giao diện chưa được xử lý:", error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-8 text-center">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold">Giao diện gặp sự cố ngoài dự kiến</h2>
            <p className="text-sm text-muted-foreground">
              Dữ liệu của bạn vẫn an toàn. Hãy tải lại trang hoặc quay về trang tổng quan.
            </p>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => window.location.assign("/workspace")}>
              Về trang tổng quan
            </Button>
            <Button onClick={() => window.location.reload()}>Tải lại trang</Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
