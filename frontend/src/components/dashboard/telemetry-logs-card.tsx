import type { ComponentProps, ReactNode } from "react";
import { RefreshCw, ScrollText } from "lucide-react";
import type { TelemetryLogItem, TelemetryLogsResponse } from "@/lib/types";
import { formatBytes, formatDateTime, formatNumber, formatRelativeTime } from "@/lib/utils";
import { describeApiError } from "@/lib/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert } from "@/components/feedback/alert";
import { EmptyState } from "@/components/feedback/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { Sec026Notice } from "./analytics-widgets";

type BadgeVariant = NonNullable<ComponentProps<typeof Badge>["variant"]>;

export const EVENT_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "", label: "Tất cả sự kiện" },
  { value: "chat_query", label: "Chat (không stream)" },
  { value: "chat_stream", label: "Chat (SSE stream)" },
  { value: "document_upload", label: "Tải tài liệu" },
  { value: "document_delete", label: "Xóa tài liệu" },
  { value: "rate_limit_blocked", label: "Chặn hạn mức" },
  { value: "llm_upstream_error", label: "Lỗi LLM upstream" },
];

const EVENT_LABELS: Record<string, string> = Object.fromEntries(
  EVENT_TYPE_OPTIONS.filter((option) => option.value).map((option) => [option.value, option.label]),
);

function eventVariant(eventType: string, statusCode: number): BadgeVariant {
  if (statusCode === 429) return "warning";
  if (statusCode >= 400) return "destructive";
  if (eventType === "document_upload") return "success";
  if (eventType === "document_delete") return "warning";
  if (eventType === "chat_query" || eventType === "chat_stream") return "info";
  return "outline";
}

function statusVariant(statusCode: number): BadgeVariant {
  if (statusCode === 429) return "warning";
  if (statusCode >= 400) return "destructive";
  if (statusCode >= 200 && statusCode < 300) return "success";
  return "outline";
}

function detailsSummary(item: TelemetryLogItem): string | null {
  const details = item.details ?? {};
  const parts: string[] = [];
  const push = (key: string, format?: (value: number) => string): void => {
    const value = details[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      parts.push(`${key}: ${format ? format(value) : formatNumber(value)}`);
    } else if (typeof value === "string" && value.trim()) {
      parts.push(`${key}: ${value.slice(0, 60)}`);
    }
  };
  push("savings_percent", (value) => `${value.toFixed(1)}%`);
  push("retained_chunks");
  push("retrieved_count");
  push("vectors_removed");
  push("file_type");
  push("file_size_bytes", (value) => formatBytes(value));
  push("parent_chunks");
  push("child_chunks");
  push("retry_after", (value) => `${value}s`);
  push("error");
  return parts.length > 0 ? parts.join(" · ") : null;
}

function storageDelta(value: number): ReactNode {
  if (!value) return <span className="text-muted-foreground">—</span>;
  const sign = value > 0 ? "+" : "−";
  return (
    <span className={value > 0 ? "text-emerald-600" : "text-red-600"}>
      {sign}
      {formatBytes(Math.abs(value))}
    </span>
  );
}

interface TelemetryLogsCardProps {
  title: string;
  description: string;
  data: TelemetryLogsResponse | undefined;
  isLoading: boolean;
  isFetching: boolean;
  error: unknown;
  eventType: string;
  onEventTypeChange: (value: string) => void;
  limit: number;
  onLimitChange: (value: number) => void;
  onRefresh: () => void;
  tenantNames?: Record<string, string>;
  extraFilter?: ReactNode;
}

/** Bảng audit logs dùng chung cho Tenant Admin và Platform Super-Admin (dữ liệu đã sanitize). */
export function TelemetryLogsCard({
  title,
  description,
  data,
  isLoading,
  isFetching,
  error,
  eventType,
  onEventTypeChange,
  limit,
  onLimitChange,
  onRefresh,
  tenantNames,
  extraFilter,
}: TelemetryLogsCardProps) {
  const logs = data?.logs ?? [];

  return (
    <Card>
      <CardHeader className="gap-3 xl:flex-row xl:items-end xl:justify-between">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-1.5">
            <ScrollText className="h-4 w-4 text-slate-500" /> {title}
          </CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {extraFilter}
          <Select
            className="h-8 w-44 text-xs"
            aria-label="Lọc theo loại sự kiện"
            value={eventType}
            onChange={(event) => onEventTypeChange(event.target.value)}
          >
            {EVENT_TYPE_OPTIONS.map((option) => (
              <option key={option.value || "all"} value={option.value}>
                {option.label}
              </option>
            ))}
          </Select>
          <Select
            className="h-8 w-28 text-xs"
            aria-label="Số bản ghi"
            value={String(limit)}
            onChange={(event) => onLimitChange(Number(event.target.value))}
          >
            <option value="50">50 bản ghi</option>
            <option value="100">100 bản ghi</option>
            <option value="200">200 bản ghi</option>
          </Select>
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={isFetching}>
            <RefreshCw className={isFetching ? "animate-spin" : undefined} /> Làm mới
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 px-0 pb-0">
        <div className="px-5">
          <Sec026Notice>
            Nhật ký chỉ chứa siêu dữ liệu vận hành (loại sự kiện, mã HTTP, độ trễ, token, dung lượng). Nội dung câu hỏi,
            văn bản tài liệu và khóa API không bao giờ xuất hiện trong bảng này.
          </Sec026Notice>
        </div>

        {error ? (
          <div className="px-5">
            <Alert variant="destructive" title="Không tải được nhật ký vận hành">
              {describeApiError(error, "Vui lòng thử lại sau.")}
            </Alert>
          </div>
        ) : isLoading ? (
          <div className="space-y-2 px-5 pb-5">
            {Array.from({ length: 5 }).map((_, index) => (
              <Skeleton key={index} className="h-10 rounded-lg" />
            ))}
          </div>
        ) : logs.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="Chưa có bản ghi phù hợp"
            description="Thử đổi bộ lọc loại sự kiện hoặc thực hiện một thao tác tải tài liệu / chat để sinh telemetry."
          />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Thời điểm</TableHead>
                <TableHead>Sự kiện</TableHead>
                {tenantNames ? <TableHead>Doanh nghiệp</TableHead> : null}
                <TableHead>Model / Mode</TableHead>
                <TableHead>HTTP</TableHead>
                <TableHead className="text-right">Độ trễ</TableHead>
                <TableHead className="text-right">Tokens (in→nén / out)</TableHead>
                <TableHead className="text-right">Lưu trữ</TableHead>
                <TableHead className="pr-5">Chi tiết</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {logs.map((log) => {
                const summary = detailsSummary(log);
                return (
                  <TableRow key={log.id}>
                    <TableCell className="pl-5 whitespace-nowrap">
                      <p className="text-xs">{formatDateTime(log.timestamp_utc)}</p>
                      <p className="text-[10px] text-muted-foreground">{formatRelativeTime(log.timestamp_utc)}</p>
                    </TableCell>
                    <TableCell>
                      <Badge variant={eventVariant(log.event_type, log.status_code)}>
                        {EVENT_LABELS[log.event_type] ?? log.event_type}
                      </Badge>
                    </TableCell>
                    {tenantNames ? (
                      <TableCell className="max-w-[180px]">
                        {log.tenant_id ? (
                          <>
                            <p className="truncate text-xs">{tenantNames[log.tenant_id] ?? "Tenant"}</p>
                            <p className="truncate font-mono text-[10px] text-muted-foreground">
                              {log.tenant_id.slice(0, 8)}…
                            </p>
                          </>
                        ) : (
                          <span className="text-xs text-muted-foreground">Hệ thống</span>
                        )}
                      </TableCell>
                    ) : null}
                    <TableCell className="max-w-[170px]">
                      <p className="truncate font-mono text-[11px]">{log.model_name ?? "—"}</p>
                      <p className="text-[10px] text-muted-foreground">{log.mode ?? "—"}</p>
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(log.status_code)}>{log.status_code}</Badge>
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums">{formatNumber(log.latency_ms)} ms</TableCell>
                    <TableCell className="text-right text-xs tabular-nums">
                      {formatNumber(log.input_tokens)}→{formatNumber(log.compressed_tokens)} / {formatNumber(log.output_tokens)}
                    </TableCell>
                    <TableCell className="text-right text-xs tabular-nums">{storageDelta(log.storage_delta_bytes)}</TableCell>
                    <TableCell className="max-w-[240px] pr-5">
                      <p className="truncate text-[11px] text-muted-foreground" title={summary ?? undefined}>
                        {summary ?? "—"}
                      </p>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
        <p className="px-5 pb-4 text-right text-[10px] text-muted-foreground">
          Hiển thị {formatNumber(logs.length)} bản ghi mới nhất
          {data ? ` / tổng ${formatNumber(data.total_returned)}` : ""}.
        </p>
      </CardContent>
    </Card>
  );
}
