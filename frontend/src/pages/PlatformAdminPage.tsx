import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Activity,
  Building2,
  Database,
  FileStack,
  Pause,
  Play,
  RefreshCw,
  ScrollText,
  Server,
  ShieldCheck,
  Users,
  Wifi,
  WifiOff,
} from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { dashboardApi } from "@/lib/api";
import { describeApiError } from "@/lib/api-client";
import { formatBytes, formatNumber, formatPercent, formatDateTime } from "@/lib/utils";
import type { PlatformAnalytics } from "@/lib/types";
import { useRealtimeSnapshot, type LiveStatus } from "@/features/dashboard/use-realtime-snapshot";
import { PageHeader } from "@/components/layout/page-header";
import { StatCard } from "@/components/layout/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert } from "@/components/feedback/alert";
import { EmptyState } from "@/components/feedback/empty-state";
import {
  ModelLimitsCards,
  RealtimeMetricsCard,
  Sec026Notice,
  StorageArchitectureNote,
} from "@/components/dashboard/analytics-widgets";
import { TelemetryLogsCard } from "@/components/dashboard/telemetry-logs-card";

function isPlatformAnalytics(payload: unknown): payload is PlatformAnalytics {
  if (typeof payload !== "object" || payload === null) return false;
  const record = payload as Record<string, unknown>;
  return (
    typeof record.storage_summary === "object" &&
    record.storage_summary !== null &&
    Array.isArray(record.tenants_breakdown) &&
    typeof record.realtime_metrics === "object" &&
    record.realtime_metrics !== null
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

function quotaTone(percent: number): string {
  if (percent >= 90) return "text-red-600";
  if (percent >= 70) return "text-amber-600";
  return "text-emerald-600";
}

interface PlatformStorageCardProps {
  summary: Record<string, number>;
  measuredAt: string | null;
}

function PlatformStorageCard({ summary, measuredAt }: PlatformStorageCardProps) {
  const dbBytes = Math.max(0, summary.total_db_text_bytes ?? 0);
  const faissBytes = Math.max(0, summary.total_faiss_bytes ?? 0);
  const total = Math.max(1, dbBytes + faissBytes);
  const dbPercent = (dbBytes / total) * 100;

  const tiles: { label: string; value: string; hint?: string }[] = [
    { label: "Doanh nghiệp", value: formatNumber(summary.total_tenants ?? 0) },
    { label: "Tài liệu", value: formatNumber(summary.total_documents ?? 0) },
    { label: "Parent chunks", value: formatNumber(summary.total_parent_chunks ?? 0) },
    { label: "Child chunks", value: formatNumber(summary.total_child_chunks ?? 0) },
    { label: "Tệp đã tải", value: formatBytes(summary.total_upload_bytes ?? 0), hint: "Không lưu gốc" },
    { label: "Text trong DB", value: formatBytes(dbBytes) },
    { label: "Index FAISS", value: formatBytes(faissBytes), hint: "mmap theo Tenant" },
    { label: "Vector FAISS", value: formatNumber(summary.total_faiss_vectors ?? 0) },
    { label: "Telemetry logs", value: formatNumber(summary.total_telemetry_logs ?? 0) },
  ];

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-1.5">
            <Database className="h-4 w-4 text-indigo-500" /> Dung lượng toàn nền tảng: PostgreSQL vs FAISS
          </CardTitle>
          <CardDescription>
            Tổng hợp siêu dữ liệu từ mọi Tenant. Nội dung raw text không được đọc hay hiển thị (SEC-026).
          </CardDescription>
        </div>
        {measuredAt ? <Badge variant="outline">Đo lúc {formatDateTime(measuredAt)}</Badge> : null}
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex h-3 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full bg-indigo-500 transition-all"
            style={{ width: `${dbPercent}%` }}
            title={`PostgreSQL text: ${formatBytes(dbBytes)}`}
          />
          <div
            className="h-full bg-emerald-500 transition-all"
            style={{ width: `${100 - dbPercent}%` }}
            title={`FAISS index: ${formatBytes(faissBytes)}`}
          />
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-indigo-500" /> PostgreSQL text: {formatBytes(dbBytes)}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-500" /> FAISS mmap: {formatBytes(faissBytes)}
          </span>
          <span>Tổng cộng: {formatBytes(dbBytes + faissBytes)}</span>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {tiles.map((tile) => (
            <div key={tile.label} className="rounded-lg border bg-muted/20 px-3 py-2.5">
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{tile.label}</p>
              <p className="mt-0.5 text-lg font-semibold tabular-nums leading-tight">{tile.value}</p>
              {tile.hint ? <p className="mt-0.5 text-[10px] text-muted-foreground">{tile.hint}</p> : null}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

/** Platform Super-Admin Console (Tier 1): giám sát toàn hệ thống, storage từng Tenant, model limits và audit logs. */
export function PlatformAdminPage() {
  const { me } = useAuth();
  const tenantKey = me?.tenant_id ?? "";
  const [eventType, setEventType] = useState("");
  const [limit, setLimit] = useState(50);
  const [tenantFilter, setTenantFilter] = useState("");

  const dashboardQuery = useQuery({
    queryKey: ["dashboard", "platform", tenantKey],
    queryFn: dashboardApi.platform,
  });
  const analyticsQuery = useQuery({
    queryKey: ["dashboard", "platform", "analytics", tenantKey],
    queryFn: dashboardApi.platformAnalytics,
  });
  const logsQuery = useQuery({
    queryKey: ["dashboard", "platform", "logs", tenantKey, tenantFilter, eventType, limit],
    queryFn: () => dashboardApi.platformLogs(tenantFilter || undefined, eventType || undefined, limit),
  });

  const live = useRealtimeSnapshot<PlatformAnalytics>(
    dashboardApi.platformRealtimePath(3),
    tenantKey,
    isPlatformAnalytics,
  );

  const dashboard = dashboardQuery.data;
  const analytics = live.snapshot ?? analyticsQuery.data;
  const breakdown = analytics?.tenants_breakdown ?? [];
  const tenantNames = useMemo(
    () => Object.fromEntries(breakdown.map((item) => [item.tenant_id, item.tenant_name])),
    [breakdown],
  );
  const summary = analytics?.storage_summary ?? {};
  const refreshing = dashboardQuery.isFetching || analyticsQuery.isFetching || logsQuery.isFetching;
  const healthy = (analytics?.system_health ?? dashboard?.system_health) === "OPERATIONAL";

  const refreshAll = (): void => {
    void dashboardQuery.refetch();
    void analyticsQuery.refetch();
    void logsQuery.refetch();
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tổng quan nền tảng"
        description="Góc nhìn Platform Super-Admin: hạn ngạch và lưu trữ từng doanh nghiệp, model/API key đã che, độ trễ P95, lỗi và 429 — tuyệt đối không đọc nội dung khách hàng (SEC-026)."
        actions={
          <>
            <Badge variant={healthy ? "success" : "warning"}>
              <Activity className="h-3 w-3" /> {healthy ? "Hệ thống OPERATIONAL" : "Cần kiểm tra"}
            </Badge>
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
        <Alert variant="destructive" title="Không tải được dữ liệu nền tảng">
          {describeApiError(
            dashboardQuery.error ?? analyticsQuery.error,
            "Vui lòng kiểm tra kết nối hoặc quyền Platform Super-Admin.",
          )}
        </Alert>
      ) : null}

      {live.status === "error" ? (
        <Alert variant="warning" title="Luồng realtime bị gián đoạn">
          Hệ thống sẽ tự thử kết nối lại sau vài giây. Các số liệu bên dưới vẫn là ảnh chụp hợp lệ gần nhất.
        </Alert>
      ) : null}

      <Sec026Notice>
        {analytics?.security_notice ??
          "Chỉ hiển thị siêu dữ liệu lưu trữ & hạn mức kỹ thuật; cấm truy cập văn bản thô của khách hàng."}
      </Sec026Notice>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {dashboardQuery.isLoading ? (
          Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-[104px] rounded-xl" />)
        ) : (
          <>
            <StatCard
              label="Tổng doanh nghiệp"
              value={formatNumber(dashboard?.total_tenants ?? 0)}
              hint={`${formatNumber(dashboard?.active_tenants ?? 0)} đang hoạt động`}
              icon={Building2}
              tone="indigo"
            />
            <StatCard
              label="Người dùng toàn hệ thống"
              value={formatNumber(dashboard?.total_users ?? 0)}
              hint="Tài khoản đã đăng ký"
              icon={Users}
              tone="violet"
            />
            <StatCard
              label="Tài liệu toàn nền tảng"
              value={formatNumber(dashboard?.total_documents ?? 0)}
              hint="Đã bóc tách, không lưu file gốc"
              icon={FileStack}
              tone="emerald"
            />
            <StatCard
              label="Dung lượng tệp đã tải"
              value={formatBytes(dashboard?.total_storage_bytes ?? 0)}
              hint="Tổng kích thước lúc nạp"
              icon={Server}
              tone="sky"
            />
          </>
        )}
      </div>

      {analytics ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <PlatformStorageCard summary={summary} measuredAt={analytics.measured_at_utc} />
          <RealtimeMetricsCard metrics={analytics.realtime_metrics} />
        </div>
      ) : analyticsQuery.isLoading ? (
        <div className="grid gap-4 xl:grid-cols-2">
          <Skeleton className="h-[420px] rounded-xl" />
          <Skeleton className="h-[420px] rounded-xl" />
        </div>
      ) : null}

      {analytics ? <ModelLimitsCards limits={analytics.model_api_limits} /> : null}

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-1.5">
              <ScrollText className="h-4 w-4 text-slate-500" /> Hạn ngạch & lưu trữ từng doanh nghiệp
            </CardTitle>
            <CardDescription>Số liệu đo trực tiếp từ DB, file FAISS trên đĩa và Token Bucket in-memory của từng Tenant.</CardDescription>
          </div>
          <Link to="/admin/tenants" className={buttonVariants({ variant: "outline", size: "sm" })}>
            <ShieldCheck /> Quản lý Tenants
          </Link>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          {analyticsQuery.isLoading ? (
            <div className="space-y-2 px-5 pb-5">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-12 rounded-lg" />
              ))}
            </div>
          ) : breakdown.length === 0 ? (
            <EmptyState
              icon={Building2}
              title="Chưa có dữ liệu Tenant"
              description="Tạo doanh nghiệp đầu tiên ở trang Quản lý Tenants để bắt đầu theo dõi lưu trữ."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-5">Doanh nghiệp</TableHead>
                  <TableHead className="text-right">Tài liệu</TableHead>
                  <TableHead className="text-right">Chunks P/C</TableHead>
                  <TableHead className="text-right">DB text</TableHead>
                  <TableHead className="text-right">FAISS</TableHead>
                  <TableHead className="text-right">Telemetry</TableHead>
                  <TableHead className="text-right">RPM dùng</TableHead>
                  <TableHead className="text-right">TPM dùng</TableHead>
                  <TableHead className="pr-5 text-right">Thao tác</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {breakdown.map((item) => (
                  <TableRow key={item.tenant_id}>
                    <TableCell className="pl-5">
                      <div className="min-w-0">
                        <p className="flex items-center gap-1.5 truncate text-xs font-medium">
                          {item.tenant_name}
                          <Badge variant={item.is_active ? "success" : "destructive"}>
                            {item.is_active ? "Hoạt động" : "Tạm khóa"}
                          </Badge>
                        </p>
                        <p className="truncate text-[10px] text-muted-foreground">
                          {item.tenant_slug} · {item.tier}
                        </p>
                      </div>
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums">
                      {formatNumber(item.storage.documents_count)}
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums">
                      {formatNumber(item.storage.parent_chunks_count)} / {formatNumber(item.storage.child_chunks_count)}
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums">
                      {formatBytes(item.storage.total_db_bytes)}
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums">
                      <p>{formatBytes(item.storage.faiss_index_bytes)}</p>
                      <p className="text-[10px] text-muted-foreground">
                        {formatNumber(item.storage.faiss_vectors_count)} vector
                      </p>
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums">
                      {formatNumber(item.total_logged_events)}
                    </TableCell>
                    <TableCell className={`text-right text-xs font-medium tabular-nums ${quotaTone(item.live_quota.rpm_used_percent)}`}>
                      {formatPercent(item.live_quota.rpm_used_percent)}
                    </TableCell>
                    <TableCell className={`text-right text-xs font-medium tabular-nums ${quotaTone(item.live_quota.tpm_used_percent)}`}>
                      {formatPercent(item.live_quota.tpm_used_percent)}
                    </TableCell>
                    <TableCell className="pr-5 text-right">
                      <Link to="/admin/tenants" className={buttonVariants({ variant: "ghost", size: "sm" })}>
                        Quản lý
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <TelemetryLogsCard
        title="Nhật ký vận hành toàn nền tảng"
        description="Lọc theo doanh nghiệp và loại sự kiện. Không chứa câu hỏi, văn bản tài liệu hay API key."
        data={logsQuery.data}
        isLoading={logsQuery.isLoading}
        isFetching={logsQuery.isFetching}
        error={logsQuery.error}
        eventType={eventType}
        onEventTypeChange={setEventType}
        limit={limit}
        onLimitChange={setLimit}
        onRefresh={() => void logsQuery.refetch()}
        tenantNames={tenantNames}
        extraFilter={
          <Select
            className="h-8 w-48 text-xs"
            aria-label="Lọc theo doanh nghiệp"
            value={tenantFilter}
            onChange={(event) => setTenantFilter(event.target.value)}
          >
            <option value="">Tất cả doanh nghiệp</option>
            {breakdown.map((item) => (
              <option key={item.tenant_id} value={item.tenant_id}>
                {item.tenant_name}
              </option>
            ))}
          </Select>
        }
      />

      <StorageArchitectureNote />
    </div>
  );
}
