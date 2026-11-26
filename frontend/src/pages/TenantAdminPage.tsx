import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  FileStack,
  FolderTree,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
  ThumbsDown,
  ThumbsUp,
  Users,
  Wifi,
  WifiOff,
} from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { dashboardApi } from "@/lib/api";
import { describeApiError } from "@/lib/api-client";
import { formatBytes, formatNumber, formatPercent } from "@/lib/utils";
import type { TenantAnalytics } from "@/lib/types";
import { useRealtimeSnapshot, type LiveStatus } from "@/features/dashboard/use-realtime-snapshot";
import { PageHeader } from "@/components/layout/page-header";
import { StatCard } from "@/components/layout/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/feedback/alert";
import {
  ModelLimitsCards,
  QuotaMeters,
  RealtimeMetricsCard,
  StorageArchitectureNote,
  StorageSplitCard,
} from "@/components/dashboard/analytics-widgets";
import { TelemetryLogsCard } from "@/components/dashboard/telemetry-logs-card";

function isTenantAnalytics(payload: unknown): payload is TenantAnalytics {
  if (typeof payload !== "object" || payload === null) return false;
  const record = payload as Record<string, unknown>;
  return (
    typeof record.tenant_id === "string" &&
    typeof record.storage === "object" &&
    record.storage !== null &&
    typeof record.live_quota === "object" &&
    record.live_quota !== null
  );
}

function LiveBadge({ status }: { status: LiveStatus }) {
  if (status === "live") {
    return (
      <Badge variant="success">
        <Wifi className="h-3 w-3" /> SSE realtime
      </Badge>
    );
  }
  if (status === "connecting") {
    return (
      <Badge variant="info">
        <RefreshCw className="h-3 w-3 animate-spin" /> Đang kết nối
      </Badge>
    );
  }
  if (status === "paused") {
    return (
      <Badge variant="outline">
        <Pause className="h-3 w-3" /> Live tạm dừng
      </Badge>
    );
  }
  return (
    <Badge variant="destructive">
      <WifiOff className="h-3 w-3" /> Mất luồng live
    </Badge>
  );
}

function quotaValue(quota: Record<string, unknown>, key: string, fallback: number): number {
  const value = quota[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** Tenant Admin Console (Tier 2): lưu trữ DB/FAISS, hạn ngạch realtime, hiệu năng và audit logs. */
export function TenantAdminPage() {
  const { me } = useAuth();
  const tenantKey = me?.tenant_id ?? "";
  const [eventType, setEventType] = useState("");
  const [limit, setLimit] = useState(50);

  const dashboardQuery = useQuery({
    queryKey: ["dashboard", "tenant", tenantKey],
    queryFn: dashboardApi.tenant,
  });
  const analyticsQuery = useQuery({
    queryKey: ["dashboard", "tenant", "analytics", tenantKey],
    queryFn: dashboardApi.tenantAnalytics,
  });
  const logsQuery = useQuery({
    queryKey: ["dashboard", "tenant", "logs", tenantKey, eventType, limit],
    queryFn: () => dashboardApi.tenantLogs(eventType || undefined, limit),
  });

  const live = useRealtimeSnapshot<TenantAnalytics>(
    dashboardApi.tenantRealtimePath(3),
    tenantKey,
    isTenantAnalytics,
  );

  const dashboard = dashboardQuery.data;
  const analytics = live.snapshot ?? analyticsQuery.data;
  const refreshing = dashboardQuery.isFetching || analyticsQuery.isFetching || logsQuery.isFetching;

  const thumbsUp = dashboard?.feedback_summary.thumbs_up ?? 0;
  const thumbsDown = dashboard?.feedback_summary.thumbs_down ?? 0;
  const feedbackTotal = thumbsUp + thumbsDown;
  const helpfulPercent = feedbackTotal > 0 ? (thumbsUp / feedbackTotal) * 100 : null;
  const quota = dashboard?.quota_limits ?? {};

  const refreshAll = (): void => {
    void dashboardQuery.refetch();
    void analyticsQuery.refetch();
    void logsQuery.refetch();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Quản trị doanh nghiệp"
        description="Bảng điều khiển Tenant Admin/Owner: dung lượng PostgreSQL vs FAISS, hạn ngạch 30 RPM / 16K TPM, độ trễ P95, mức nén ngữ cảnh và nhật ký vận hành đã khử nội dung thô."
        actions={
          <>
            <LiveBadge status={live.status} />
            <Button variant="outline" size="sm" onClick={() => live.setEnabled(!live.enabled)}>
              {live.enabled ? <Pause /> : <Play />} {live.enabled ? "Tạm dừng live" : "Bật live"}
            </Button>
            <Button variant="outline" size="sm" onClick={refreshAll} disabled={refreshing}>
              <RefreshCw className={refreshing ? "animate-spin" : undefined} /> Làm mới
            </Button>
          </>
        }
      />

      {dashboardQuery.isError || analyticsQuery.isError ? (
        <Alert variant="destructive" title="Không tải được dữ liệu quản trị">
          {describeApiError(
            dashboardQuery.error ?? analyticsQuery.error,
            "Vui lòng kiểm tra kết nối hoặc quyền Tenant Admin.",
          )}
        </Alert>
      ) : null}

      {live.status === "error" ? (
        <Alert variant="warning" title="Luồng realtime bị gián đoạn">
          Hệ thống sẽ tự thử kết nối lại sau vài giây. Nếu lỗi kéo dài, bấm tạm dừng rồi bật live lại. Số liệu trên trang vẫn
          là ảnh chụp gần nhất từ REST API.
        </Alert>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {dashboardQuery.isLoading ? (
          Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-[104px] rounded-xl" />)
        ) : (
          <>
            <StatCard
              label="Thành viên hoạt động"
              value={formatNumber(dashboard?.total_members ?? 0)}
              hint={`${formatNumber(dashboard?.departments_count ?? 0)} phòng ban`}
              icon={Users}
              tone="indigo"
            />
            <StatCard
              label="Tài liệu trong Tenant"
              value={formatNumber(dashboard?.total_documents ?? 0)}
              hint="Đã bóc tách, không lưu file gốc"
              icon={FileStack}
              tone="emerald"
            />
            <StatCard
              label="Dung lượng tệp đã tải"
              value={formatBytes(dashboard?.total_storage_bytes ?? 0)}
              hint="Tổng kích thước lúc nạp (Zero Raw Storage)"
              icon={FolderTree}
              tone="sky"
            />
            <StatCard
              label="Phản hồi hữu ích"
              value={helpfulPercent !== null ? formatPercent(helpfulPercent) : "—"}
              hint={`${formatNumber(thumbsUp)} hữu ích · ${formatNumber(thumbsDown)} chưa hữu ích`}
              icon={Activity}
              tone="violet"
            />
          </>
        )}
      </div>

      {analytics ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <StorageSplitCard storage={analytics.storage} />
          <QuotaMeters quota={analytics.live_quota} />
        </div>
      ) : analyticsQuery.isLoading ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <Skeleton className="h-[420px] rounded-xl" />
          <Skeleton className="h-[420px] rounded-xl" />
        </div>
      ) : null}

      {analytics ? (
        <RealtimeMetricsCard metrics={analytics.realtime_metrics} />
      ) : null}

      {analytics ? <ModelLimitsCards limits={analytics.model_api_limits} /> : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="space-y-1">
            <CardTitle className="flex items-center gap-1.5">
              <ThumbsUp className="h-4 w-4 text-emerald-500" /> Mức độ hài lòng của nhân viên
            </CardTitle>
            <CardDescription>Tổng hợp đánh giá THUMBS_UP / THUMBS_DOWN từ Chat Feedback.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-end justify-between">
              <div>
                <p className="text-3xl font-semibold tabular-nums">
                  {helpfulPercent !== null ? formatPercent(helpfulPercent) : "—"}
                </p>
                <p className="text-xs text-muted-foreground">tỷ lệ câu trả lời được đánh giá hữu ích</p>
              </div>
              <Badge variant={helpfulPercent !== null && helpfulPercent >= 80 ? "success" : "outline"}>
                {formatNumber(feedbackTotal)} đánh giá
              </Badge>
            </div>
            <Progress value={helpfulPercent ?? 0} label="Tỷ lệ hài lòng" />
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="flex items-center justify-between rounded-lg border bg-muted/20 px-3 py-2">
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <ThumbsUp className="h-3 w-3 text-emerald-600" /> Hữu ích
                </span>
                <span className="font-semibold tabular-nums">{formatNumber(thumbsUp)}</span>
              </div>
              <div className="flex items-center justify-between rounded-lg border bg-muted/20 px-3 py-2">
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <ThumbsDown className="h-3 w-3 text-red-500" /> Chưa hữu ích
                </span>
                <span className="font-semibold tabular-nums">{formatNumber(thumbsDown)}</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="space-y-1">
            <CardTitle className="flex items-center gap-1.5">
              <ShieldCheck className="h-4 w-4 text-indigo-500" /> Gói dịch vụ & hạn ngạch
            </CardTitle>
            <CardDescription>
              Hạn ngạch hiện hành của {dashboard?.tenant_name ?? "doanh nghiệp"} (áp dụng tức thời cho mọi nhân viên).
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="violet">{String(quota.tier ?? "FREE")}</Badge>
              <Badge variant="outline">RPM {formatNumber(quotaValue(quota, "rpm_limit", 30))}</Badge>
              <Badge variant="outline">TPM {formatNumber(quotaValue(quota, "tpm_limit", 16000))}</Badge>
              <Badge variant="outline">
                {quotaValue(quota, "max_users", 0) === 0
                  ? "Người dùng ∞"
                  : `${formatNumber(quotaValue(quota, "max_users", 10))} người dùng`}
              </Badge>
              <Badge variant="outline">
                {quotaValue(quota, "max_documents", 0) === 0
                  ? "Tài liệu ∞"
                  : `${formatNumber(quotaValue(quota, "max_documents", 50))} tài liệu`}
              </Badge>
            </div>
            <div className="rounded-lg border bg-muted/20 p-3 text-xs leading-relaxed text-muted-foreground">
              <p className="font-medium text-foreground">Mã Tenant: {dashboard?.tenant_slug ?? "—"}</p>
              <p className="mt-1">
                Token bucket chạy trong tiến trình FastAPI duy nhất. Khi vượt hạn ngạch, người dùng nhận HTTP 429 kèm
                thời gian chờ; hiện tượng này được đếm ở thẻ &quot;Chặn RPM/TPM&quot; phía trên.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      <TelemetryLogsCard
        title="Nhật ký vận hành của doanh nghiệp"
        description="Chỉ hiển thị sự kiện thuộc Tenant hiện tại; lọc theo loại sự kiện để điều tra bất thường."
        data={logsQuery.data}
        isLoading={logsQuery.isLoading}
        isFetching={logsQuery.isFetching}
        error={logsQuery.error}
        eventType={eventType}
        onEventTypeChange={setEventType}
        limit={limit}
        onLimitChange={setLimit}
        onRefresh={() => void logsQuery.refetch()}
      />

      <StorageArchitectureNote />
    </div>
  );
}
