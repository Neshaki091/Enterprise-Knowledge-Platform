import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  Building2,
  CircleCheck,
  Gauge,
  Plus,
  RefreshCw,
  Search,
  Settings2,
} from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/features/auth/auth-context";
import { tenantApi } from "@/lib/api";
import { describeApiError } from "@/lib/api-client";
import { formatDateTime, formatNumber, slugify } from "@/lib/utils";
import type { TenantResponse } from "@/lib/types";
import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Alert } from "@/components/feedback/alert";
import { EmptyState } from "@/components/feedback/empty-state";
import { Spinner } from "@/components/feedback/spinner";

function quotaNumber(tenant: TenantResponse, key: string, fallback: number): number {
  const value = tenant.quota_limits[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** Trang Platform Super-Admin: quản lý vòng đời và hạn ngạch của mọi Tenant. */
export function TenantsPage() {
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [tenantName, setTenantName] = useState("");
  const [tenantSlug, setTenantSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);

  const [editing, setEditing] = useState<TenantResponse | null>(null);
  const [rpmLimit, setRpmLimit] = useState("30");
  const [tpmLimit, setTpmLimit] = useState("16000");
  const [maxUsers, setMaxUsers] = useState("10");
  const [maxDocuments, setMaxDocuments] = useState("50");

  const [confirming, setConfirming] = useState<TenantResponse | null>(null);

  const tenantsQuery = useQuery({ queryKey: ["tenants"], queryFn: () => tenantApi.list(0, 100) });

  const createTenant = useMutation({
    mutationFn: tenantApi.create,
    onSuccess: async (created) => {
      toast.success(`Đã tạo Tenant ${created.name}`);
      setCreateOpen(false);
      setTenantName("");
      setTenantSlug("");
      setSlugTouched(false);
      await queryClient.invalidateQueries({ queryKey: ["tenants"] });
    },
    onError: (error) => toast.error(describeApiError(error, "Không thể tạo Tenant")),
  });

  const updateQuota = useMutation({
    mutationFn: ({ tenantId, quota }: { tenantId: string; quota: Record<string, unknown> }) =>
      tenantApi.update(tenantId, { quota_limits: quota }),
    onSuccess: async (updated) => {
      toast.success(`Đã cập nhật hạn ngạch ${updated.name}`);
      setEditing(null);
      await queryClient.invalidateQueries({ queryKey: ["tenants"] });
    },
    onError: (error) => toast.error(describeApiError(error, "Không thể cập nhật hạn ngạch")),
  });

  const toggleStatus = useMutation({
    mutationFn: (tenant: TenantResponse) =>
      tenant.is_active ? tenantApi.suspend(tenant.id) : tenantApi.activate(tenant.id),
    onSuccess: async (updated) => {
      toast.success(updated.is_active ? `Đã kích hoạt ${updated.name}` : `Đã tạm khóa ${updated.name}`);
      setConfirming(null);
      await queryClient.invalidateQueries({ queryKey: ["tenants"] });
    },
    onError: (error) => toast.error(describeApiError(error, "Không thể đổi trạng thái Tenant")),
  });

  const tenants = tenantsQuery.data ?? [];
  const filtered = useMemo(() => {
    const keyword = filter.trim().toLowerCase();
    if (!keyword) return tenants;
    return tenants.filter(
      (tenant) =>
        tenant.name.toLowerCase().includes(keyword) || tenant.slug.toLowerCase().includes(keyword),
    );
  }, [tenants, filter]);

  const handleNameChange = (value: string): void => {
    setTenantName(value);
    if (!slugTouched) setTenantSlug(slugify(value));
  };

  const openCreate = (): void => {
    setTenantName("");
    setTenantSlug("");
    setSlugTouched(false);
    setCreateOpen(true);
  };

  const openQuotaEditor = (tenant: TenantResponse): void => {
    setEditing(tenant);
    setRpmLimit(String(quotaNumber(tenant, "rpm_limit", 30)));
    setTpmLimit(String(quotaNumber(tenant, "tpm_limit", 16000)));
    setMaxUsers(String(quotaNumber(tenant, "max_users", 10)));
    setMaxDocuments(String(quotaNumber(tenant, "max_documents", 50)));
  };

  const handleCreate = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    createTenant.mutate({ name: tenantName.trim(), slug: tenantSlug.trim() });
  };

  const handleSaveQuota = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!editing) return;
    const quota: Record<string, unknown> = {
      ...editing.quota_limits,
      rpm_limit: Math.max(1, Number(rpmLimit) || 30),
      tpm_limit: Math.max(100, Number(tpmLimit) || 16000),
      max_users: Math.max(0, Number(maxUsers) || 0),
      max_documents: Math.max(0, Number(maxDocuments) || 0),
    };
    updateQuota.mutate({ tenantId: editing.id, quota });
  };

  const suspendSelf = Boolean(confirming && confirming.id === me?.tenant_id && confirming.is_active);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Quản lý Tenants"
        description="Vòng đời doanh nghiệp trên nền tảng: tạo mới, tạm khóa, kích hoạt và cấu hình hạn ngạch RPM/TPM/người dùng/tài liệu."
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => void tenantsQuery.refetch()}
              disabled={tenantsQuery.isFetching}
            >
              <RefreshCw className={tenantsQuery.isFetching ? "animate-spin" : undefined} /> Làm mới
            </Button>
            <Button onClick={openCreate}>
              <Plus /> Tạo Tenant
            </Button>
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="rounded-xl border bg-card p-5 shadow-card">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Tổng Tenant</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{formatNumber(tenants.length)}</p>
        </div>
        <div className="rounded-xl border bg-card p-5 shadow-card">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Đang hoạt động</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-emerald-600">
            {formatNumber(tenants.filter((tenant) => tenant.is_active).length)}
          </p>
        </div>
        <div className="rounded-xl border bg-card p-5 shadow-card">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Đang tạm khóa</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums text-red-600">
            {formatNumber(tenants.filter((tenant) => !tenant.is_active).length)}
          </p>
        </div>
      </div>

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>Danh sách doanh nghiệp</CardTitle>
          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="h-8 pl-8 text-xs"
              placeholder="Tìm theo tên hoặc slug…"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          {tenantsQuery.isLoading ? (
            <div className="space-y-2 px-5 pb-5">
              {Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-12 rounded-lg" />)}
            </div>
          ) : tenantsQuery.isError ? (
            <div className="px-5 pb-5">
              <Alert variant="destructive" title="Không tải được danh sách Tenant">
                {describeApiError(tenantsQuery.error, "Vui lòng thử lại sau.")}
              </Alert>
            </div>
          ) : filtered.length === 0 ? (
            <EmptyState
              icon={Building2}
              title={tenants.length === 0 ? "Chưa có Tenant nào" : "Không khớp bộ lọc"}
              description={tenants.length === 0 ? "Tạo doanh nghiệp đầu tiên trên nền tảng." : "Thử từ khóa khác."}
              action={tenants.length === 0 ? <Button size="sm" onClick={openCreate}><Plus /> Tạo Tenant</Button> : undefined}
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-5">Doanh nghiệp</TableHead>
                  <TableHead>Trạng thái</TableHead>
                  <TableHead>Hạn ngạch</TableHead>
                  <TableHead>Thời điểm tạo</TableHead>
                  <TableHead className="pr-5 text-right">Thao tác</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.map((tenant) => {
                  const isCurrent = tenant.id === me?.tenant_id;
                  return (
                    <TableRow key={tenant.id}>
                      <TableCell className="pl-5">
                        <div className="flex items-center gap-2.5">
                          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-600">
                            <Building2 className="h-4 w-4" />
                          </div>
                          <div className="min-w-0">
                            <p className="flex items-center gap-1.5 truncate text-xs font-medium">
                              {tenant.name}
                              {isCurrent ? <Badge variant="info">Tenant hiện tại</Badge> : null}
                            </p>
                            <p className="truncate text-[10px] text-muted-foreground">{tenant.slug}</p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={tenant.is_active ? "success" : "destructive"}>
                          {tenant.is_active ? "Hoạt động" : "Tạm khóa"}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1">
                          <Badge variant="outline">RPM {quotaNumber(tenant, "rpm_limit", 30)}</Badge>
                          <Badge variant="outline">TPM {formatNumber(quotaNumber(tenant, "tpm_limit", 16000))}</Badge>
                          <Badge variant="outline">
                            {quotaNumber(tenant, "max_users", 0) === 0
                              ? "Users ∞"
                              : `${quotaNumber(tenant, "max_users", 10)} users`}
                          </Badge>
                          <Badge variant="outline">
                            {quotaNumber(tenant, "max_documents", 0) === 0
                              ? "Docs ∞"
                              : `${quotaNumber(tenant, "max_documents", 50)} docs`}
                          </Badge>
                        </div>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{formatDateTime(tenant.created_at)}</TableCell>
                      <TableCell className="pr-5">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            title="Chỉnh hạn ngạch"
                            onClick={() => openQuotaEditor(tenant)}
                          >
                            <Settings2 className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className={tenant.is_active ? "h-7 w-7 text-red-600 hover:bg-red-50" : "h-7 w-7 text-emerald-600 hover:bg-emerald-50"}
                            title={tenant.is_active ? "Tạm khóa" : "Kích hoạt"}
                            disabled={isCurrent}
                            onClick={() => setConfirming(tenant)}
                          >
                            {tenant.is_active ? <Ban className="h-3.5 w-3.5" /> : <CircleCheck className="h-3.5 w-3.5" />}
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        title="Tạo doanh nghiệp (Tenant)"
        description="Tenant mới chưa có người dùng; hãy thêm nhân sự qua API membership hoặc yêu cầu chủ doanh nghiệp đăng ký."
        footer={
          <>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>Hủy</Button>
            <Button type="submit" form="create-tenant-form" disabled={createTenant.isPending}>
              {createTenant.isPending ? <Spinner /> : <Plus />} Tạo Tenant
            </Button>
          </>
        }
      >
        <form id="create-tenant-form" onSubmit={handleCreate} className="space-y-4">
          {createTenant.isError ? (
            <Alert variant="destructive">{describeApiError(createTenant.error, "Không thể tạo Tenant")}</Alert>
          ) : null}
          <div className="space-y-1.5">
            <Label htmlFor="new-tenant-name">Tên doanh nghiệp</Label>
            <Input
              id="new-tenant-name"
              required
              minLength={2}
              maxLength={255}
              placeholder="Công ty Cổ phần Sao Mai"
              value={tenantName}
              onChange={(event) => handleNameChange(event.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="new-tenant-slug">Slug định danh</Label>
            <Input
              id="new-tenant-slug"
              required
              minLength={2}
              maxLength={100}
              pattern="^[a-z0-9\-]+$"
              placeholder="sao-mai"
              value={tenantSlug}
              onChange={(event) => {
                setSlugTouched(true);
                setTenantSlug(event.target.value.toLowerCase());
              }}
            />
          </div>
          <Alert variant="info">
            Hạn ngạch mặc định: 30 RPM, 16.000 TPM, tối đa 10 người dùng và 50 tài liệu. Có thể chỉnh ngay sau khi tạo.
          </Alert>
        </form>
      </Dialog>

      <Dialog
        open={editing !== null}
        onOpenChange={(open) => { if (!open) setEditing(null); }}
        title={editing ? `Hạn ngạch — ${editing.name}` : "Hạn ngạch Tenant"}
        description="Áp dụng tức thời cho Token Bucket in-memory của Tenant. Giá trị 0 ở người dùng/tài liệu nghĩa là không giới hạn."
        footer={
          <>
            <Button variant="outline" onClick={() => setEditing(null)}>Hủy</Button>
            <Button type="submit" form="quota-form" disabled={updateQuota.isPending}>
              {updateQuota.isPending ? <Spinner /> : <Gauge />} Lưu hạn ngạch
            </Button>
          </>
        }
      >
        <form id="quota-form" onSubmit={handleSaveQuota} className="space-y-4">
          {updateQuota.isError ? (
            <Alert variant="destructive">{describeApiError(updateQuota.error, "Không thể cập nhật hạn ngạch")}</Alert>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="rpm-limit">Requests / phút (RPM)</Label>
              <Input
                id="rpm-limit"
                type="number"
                min={1}
                max={60000}
                required
                value={rpmLimit}
                onChange={(event) => setRpmLimit(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="tpm-limit">Tokens / phút (TPM)</Label>
              <Input
                id="tpm-limit"
                type="number"
                min={100}
                max={100000000}
                required
                value={tpmLimit}
                onChange={(event) => setTpmLimit(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="max-users">Người dùng tối đa</Label>
              <Input
                id="max-users"
                type="number"
                min={0}
                required
                value={maxUsers}
                onChange={(event) => setMaxUsers(event.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="max-documents">Tài liệu tối đa</Label>
              <Input
                id="max-documents"
                type="number"
                min={0}
                required
                value={maxDocuments}
                onChange={(event) => setMaxDocuments(event.target.value)}
              />
            </div>
          </div>
          <Alert variant="warning">
            Lưu ý: thay đổi hạn ngạch ảnh hưởng ngay đến mọi nhân viên của doanh nghiệp này. RPM/TPM thấp hơn
            nhu cầu thực tế sẽ khiến chat trả về HTTP 429 kèm thời gian chờ.
          </Alert>
        </form>
      </Dialog>

      <Dialog
        open={confirming !== null}
        onOpenChange={(open) => { if (!open) setConfirming(null); }}
        title={confirming?.is_active ? "Tạm khóa doanh nghiệp?" : "Kích hoạt lại doanh nghiệp?"}
        size="sm"
        footer={
          <>
            <Button variant="outline" onClick={() => setConfirming(null)}>Hủy</Button>
            <Button
              variant={confirming?.is_active ? "destructive" : "default"}
              disabled={toggleStatus.isPending || suspendSelf}
              onClick={() => { if (confirming) toggleStatus.mutate(confirming); }}
            >
              {toggleStatus.isPending ? <Spinner /> : confirming?.is_active ? <Ban /> : <CircleCheck />}
              {confirming?.is_active ? "Tạm khóa" : "Kích hoạt"}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm">
          {suspendSelf ? (
            <Alert variant="warning" title="Không thể tự khóa Tenant đang làm việc">
              Hãy chuyển sang một workspace khác trước khi tạm khóa doanh nghiệp này.
            </Alert>
          ) : null}
          <p className="text-muted-foreground">
            {confirming?.is_active
              ? "Mọi phiên truy cập của nhân viên thuộc doanh nghiệp này sẽ bị từ chối ngay lập tức cho tới khi được kích hoạt lại."
              : "Doanh nghiệp sẽ hoạt động trở lại và nhân viên có thể đăng nhập bình thường."}
          </p>
          <div className="rounded-lg border bg-muted/30 px-3 py-2 text-xs">
            <p className="font-medium">{confirming?.name}</p>
            <p className="text-muted-foreground">{confirming?.slug}</p>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
