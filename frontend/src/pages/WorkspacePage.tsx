import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  FileStack,
  FolderTree,
  MessageSquareText,
  Search,
  ShieldCheck,
  Sparkles,
  UserRound,
} from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { dashboardApi } from "@/lib/api";
import { describeApiError } from "@/lib/api-client";
import { roleLabel } from "@/lib/labels";
import { formatNumber } from "@/lib/utils";
import { PageHeader } from "@/components/layout/page-header";
import { StatCard } from "@/components/layout/stat-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/feedback/alert";
import { EmptyState } from "@/components/feedback/empty-state";

const QUICK_ACTIONS = [
  {
    to: "/chat",
    icon: MessageSquareText,
    title: "Hỏi trợ lý tri thức",
    description: "Đặt câu hỏi và nhận câu trả lời streaming kèm trích dẫn nguồn tài liệu.",
    accent: "bg-indigo-50 text-indigo-600",
  },
  {
    to: "/search",
    icon: Search,
    title: "Tìm kiếm lai",
    description: "Kết hợp FAISS ngữ nghĩa và PostgreSQL FTS qua Reciprocal Rank Fusion.",
    accent: "bg-sky-50 text-sky-600",
  },
  {
    to: "/documents",
    icon: FileStack,
    title: "Kho tài liệu",
    description: "Tải lên PDF/DOCX/XLSX/TXT/MD, xem phân cấp Parent-Child và quản lý nguồn.",
    accent: "bg-emerald-50 text-emerald-600",
  },
];

/** Dashboard cấp 3 (Employee Workspace): thông tin phiên làm việc và lối tắt nghiệp vụ. */
export function WorkspacePage() {
  const { me } = useAuth();
  const workspaceQuery = useQuery({
    queryKey: ["dashboard", "workspace"],
    queryFn: dashboardApi.workspace,
  });

  const data = workspaceQuery.data;
  const displayName = data?.user_name ?? me?.email.split("@")[0] ?? "bạn";

  return (
    <div className="space-y-6">
      <PageHeader
        title={`Xin chào, ${displayName}`}
        description="Không gian làm việc cá nhân trong doanh nghiệp của bạn. Mọi tìm kiếm và câu trả lời đều bị giới hạn trong phạm vi được cấp quyền."
        actions={
          data ? (
            <Badge variant="success" className="h-7 px-3">
              <span className="mr-1 h-1.5 w-1.5 rounded-full bg-emerald-500" /> Phiên hợp lệ
            </Badge>
          ) : null
        }
      />

      {workspaceQuery.isError ? (
        <Alert variant="destructive" title="Không tải được thông tin workspace">
          {describeApiError(workspaceQuery.error, "Vui lòng thử tải lại trang.")}
        </Alert>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {workspaceQuery.isLoading
          ? Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-[104px] rounded-xl" />)
          : (
            <>
              <StatCard
                label="Doanh nghiệp"
                value={data?.tenant_name ?? "—"}
                hint={data?.role ? roleLabel(data.role) : undefined}
                icon={FolderTree}
                tone="indigo"
              />
              <StatCard
                label="Phòng ban"
                value={data?.department_name ?? "Toàn doanh nghiệp"}
                hint={data?.department_name ? "Phạm vi dữ liệu được giới hạn theo phòng ban" : "Bạn đang truy cập tài liệu toàn Tenant"}
                icon={UserRound}
                tone="sky"
              />
              <StatCard
                label="Tài liệu có thể tra cứu"
                value={formatNumber(data?.accessible_documents_count ?? 0)}
                hint="Đã áp dụng ACL phòng ban và phạm vi Tenant"
                icon={FileStack}
                tone="emerald"
              />
              <StatCard
                label="Vai trò của bạn"
                value={roleLabel(data?.role ?? me?.role)}
                hint={data?.email ?? me?.email ?? ""}
                icon={ShieldCheck}
                tone="violet"
              />
            </>
          )}
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {QUICK_ACTIONS.map((action) => (
          <Link
            key={action.to}
            to={action.to}
            className="group rounded-xl border bg-card p-5 shadow-card transition-all hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-pop"
          >
            <div className={`mb-3 flex h-10 w-10 items-center justify-center rounded-lg ${action.accent}`}>
              <action.icon className="h-5 w-5" />
            </div>
            <p className="flex items-center gap-1 text-sm font-semibold">
              {action.title}
              <ArrowRight className="h-3.5 w-3.5 -translate-x-1 opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100" />
            </p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{action.description}</p>
          </Link>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-1.5">
            <Sparkles className="h-4 w-4 text-indigo-500" /> Cam kết bảo mật dữ liệu doanh nghiệp
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 text-xs text-muted-foreground md:grid-cols-3">
          <div className="space-y-1">
            <p className="font-semibold text-foreground">Cách ly đa khách thuê</p>
            <p className="leading-relaxed">
              Mọi truy vấn đều gắn Tenant Context và đối soát Membership; PostgreSQL RLS bảo vệ thêm ở tầng CSDL.
            </p>
          </div>
          <div className="space-y-1">
            <p className="font-semibold text-foreground">Không lưu file gốc</p>
            <p className="leading-relaxed">
              Tài liệu được băm SHA-256, bóc tách văn bản rồi hủy buffer nhị phân ngay trong RAM (Zero Raw Storage).
            </p>
          </div>
          <div className="space-y-1">
            <p className="font-semibold text-foreground">Trích dẫn kiểm chứng được</p>
            <p className="leading-relaxed">
              Câu trả lời RAG luôn kèm chunks và citations; không có ngữ cảnh phù hợp, hệ thống từ chối suy đoán.
            </p>
          </div>
        </CardContent>
      </Card>

      {workspaceQuery.isError ? (
        <EmptyState
          icon={ShieldCheck}
          title="Phiên đăng nhập có thể đã thay đổi"
          description="Nếu bạn vừa bị quản trị viên thay đổi vai trò hoặc phòng ban, hãy tải lại trang để nhận quyền mới."
        />
      ) : null}
    </div>
  );
}
