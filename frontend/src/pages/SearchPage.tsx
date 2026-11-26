import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  FileText,
  Gauge,
  Layers,
  Search,
  SlidersHorizontal,
  Sparkles,
  Target,
} from "lucide-react";
import { useAuth } from "@/features/auth/auth-context";
import { departmentApi, searchApi } from "@/lib/api";
import { describeApiError } from "@/lib/api-client";
import { formatNumber } from "@/lib/utils";
import type { SearchResultItem } from "@/lib/types";
import { PageHeader } from "@/components/layout/page-header";
import { Alert } from "@/components/feedback/alert";
import { EmptyState } from "@/components/feedback/empty-state";
import { Spinner } from "@/components/feedback/spinner";
import { DocumentDetailDialog } from "@/components/knowledge/document-detail-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const TOP_K_OPTIONS = [3, 5, 10, 20, 30, 50];

const WEIGHT_PRESETS = [
  { label: "Cân bằng", dense: 0.5 },
  { label: "Ngữ nghĩa", dense: 0.6 },
  { label: "Từ khóa", dense: 0.3 },
];

const EXAMPLE_QUERIES = [
  "Quy trình nghỉ phép của nhân viên",
  "Chính sách bảo mật dữ liệu khách hàng",
  "Điều kiện bảo hành sản phẩm",
];

interface SearchForm {
  query: string;
  topK: string;
  departmentId: string;
  denseWeight: number;
}

interface CitedTarget {
  documentId: string;
  childChunkId: string | null;
  heading: string | null;
  title: string;
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(4);
}

/** Trang Hybrid Search Explorer: FAISS + PostgreSQL FTS hợp nhất bằng RRF, hiển thị đầy đủ điểm thành phần. */
export function SearchPage() {
  const { me, isAdmin } = useAuth();
  const [form, setForm] = useState<SearchForm>({
    query: "",
    topK: "5",
    departmentId: "",
    denseWeight: 0.6,
  });
  const [target, setTarget] = useState<CitedTarget | null>(null);

  const departmentsQuery = useQuery({ queryKey: ["departments"], queryFn: departmentApi.list });
  const departments = departmentsQuery.data ?? [];

  /** Nhân viên chỉ thấy phòng ban của mình; phạm vi đọc thực tế vẫn do backend quyết định. */
  const selectableDepartments = useMemo(() => {
    if (isAdmin) return departments;
    return departments.filter((department) => department.id === me?.department_id);
  }, [departments, isAdmin, me?.department_id]);

  const searchMutation = useMutation({
    mutationFn: (payload: {
      query: string;
      top_k: number;
      department_id: string | null;
      dense_weight: number;
      sparse_weight: number;
    }) => searchApi.hybrid(payload),
  });

  const sparseWeight = Number((1 - form.denseWeight).toFixed(2));

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmed = form.query.trim();
    if (trimmed.length < 2 || searchMutation.isPending) return;
    searchMutation.mutate({
      query: trimmed,
      top_k: Number(form.topK),
      department_id: form.departmentId || null,
      dense_weight: form.denseWeight,
      sparse_weight: sparseWeight,
    });
  };

  const results = searchMutation.data?.results ?? [];
  const data = searchMutation.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tìm kiếm lai"
        description="Kết hợp FAISS (ngữ nghĩa) và PostgreSQL Full-Text Search (từ khóa) bằng Reciprocal Rank Fusion k=60. Kết quả trả về theo đúng phạm vi ACL phòng ban của bạn."
      />

      <Card>
        <CardContent className="space-y-4 p-5">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="flex flex-col gap-2 lg:flex-row">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  className="h-11 pl-9 text-sm"
                  placeholder="Nhập câu hỏi hoặc cụm từ khóa (2–1000 ký tự)…"
                  value={form.query}
                  maxLength={1000}
                  onChange={(event) => setForm((current) => ({ ...current, query: event.target.value }))}
                  aria-label="Truy vấn tìm kiếm"
                />
              </div>
              <Button type="submit" size="lg" className="h-11" disabled={form.query.trim().length < 2 || searchMutation.isPending}>
                {searchMutation.isPending ? <Spinner /> : <Search />} Tìm kiếm
              </Button>
            </div>

            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_160px_220px]">
              <div className="space-y-2 rounded-lg border bg-muted/20 px-3 py-2.5">
                <div className="flex items-center justify-between">
                  <Label className="flex items-center gap-1.5">
                    <SlidersHorizontal className="h-3.5 w-3.5" /> Trọng số dung hợp
                  </Label>
                  <div className="flex items-center gap-1.5">
                    <Badge variant="info">dense {form.denseWeight.toFixed(2)}</Badge>
                    <Badge variant="violet">sparse {sparseWeight.toFixed(2)}</Badge>
                  </div>
                </div>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={form.denseWeight}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, denseWeight: Number(event.target.value) }))
                  }
                  className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-slate-200 accent-indigo-600"
                  aria-label="Trọng số tìm kiếm ngữ nghĩa FAISS"
                />
                <div className="flex flex-wrap items-center gap-1.5">
                  {WEIGHT_PRESETS.map((preset) => (
                    <Button
                      key={preset.label}
                      type="button"
                      variant={Math.abs(form.denseWeight - preset.dense) < 0.001 ? "secondary" : "ghost"}
                      size="sm"
                      className="h-6 px-2 text-[10px]"
                      onClick={() =>
                        setForm((current) => ({ ...current, denseWeight: preset.dense }))
                      }
                    >
                      {preset.label} {preset.dense.toFixed(1)}/{ (1 - preset.dense).toFixed(1)}
                    </Button>
                  ))}
                  <span className="text-[10px] text-muted-foreground">Kéo về 1 để ưu tiên ngữ nghĩa.</span>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="search-top-k">Số kết quả (Top-K)</Label>
                <Select
                  id="search-top-k"
                  value={form.topK}
                  onChange={(event) => setForm((current) => ({ ...current, topK: event.target.value }))}
                >
                  {TOP_K_OPTIONS.map((value) => (
                    <option key={value} value={value}>
                      {value} phân đoạn
                    </option>
                  ))}
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="search-department">Phòng ban</Label>
                <Select
                  id="search-department"
                  value={form.departmentId}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, departmentId: event.target.value }))
                  }
                >
                  <option value="">Toàn bộ phạm vi được phép</option>
                  {selectableDepartments.map((department) => (
                    <option key={department.id} value={department.id}>
                      {department.name}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          </form>

          {searchMutation.isError ? (
            <Alert variant="destructive" title="Tìm kiếm thất bại">
              {describeApiError(searchMutation.error, "Vui lòng thử lại sau.")}
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      {searchMutation.isPending ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-36 rounded-xl" />
          ))}
        </div>
      ) : data ? (
        data.total_found === 0 ? (
          <Card>
            <EmptyState
              icon={Search}
              title="Không tìm thấy phân đoạn phù hợp"
              description="Thử mở rộng phạm vi phòng ban, giảm trọng số từ khóa hoặc diễn đạt lại câu hỏi."
            />
          </Card>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Sparkles className="h-3.5 w-3.5 text-indigo-500" />
              <span>
                <span className="font-medium text-foreground">{formatNumber(data.total_found)}</span> phân đoạn cho
                “<span className="font-medium text-foreground">{data.query}</span>”
              </span>
              <Badge variant="outline">RRF k=60</Badge>
              <Badge variant="info">dense {form.denseWeight.toFixed(2)}</Badge>
              <Badge variant="violet">sparse {sparseWeight.toFixed(2)}</Badge>
            </div>

            {results.map((result: SearchResultItem, index: number) => (
              <Card key={result.child_chunk_id} className="overflow-hidden">
                <CardHeader className="flex-row items-start gap-3 space-y-0 p-4 pb-2">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-xs font-semibold text-indigo-600">
                    #{index + 1}
                  </div>
                  <div className="min-w-0 flex-1 space-y-1">
                    <CardTitle className="truncate text-sm">{result.document_title}</CardTitle>
                    <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      <FileText className="h-3 w-3 shrink-0" />
                      {result.heading || "Không có tiêu đề phân đoạn"}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
                    <Badge variant="default" className="font-mono">
                      RRF {formatScore(result.rrf_score)}
                    </Badge>
                    <Badge variant="info" className="font-mono">
                      D {formatScore(result.dense_score)}
                    </Badge>
                    <Badge variant="violet" className="font-mono">
                      S {formatScore(result.sparse_score)}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3 p-4 pt-1">
                  <p className="whitespace-pre-wrap rounded-lg border border-indigo-100 bg-indigo-50/40 px-3 py-2.5 text-xs leading-relaxed text-foreground">
                    {result.child_content}
                  </p>

                  <details className="group">
                    <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground">
                      <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
                      Ngữ cảnh Parent (dùng để tổng hợp câu trả lời)
                    </summary>
                    <div className="mt-2 max-h-52 overflow-y-auto whitespace-pre-wrap rounded-lg border bg-muted/20 px-3 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
                      {result.parent_content}
                    </div>
                  </details>

                  <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
                    <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                      <Gauge className="h-3.5 w-3.5" /> Dung hợp xếp hạng nghịch đảo
                      <span className="hidden font-mono sm:inline">child={result.child_chunk_id.slice(0, 8)}</span>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        setTarget({
                          documentId: result.document_id,
                          childChunkId: result.child_chunk_id,
                          heading: result.heading,
                          title: result.document_title,
                        })
                      }
                    >
                      <Target /> Mở đúng đoạn trong tài liệu
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )
      ) : (
        <Card>
          <EmptyState
            icon={Layers}
            title="Bắt đầu khám phá tri thức doanh nghiệp"
            description="Nhập câu hỏi để xem cách FAISS và FTS cùng đóng góp vào kết quả. Mỗi thẻ hiển thị điểm RRF, điểm dense và sparse riêng biệt."
            action={
              <div className="flex flex-wrap items-center justify-center gap-2">
                {EXAMPLE_QUERIES.map((example) => (
                  <Button
                    key={example}
                    variant="outline"
                    size="sm"
                    onClick={() => setForm((current) => ({ ...current, query: example }))}
                  >
                    {example}
                  </Button>
                ))}
              </div>
            }
          />
        </Card>
      )}

      <DocumentDetailDialog
        open={Boolean(target)}
        onOpenChange={(open) => {
          if (!open) setTarget(null);
        }}
        documentId={target?.documentId ?? null}
        highlightChildChunkId={target?.childChunkId ?? null}
        highlightHeading={target?.heading ?? null}
      />
    </div>
  );
}
