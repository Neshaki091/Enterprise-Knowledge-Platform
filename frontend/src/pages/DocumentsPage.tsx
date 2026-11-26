import {
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Eye,
  FileStack,
  FileText,
  FolderTree,
  HardDrive,
  Layers,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  UploadCloud,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/features/auth/auth-context";
import { departmentApi, documentApi } from "@/lib/api";
import { ApiError, describeApiError } from "@/lib/api-client";
import { documentStatusLabel } from "@/lib/labels";
import { cn, formatBytes, formatDateTime, formatNumber, shortId } from "@/lib/utils";
import type { DocumentDetailResponse, DocumentResponse } from "@/lib/types";
import { PageHeader } from "@/components/layout/page-header";
import { StatCard } from "@/components/layout/stat-card";
import { Alert } from "@/components/feedback/alert";
import { EmptyState } from "@/components/feedback/empty-state";
import { Spinner } from "@/components/feedback/spinner";
import { DocumentDetailDialog } from "@/components/knowledge/document-detail-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

/** Giới hạn mặc định phía server (MAX_UPLOAD_SIZE_BYTES); chặn sớm để tiết kiệm băng thông. */
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv", ".json", ".log", ".rst"];

const STATUS_VARIANTS: Record<string, "success" | "warning" | "destructive"> = {
  PROCESSED: "success",
  PROCESSING: "warning",
  FAILED: "destructive",
};

interface UploadForm {
  file: File | null;
  title: string;
  departmentId: string;
}

/** Gợi ý nguyên nhân lỗi upload theo mã HTTP của backend (400/403/409/413/429). */
function uploadErrorHint(error: unknown): { title: string; description: string } {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return {
        title: "Tài liệu đã tồn tại",
        description: `${error.message} Hệ thống chống trùng lặp bằng SHA-256 trong cùng một doanh nghiệp.`,
      };
    }
    if (error.status === 413) {
      return {
        title: "Tệp vượt quá dung lượng cho phép",
        description: `${error.message} Hãy chia nhỏ tài liệu hoặc nén bớt trước khi tải lên.`,
      };
    }
    if (error.status === 400) {
      return {
        title: "Không bóc tách được nội dung",
        description: `${error.message} Tệp có thể rỗng, bị hỏng hoặc là PDF scan không có lớp văn bản.`,
      };
    }
    if (error.status === 403) {
      return { title: "Không có quyền tải lên phòng ban này", description: error.message };
    }
    if (error.status === 429) {
      return { title: "Tạm thời vượt hạn mức", description: error.message };
    }
  }
  return {
    title: "Tải tài liệu thất bại",
    description: describeApiError(error, "Vui lòng kiểm tra kết nối và thử lại."),
  };
}

/** Trang Knowledge Hub: tải lên Zero Raw Storage, danh sách tài liệu, xem Parent-Child và xóa an toàn. */
export function DocumentsPage() {
  const { me, isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [keyword, setKeyword] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");

  const [detailDoc, setDetailDoc] = useState<DocumentResponse | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DocumentResponse | null>(null);

  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadForm, setUploadForm] = useState<UploadForm>({ file: null, title: "", departmentId: "" });
  const [dragActive, setDragActive] = useState(false);
  const [localFileError, setLocalFileError] = useState("");

  const departmentsQuery = useQuery({ queryKey: ["departments"], queryFn: departmentApi.list });
  const documentsQuery = useQuery({
    queryKey: ["documents", departmentFilter],
    queryFn: () => documentApi.list({ department_id: departmentFilter || undefined, limit: 100 }),
  });

  const departments = departmentsQuery.data ?? [];
  const documents = documentsQuery.data ?? [];
  const isEmployee = !isAdmin;

  const departmentNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const department of departments) map.set(department.id, department.name);
    return map;
  }, [departments]);

  /** Nhân viên chỉ thấy phòng ban của mình trong bộ lọc; phạm vi đọc vẫn do backend quyết định. */
  const selectableDepartments = useMemo(() => {
    if (isEmployee) return departments.filter((department) => department.id === me?.department_id);
    return departments;
  }, [departments, isEmployee, me?.department_id]);

  const filteredDocuments = useMemo(() => {
    const needle = keyword.trim().toLowerCase();
    if (!needle) return documents;
    return documents.filter(
      (document) =>
        document.title.toLowerCase().includes(needle) ||
        document.file_name.toLowerCase().includes(needle) ||
        document.file_type.toLowerCase().includes(needle),
    );
  }, [documents, keyword]);

  const stats = useMemo(() => {
    let parentChunks = 0;
    let childChunks = 0;
    let storage = 0;
    for (const document of documents) {
      parentChunks += document.parent_chunk_count;
      childChunks += document.child_chunk_count;
      storage += document.file_size_bytes;
    }
    return { documents: documents.length, parentChunks, childChunks, storage };
  }, [documents]);

  const uploadMutation = useMutation({
    mutationFn: (payload: UploadForm) =>
      documentApi.upload(payload.file as File, payload.title, payload.departmentId || null),
    onSuccess: async (created) => {
      toast.success(`Đã nạp "${created.title}" vào kho tri thức`, {
        description: `${formatNumber(created.parent_chunk_count)} parent · ${formatNumber(created.child_chunk_count)} child chunks`,
      });
      setUploadOpen(false);
      setUploadForm({ file: null, title: "", departmentId: "" });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["documents"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
    },
    onError: (error) => {
      toast.error(describeApiError(error, "Không thể tải tài liệu lên"));
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (documentId: string) => documentApi.delete(documentId),
    onSuccess: async (_result, documentId) => {
      toast.success("Đã xóa tài liệu và toàn bộ vector FAISS liên quan");
      setPendingDelete(null);
      if (detailDoc?.id === documentId) setDetailDoc(null);
      queryClient.removeQueries({ queryKey: ["document", documentId] });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["documents"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
    },
    onError: (error) => {
      toast.error(describeApiError(error, "Không thể xóa tài liệu"));
    },
  });

  const openUploadDialog = (): void => {
    const defaultDepartment = isAdmin ? "" : me?.department_id ?? "";
    setUploadForm({ file: null, title: "", departmentId: defaultDepartment });
    setLocalFileError("");
    uploadMutation.reset();
    setUploadOpen(true);
  };

  const acceptFile = (incoming: File | null): void => {
    setLocalFileError("");
    if (!incoming) {
      setUploadForm((current) => ({ ...current, file: null }));
      return;
    }
    const lowerName = incoming.name.toLowerCase();
    if (!ACCEPTED_EXTENSIONS.some((extension) => lowerName.endsWith(extension))) {
      setLocalFileError(
        `Định dạng không hỗ trợ. Hệ thống nhận: ${ACCEPTED_EXTENSIONS.join(", ").toUpperCase()}.`,
      );
      setUploadForm((current) => ({ ...current, file: null }));
      return;
    }
    if (incoming.size === 0) {
      setLocalFileError("Tệp rỗng (0 byte) không thể bóc tách nội dung.");
      setUploadForm((current) => ({ ...current, file: null }));
      return;
    }
    if (incoming.size > MAX_UPLOAD_BYTES) {
      setLocalFileError(
        `Tệp ${formatBytes(incoming.size)} vượt giới hạn 25 MB. Hãy chia nhỏ tài liệu trước khi tải lên.`,
      );
      setUploadForm((current) => ({ ...current, file: null }));
      return;
    }
    setUploadForm((current) => ({
      ...current,
      file: incoming,
      title: current.title.trim() ? current.title : incoming.name.replace(/\.[^.]+$/, ""),
    }));
  };

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
    acceptFile(event.target.files?.[0] ?? null);
    event.target.value = "";
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault();
    setDragActive(false);
    acceptFile(event.dataTransfer.files?.[0] ?? null);
  };

  const handleUpload = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!uploadForm.file || uploadMutation.isPending) return;
    if (isEmployee && !me?.department_id) {
      setLocalFileError("Bạn chưa thuộc phòng ban nào. Liên hệ quản trị viên để được gán phòng ban.");
      return;
    }
    uploadMutation.mutate(uploadForm);
  };

  const canUpload = Boolean(uploadForm.file) && (!isEmployee || Boolean(me?.department_id));
  const uploadError = uploadMutation.isError ? uploadErrorHint(uploadMutation.error) : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Kho tài liệu"
        description="Tải lên PDF, DOCX, XLSX, TXT, MD… Hệ thống băm SHA-256, bóc tách văn bản rồi hủy file gốc (Zero Raw Storage), sau đó phân đoạn Parent-Child và gắn vector FAISS theo Tenant."
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => void documentsQuery.refetch()}
              disabled={documentsQuery.isFetching}
            >
              <RefreshCw className={documentsQuery.isFetching ? "animate-spin" : undefined} /> Làm mới
            </Button>
            <Button onClick={openUploadDialog}>
              <Upload /> Tải tài liệu
            </Button>
          </>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Tài liệu trong phạm vi"
          value={formatNumber(stats.documents)}
          hint={departmentFilter ? "Đang lọc theo phòng ban" : "Đã áp dụng ACL phòng ban của bạn"}
          icon={FileStack}
          tone="indigo"
        />
        <StatCard
          label="Parent chunks"
          value={formatNumber(stats.parentChunks)}
          hint="Ngữ cảnh rộng dùng để tổng hợp câu trả lời"
          icon={Layers}
          tone="sky"
        />
        <StatCard
          label="Child chunks"
          value={formatNumber(stats.childChunks)}
          hint="Đơn vị vector FAISS 786 chiều"
          icon={FileText}
          tone="violet"
        />
        <StatCard
          label="Dung lượng gốc"
          value={formatBytes(stats.storage)}
          hint="File gốc không được lưu trữ lâu dài"
          icon={HardDrive}
          tone="emerald"
        />
      </div>

      <Card>
        <CardHeader className="gap-3 sm:flex-row sm:items-center sm:justify-between">
          <CardTitle>Nguồn tri thức của doanh nghiệp</CardTitle>
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center">
            <div className="relative w-full sm:w-64">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="h-8 pl-8 text-xs"
                placeholder="Tìm theo tiêu đề hoặc tên tệp…"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
            </div>
            <Select
              className="h-8 w-full text-xs sm:w-52"
              value={departmentFilter}
              onChange={(event) => setDepartmentFilter(event.target.value)}
              aria-label="Lọc theo phòng ban"
            >
              <option value="">{isEmployee ? "Phòng ban của tôi + dùng chung" : "Tất cả phòng ban"}</option>
              {selectableDepartments.map((department) => (
                <option key={department.id} value={department.id}>
                  {department.name}
                </option>
              ))}
            </Select>
          </div>
        </CardHeader>
        <CardContent className="px-0 pb-0">
          {documentsQuery.isLoading ? (
            <div className="space-y-2 px-5 pb-5">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-12 rounded-lg" />
              ))}
            </div>
          ) : documentsQuery.isError ? (
            <div className="px-5 pb-5">
              <Alert variant="destructive" title="Không tải được danh sách tài liệu">
                {describeApiError(documentsQuery.error, "Vui lòng thử lại sau.")}
              </Alert>
            </div>
          ) : filteredDocuments.length === 0 ? (
            <EmptyState
              icon={FileStack}
              title={documents.length === 0 ? "Kho tri thức đang trống" : "Không khớp bộ lọc"}
              description={
                documents.length === 0
                  ? "Tải lên tài liệu đầu tiên để nhân viên có thể tìm kiếm và hỏi đáp RAG."
                  : "Thử từ khóa khác hoặc bỏ bộ lọc phòng ban."
              }
              action={
                documents.length === 0 ? (
                  <Button size="sm" onClick={openUploadDialog}>
                    <Upload /> Tải tài liệu đầu tiên
                  </Button>
                ) : null
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-5">Tài liệu</TableHead>
                  <TableHead>Trạng thái</TableHead>
                  <TableHead>Phòng ban</TableHead>
                  <TableHead>Phân đoạn</TableHead>
                  <TableHead>Dung lượng</TableHead>
                  <TableHead>Ngày tải</TableHead>
                  <TableHead className="pr-5 text-right">Thao tác</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredDocuments.map((document) => (
                  <TableRow
                    key={document.id}
                    className="cursor-pointer"
                    onClick={() => setDetailDoc(document)}
                  >
                    <TableCell className="pl-5">
                      <div className="flex items-center gap-2.5">
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-indigo-50 text-indigo-600">
                          <FileText className="h-4 w-4" />
                        </div>
                        <div className="min-w-0">
                          <p className="max-w-[280px] truncate text-xs font-medium">{document.title}</p>
                          <p className="max-w-[280px] truncate text-[10px] text-muted-foreground">
                            {document.file_name} · {document.file_type.toUpperCase()} ·{" "}
                            <span className="font-mono">{shortId(document.id)}</span>
                          </p>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANTS[document.status.toUpperCase()] ?? "secondary"}>
                        {documentStatusLabel(document.status)}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {document.department_id
                        ? departmentNames.get(document.department_id) ?? shortId(document.department_id)
                        : "Dùng chung"}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <Badge variant="outline">{formatNumber(document.parent_chunk_count)} P</Badge>
                        <Badge variant="secondary">{formatNumber(document.child_chunk_count)} C</Badge>
                      </div>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatBytes(document.file_size_bytes)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatDateTime(document.created_at)}
                    </TableCell>
                    <TableCell className="pr-5">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8"
                          aria-label={`Xem chi tiết ${document.title}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            setDetailDoc(document);
                          }}
                        >
                          <Eye />
                        </Button>
                        {isAdmin ? (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 text-destructive hover:text-destructive"
                            aria-label={`Xóa ${document.title}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              setPendingDelete(document);
                            }}
                          >
                            <Trash2 />
                          </Button>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <DocumentDetailDialog
        open={Boolean(detailDoc)}
        onOpenChange={(open) => {
          if (!open) setDetailDoc(null);
        }}
        documentId={detailDoc?.id ?? null}
        departmentName={
          detailDoc?.department_id
            ? departmentNames.get(detailDoc.department_id) ?? shortId(detailDoc.department_id)
            : undefined
        }
        onRequestDelete={
          isAdmin
            ? (document: DocumentDetailResponse) => {
                setPendingDelete(document);
              }
            : undefined
        }
      />

      <Dialog
        open={uploadOpen}
        onOpenChange={(open) => {
          if (!open) uploadMutation.reset();
          setUploadOpen(open);
        }}
        title="Tải tài liệu lên kho tri thức"
        description="Nội dung được bóc tách trong RAM, băm SHA-256 chống trùng lặp và hủy file gốc ngay sau khi xử lý."
        footer={
          <>
            <Button variant="outline" onClick={() => setUploadOpen(false)} disabled={uploadMutation.isPending}>
              Hủy
            </Button>
            <Button type="submit" form="document-upload-form" disabled={!canUpload || uploadMutation.isPending}>
              {uploadMutation.isPending ? <Spinner /> : <Upload />} Nạp tài liệu
            </Button>
          </>
        }
      >
        <form id="document-upload-form" onSubmit={handleUpload} className="space-y-4">
          {uploadError ? (
            <Alert variant={uploadMutation.error instanceof ApiError && uploadMutation.error.status === 409 ? "warning" : "destructive"} title={uploadError.title}>
              {uploadError.description}
            </Alert>
          ) : null}
          {localFileError ? <Alert variant="warning" title="Tệp chưa hợp lệ">{localFileError}</Alert> : null}

          <div
            onDragOver={(event) => {
              event.preventDefault();
              setDragActive(true);
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={handleDrop}
            className={cn(
              "rounded-xl border-2 border-dashed p-5 text-center transition-colors",
              dragActive ? "border-primary bg-primary/5" : "border-border bg-muted/20",
            )}
          >
            <input
              ref={fileInputRef}
              id="document-file"
              type="file"
              className="sr-only"
              accept={ACCEPTED_EXTENSIONS.join(",")}
              onChange={handleFileChange}
            />
            {uploadForm.file ? (
              <div className="flex items-center justify-between gap-3 text-left">
                <div className="flex min-w-0 items-center gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
                    <FileText className="h-5 w-5" />
                  </div>
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium">{uploadForm.file.name}</p>
                    <p className="text-[10px] text-muted-foreground">
                      {formatBytes(uploadForm.file.size)} · Sẵn sàng bóc tách
                    </p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 shrink-0"
                  aria-label="Bỏ chọn tệp"
                  onClick={() => acceptFile(null)}
                >
                  <X />
                </Button>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
                  <UploadCloud className="h-5 w-5" />
                </div>
                <p className="text-xs text-muted-foreground">
                  Kéo thả tệp vào đây hoặc{" "}
                  <button
                    type="button"
                    className="font-medium text-primary underline-offset-2 hover:underline"
                    onClick={() => fileInputRef.current?.click()}
                  >
                    chọn từ máy tính
                  </button>
                </p>
                <p className="text-[10px] text-muted-foreground">
                  Hỗ trợ PDF, DOCX, XLSX, TXT, MD, CSV, JSON, LOG, RST · Tối đa 25 MB
                </p>
              </div>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="document-title">Tiêu đề hiển thị (tùy chọn)</Label>
            <Input
              id="document-title"
              maxLength={255}
              placeholder="Mặc định lấy theo tên tệp"
              value={uploadForm.title}
              onChange={(event) =>
                setUploadForm((current) => ({ ...current, title: event.target.value }))
              }
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="document-department">Phòng ban trực thuộc</Label>
            {isEmployee ? (
              <Select id="document-department" value={me?.department_id ?? ""} disabled>
                <option value={me?.department_id ?? ""}>
                  {me?.department_id
                    ? departmentNames.get(me.department_id) ?? "Phòng ban của tôi"
                    : "Chưa được gán phòng ban"}
                </option>
              </Select>
            ) : (
              <Select
                id="document-department"
                value={uploadForm.departmentId}
                onChange={(event) =>
                  setUploadForm((current) => ({ ...current, departmentId: event.target.value }))
                }
              >
                <option value="">Dùng chung toàn doanh nghiệp</option>
                {departments.map((department) => (
                  <option key={department.id} value={department.id}>
                    {department.name}
                  </option>
                ))}
              </Select>
            )}
            <p className="text-[10px] text-muted-foreground">
              {isEmployee
                ? "Nhân viên chỉ được tải tài liệu vào đúng phòng ban của mình (Department ACL)."
                : "Tài liệu dùng chung đọc được bởi toàn bộ nhân viên; tài liệu phòng ban chỉ trong phạm vi phòng ban."}
            </p>
          </div>

          {isEmployee && !me?.department_id ? (
            <Alert variant="warning" title="Chưa thể tải lên">
              Tài khoản của bạn chưa thuộc phòng ban nào. Quản trị viên cần gán phòng ban trước khi bạn
              được phép nạp tài liệu.
            </Alert>
          ) : null}
        </form>
      </Dialog>

      <Dialog
        open={Boolean(pendingDelete)}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) setPendingDelete(null);
        }}
        title="Xóa vĩnh viễn tài liệu?"
        description={pendingDelete?.title}
        size="sm"
        footer={
          <>
            <Button variant="outline" onClick={() => setPendingDelete(null)} disabled={deleteMutation.isPending}>
              Hủy
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (pendingDelete) deleteMutation.mutate(pendingDelete.id);
              }}
            >
              {deleteMutation.isPending ? <Spinner /> : <Trash2 />} Xóa tài liệu
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-xs text-muted-foreground">
          <Alert variant="warning" title="Hành động không thể hoàn tác">
            Tài liệu, toàn bộ Parent/Child chunks trong PostgreSQL và vector tương ứng trong FAISS sẽ bị xóa.
            Khi khách hàng yêu cầu xóa dữ liệu, hệ thống xóa thật — không chỉ ẩn.
          </Alert>
          {pendingDelete ? (
            <dl className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border bg-muted/20 px-3 py-2">
                <dt className="text-[10px] uppercase tracking-wide">Tệp</dt>
                <dd className="mt-0.5 truncate text-foreground">{pendingDelete.file_name}</dd>
              </div>
              <div className="rounded-lg border bg-muted/20 px-3 py-2">
                <dt className="text-[10px] uppercase tracking-wide">Dung lượng</dt>
                <dd className="mt-0.5 text-foreground">{formatBytes(pendingDelete.file_size_bytes)}</dd>
              </div>
              <div className="rounded-lg border bg-muted/20 px-3 py-2">
                <dt className="text-[10px] uppercase tracking-wide">Parent / Child</dt>
                <dd className="mt-0.5 text-foreground">
                  {formatNumber(pendingDelete.parent_chunk_count)} / {formatNumber(pendingDelete.child_chunk_count)}
                </dd>
              </div>
              <div className="rounded-lg border bg-muted/20 px-3 py-2">
                <dt className="text-[10px] uppercase tracking-wide">Phòng ban</dt>
                <dd className="mt-0.5 truncate text-foreground">
                  {pendingDelete.department_id
                    ? departmentNames.get(pendingDelete.department_id) ?? shortId(pendingDelete.department_id)
                    : "Dùng chung"}
                </dd>
              </div>
            </dl>
          ) : null}
          {deleteMutation.isError ? (
            <Alert variant="destructive" title="Xóa thất bại">
              {describeApiError(deleteMutation.error, "Vui lòng thử lại.")}
            </Alert>
          ) : null}
          <p className="flex items-center gap-1.5">
            <FolderTree className="h-3.5 w-3.5" /> Vector FAISS được dọn dẹp ngay trong cùng giao dịch xóa.
          </p>
        </div>
      </Dialog>
    </div>
  );
}
