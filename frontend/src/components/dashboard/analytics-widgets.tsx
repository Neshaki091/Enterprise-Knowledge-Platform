import type { ReactNode } from "react";
import {
  Activity,
  Boxes,
  Clock3,
  Database,
  Gauge,
  HardDrive,
  KeyRound,
  Layers3,
  ServerCog,
  Sparkles,
  Timer,
  TriangleAlert,
} from "lucide-react";
import type { LiveQuotaStatus, TenantStorageBreakdown } from "@/lib/types";
import { formatBytes, formatDateTime, formatNumber, formatPercent, formatRelativeTime } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";

/** Thu hẹp giá trị JSON không xác định thành object để đọc an toàn các trường lồng nhau. */
export function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Đọc số từ payload JSON; trả null nếu thiếu hoặc không hữu hạn. */
export function toNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Đọc chuỗi từ payload JSON. */
export function toText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** Đọc boolean từ payload JSON. */
export function toBool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function MetricTile({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border bg-muted/20 px-3 py-2.5">
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums leading-tight">{value}</p>
      {hint ? <p className="mt-0.5 text-[10px] text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

/** Hai thanh RPM/TPM còn lại trong cửa sổ 60 giây kèm bộ đếm chặn hạn mức. */
export function QuotaMeters({ quota }: { quota: LiveQuotaStatus }) {
  const rpmUsed = Math.max(0, Math.min(100, quota.rpm_used_percent));
  const tpmUsed = Math.max(0, Math.min(100, quota.tpm_used_percent));
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-1.5">
            <Gauge className="h-4 w-4 text-indigo-500" /> Hạn ngạch Token Bucket
          </CardTitle>
          <CardDescription>Cửa sổ trượt 60 giây chạy in-memory trong tiến trình FastAPI, không Redis.</CardDescription>
        </div>
        <Badge variant={rpmUsed >= 90 || tpmUsed >= 90 ? "destructive" : "info"}>
          <Activity className="h-3 w-3" /> Realtime
        </Badge>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="font-medium">RPM còn lại</span>
              <span className="tabular-nums text-muted-foreground">
                {formatNumber(Math.ceil(quota.rpm_remaining))} / {formatNumber(quota.rpm_capacity)}
              </span>
            </div>
            <Progress value={rpmUsed} className="mt-2" label="Hạn ngạch RPM" />
            <p className="mt-1 text-[11px] text-muted-foreground">Đã dùng {formatPercent(rpmUsed)}</p>
          </div>
          <div>
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="font-medium">TPM còn lại</span>
              <span className="tabular-nums text-muted-foreground">
                {formatNumber(Math.ceil(quota.tpm_remaining))} / {formatNumber(quota.tpm_capacity)}
              </span>
            </div>
            <Progress value={tpmUsed} className="mt-2" label="Hạn ngạch TPM" />
            <p className="mt-1 text-[11px] text-muted-foreground">Đã dùng {formatPercent(tpmUsed)}</p>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <MetricTile label="Tổng request" value={formatNumber(quota.total_requests)} />
          <MetricTile label="Chặn RPM" value={formatNumber(quota.total_blocked_rpm)} hint="HTTP 429" />
          <MetricTile label="Chặn TPM" value={formatNumber(quota.total_blocked_tpm)} hint="Vượt token/phút" />
          <MetricTile label="Token tiêu thụ" value={formatNumber(quota.total_tokens_consumed)} hint="Prompt + output" />
        </div>
        <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Clock3 className="h-3 w-3" /> Request gần nhất: {formatRelativeTime(quota.last_request_at)}
        </p>
      </CardContent>
    </Card>
  );
}

/** Lưới chỉ số hiệu năng ring-buffer: lưu lượng, độ trễ TB/P95, nén ngữ cảnh, lỗi và 429. */
export function RealtimeMetricsCard({ metrics }: { metrics: Record<string, unknown> }) {
  const value = (key: string): number | null => toNumber(metrics[key]);
  const measuredAt = toText(metrics["measured_at_utc"]);
  const errorRate = value("error_rate_percent");

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-1.5">
            <Timer className="h-4 w-4 text-sky-500" /> Hiệu năng thời gian thực
          </CardTitle>
          <CardDescription>Đo từ ring buffer 250 sự kiện gần nhất; P95 phản ánh đuôi độ trễ.</CardDescription>
        </div>
        {measuredAt ? (
          <Badge variant="outline">Đo lúc {formatDateTime(measuredAt)}</Badge>
        ) : (
          <Badge variant="outline">Chưa có dữ liệu</Badge>
        )}
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-4">
          <MetricTile label="Request / 1 phút" value={formatNumber(value("requests_last_1m"))} />
          <MetricTile label="Request / 5 phút" value={formatNumber(value("requests_last_5m"))} />
          <MetricTile label="Truy vấn chat / 5 phút" value={formatNumber(value("queries_last_5m"))} />
          <MetricTile
            label="Độ trễ TB"
            value={`${formatNumber(value("avg_latency_ms"))} ms`}
          />
          <MetricTile
            label="P95 độ trễ"
            value={`${formatNumber(value("p95_latency_ms"))} ms`}
          />
          <MetricTile
            label="Nén ngữ cảnh TB"
            value={formatPercent(value("avg_compression_savings_percent"))}
            hint="Mục tiêu ≥ 70%"
          />
          <MetricTile
            label="Lỗi / 5 phút"
            value={formatNumber(value("errors_last_5m"))}
            hint={errorRate !== null ? `Tỷ lệ ${formatPercent(errorRate)}` : undefined}
          />
          <MetricTile
            label="Bị chặn 429 / 5 phút"
            value={formatNumber(value("rate_limited_429_last_5m"))}
          />
        </div>
      </CardContent>
    </Card>
  );
}

/** Thanh tỷ trọng PostgreSQL vs FAISS mmap kèm bảng phân rã dung lượng từng phần. */
export function StorageSplitCard({ storage }: { storage: TenantStorageBreakdown }) {
  const dbBytes = Math.max(0, storage.total_db_bytes);
  const faissBytes = storage.faiss_index_exists ? Math.max(0, storage.faiss_index_bytes) : 0;
  const total = Math.max(1, dbBytes + faissBytes);
  const dbPercent = (dbBytes / total) * 100;
  const faissPercent = 100 - dbPercent;

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle className="flex items-center gap-1.5">
          <HardDrive className="h-4 w-4 text-emerald-500" /> Lưu trữ thật: PostgreSQL vs FAISS
        </CardTitle>
        <CardDescription>
          File gốc không được giữ lại; cột &quot;Tệp đã tải&quot; chỉ là tổng dung lượng lúc nạp (Zero Raw Storage).
        </CardDescription>
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
            style={{ width: `${faissPercent}%` }}
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
          <MetricTile label="Tài liệu" value={formatNumber(storage.documents_count)} />
          <MetricTile label="Parent chunks" value={formatNumber(storage.parent_chunks_count)} />
          <MetricTile label="Child chunks" value={formatNumber(storage.child_chunks_count)} />
          <MetricTile label="Tệp đã tải" value={formatBytes(storage.uploaded_files_bytes)} hint="Không lưu gốc" />
          <MetricTile label="Raw text trong DB" value={formatBytes(storage.db_raw_text_bytes)} />
          <MetricTile label="Text chunks trong DB" value={formatBytes(storage.db_chunks_text_bytes)} />
          <MetricTile label="Index FAISS" value={formatBytes(storage.faiss_index_bytes)} hint="IO_FLAG_MMAP" />
          <MetricTile label="Vector FAISS" value={formatNumber(storage.faiss_vectors_count)} />
          <MetricTile label="Chiều vector" value={formatNumber(storage.faiss_dimension)} hint={storage.faiss_index_exists ? "Index sẵn sàng" : "Chưa tạo index"} />
        </div>
      </CardContent>
    </Card>
  );
}

/** Hai thẻ cấu hình LLM/Embedding: hiển thị model, endpoint và API key đã che (SEC-026). */
export function ModelLimitsCards({ limits }: { limits: Record<string, unknown> }) {
  const llm = asRecord(limits.llm);
  const embedding = asRecord(limits.embedding);
  const llmRates = asRecord(llm.rate_limits);
  const embeddingLimits = asRecord(embedding.limits);

  const llmKey = toText(llm.api_key_masked) ?? "Chưa cấu hình";
  const embedKey = toText(embedding.api_key_masked) ?? "Chưa cấu hình";

  const upstreamStats = (record: Record<string, unknown>): string => {
    const entries = Object.entries(record).filter(([, item]) => typeof item === "number" || typeof item === "string");
    if (entries.length === 0) return "Chưa ghi nhận";
    return entries
      .slice(0, 6)
      .map(([key, item]) => `${key}: ${String(item)}`)
      .join(" · ");
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader className="space-y-1">
          <CardTitle className="flex items-center gap-1.5">
            <Sparkles className="h-4 w-4 text-violet-500" /> Model sinh câu trả lời (LLM)
          </CardTitle>
          <CardDescription>Khóa API chỉ hiển thị dạng che; không bao giờ trả về secret đầy đủ.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="violet">{toText(llm.provider) ?? "—"}</Badge>
            <Badge variant="outline">{toText(llm.model) ?? "—"}</Badge>
            {toBool(llm.endpoint_configured) ? (
              <Badge variant="success">Endpoint sẵn sàng</Badge>
            ) : (
              <Badge variant="warning">Chưa có endpoint</Badge>
            )}
            {toBool(llm.api_key_configured) ? (
              <Badge variant="success">Đã có khóa</Badge>
            ) : (
              <Badge variant="warning">Thiếu khóa</Badge>
            )}
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-xs">
            <p className="flex items-center gap-1.5 text-muted-foreground">
              <KeyRound className="h-3 w-3" /> API key: <span className="font-mono text-foreground">{llmKey}</span>
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            <MetricTile label="RPM mặc định" value={formatNumber(toNumber(llmRates.rpm_limit))} />
            <MetricTile label="TPM mặc định" value={formatNumber(toNumber(llmRates.tpm_limit))} />
            <MetricTile label="Trần ngữ cảnh" value={formatNumber(toNumber(llmRates.max_context_tokens_default))} hint="tokens / câu hỏi" />
          </div>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            Telemetry upstream: {upstreamStats(asRecord(llm.upstream_telemetry))}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="space-y-1">
          <CardTitle className="flex items-center gap-1.5">
            <Boxes className="h-4 w-4 text-sky-500" /> Model nhúng vector (Embedding)
          </CardTitle>
          <CardDescription>Vector 786 chiều gắn trực tiếp vào FAISS IndexIDMap2 theo Tenant.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="info">{toText(embedding.provider) ?? "—"}</Badge>
            <Badge variant="outline">{toText(embedding.model) ?? "—"}</Badge>
            {toBool(embedding.endpoint_configured) ? (
              <Badge variant="success">Endpoint sẵn sàng</Badge>
            ) : (
              <Badge variant="warning">Chưa có endpoint</Badge>
            )}
            {toBool(embedding.api_key_configured) ? (
              <Badge variant="success">Đã có khóa</Badge>
            ) : (
              <Badge variant="warning">Thiếu khóa</Badge>
            )}
          </div>
          <div className="rounded-lg border bg-muted/20 px-3 py-2 text-xs">
            <p className="flex items-center gap-1.5 text-muted-foreground">
              <KeyRound className="h-3 w-3" /> API key: <span className="font-mono text-foreground">{embedKey}</span>
            </p>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <MetricTile label="Số chiều" value={formatNumber(toNumber(embeddingLimits.dimension))} />
            <MetricTile label="Batch tối đa" value={formatNumber(toNumber(embeddingLimits.batch_size_max))} hint="văn bản / request" />
          </div>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            Telemetry upstream: {upstreamStats(asRecord(embedding.upstream_telemetry))}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

/** Thẻ nhỏ giải thích kiến trúc lưu trữ dùng ở chân trang quản trị. */
export function StorageArchitectureNote() {
  return (
    <div className="grid gap-4 text-xs text-muted-foreground md:grid-cols-3">
      <div className="flex gap-2.5 rounded-lg border bg-card p-4">
        <Database className="mt-0.5 h-4 w-4 shrink-0 text-indigo-500" />
        <div className="space-y-0.5">
          <p className="font-semibold text-foreground">PostgreSQL 16 + RLS</p>
          <p className="leading-relaxed">Raw text và chunks lưu trong một CSDL duy nhất, cô lập bằng Row-Level Security.</p>
        </div>
      </div>
      <div className="flex gap-2.5 rounded-lg border bg-card p-4">
        <Layers3 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
        <div className="space-y-0.5">
          <p className="font-semibold text-foreground">FAISS mmap theo Tenant</p>
          <p className="leading-relaxed">Một index 786 chiều / doanh nghiệp, mở bằng IO_FLAG_MMAP để tiết kiệm RAM.</p>
        </div>
      </div>
      <div className="flex gap-2.5 rounded-lg border bg-card p-4">
        <ServerCog className="mt-0.5 h-4 w-4 shrink-0 text-sky-500" />
        <div className="space-y-0.5">
          <p className="font-semibold text-foreground">1 worker · 1.0 GB RAM</p>
          <p className="leading-relaxed">Token bucket, ring buffer và SSE chạy in-process; không Redis/Celery/MinIO.</p>
        </div>
      </div>
    </div>
  );
}

/** Cảnh báo SEC-026 dùng chung cho 2 bảng audit logs. */
export function Sec026Notice({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs text-amber-900">
      <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="space-y-0.5 leading-relaxed">
        <p className="font-semibold">Nguyên tắc SEC-026</p>
        <p>{children}</p>
      </div>
    </div>
  );
}
