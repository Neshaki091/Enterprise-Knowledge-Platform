import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText, Layers, ScrollText, Tags, Trash2 } from "lucide-react";
import { documentApi } from "@/lib/api";
import { describeApiError } from "@/lib/api-client";
import { documentStatusLabel } from "@/lib/labels";
import { cn, formatBytes, formatDateTime, formatNumber, shortId } from "@/lib/utils";
import type { DocumentDetailResponse } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert } from "@/components/feedback/alert";
import { Spinner } from "@/components/feedback/spinner";

interface DocumentDetailDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  documentId: string | null;
  /** Child chunk cần cuộn tới và làm nổi bật (dùng khi bấm citation). */
  highlightChildChunkId?: string | null;
  /** Fallback khớp theo heading khi citation cũ không có child_chunk_id. */
  highlightHeading?: string | null;
  departmentName?: string;
  onRequestDelete?: (document: DocumentDetailResponse) => void;
}

const STATUS_VARIANTS: Record<string, "success" | "warning" | "destructive"> = {
  PROCESSED: "success",
  PROCESSING: "warning",
  FAILED: "destructive",
};

/** Hộp thoại dùng chung: xem phân cấp Parent-Child, raw text, metadata và mở đúng đoạn được trích dẫn. */
export function DocumentDetailDialog({
  open,
  onOpenChange,
  documentId,
  highlightChildChunkId,
  highlightHeading,
  departmentName,
  onRequestDelete,
}: DocumentDetailDialogProps) {
  const [tab, setTab] = useState("structure");
  const targetRef = useRef<HTMLDetailsElement | null>(null);

  const detailQuery = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => documentApi.get(documentId as string),
    enabled: open && Boolean(documentId),
    staleTime: 15_000,
  });

  const data = detailQuery.data;

  const targetParentIndex = useMemo(() => {
    if (!data) return -1;
    if (highlightChildChunkId) {
      const byChild = data.parent_chunks.findIndex((parent) =>
        parent.child_chunks.some((child) => child.id === highlightChildChunkId),
      );
      if (byChild >= 0) return byChild;
    }
    if (highlightHeading) {
      const target = highlightHeading.trim().toLowerCase();
      const byHeading = data.parent_chunks.findIndex(
        (parent) => (parent.heading ?? "").trim().toLowerCase() === target,
      );
      if (byHeading >= 0) return byHeading;
    }
    return -1;
  }, [data, highlightChildChunkId, highlightHeading]);

  useEffect(() => {
    if (!open) return;
    setTab("structure");
  }, [open, documentId]);

  useEffect(() => {
    if (!open || !data || targetParentIndex < 0) return;
    setTab("structure");
    const timer = window.setTimeout(() => {
      targetRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }, 150);
    return () => window.clearTimeout(timer);
  }, [open, data, targetParentIndex]);

  const metadataEntries = useMemo(() => {
    if (!data?.doc_metadata) return [];
    return Object.entries(data.doc_metadata).map(([key, value]) => ({
      key,
      value: typeof value === "object" && value !== null ? JSON.stringify(value) : String(value),
    }));
  }, [data]);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={data?.title ?? "Chi tiết tài liệu"}
      description={data ? `${data.file_name} · ${documentStatusLabel(data.status)}` : "Đang tải cấu trúc phân đoạn…"}
      size="xl"
      footer={
        <>
          {onRequestDelete && data ? (
            <Button
              variant="destructive"
              className="mr-auto"
              onClick={() => {
                onOpenChange(false);
                onRequestDelete(data);
              }}
            >
              <Trash2 /> Xóa tài liệu
            </Button>
          ) : null}
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Đóng
          </Button>
        </>
      }
    >
      {detailQuery.isLoading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
          <Spinner /> Đang tải phân cấp chunks…
        </div>
      ) : detailQuery.isError ? (
        <Alert variant="destructive" title="Không tải được tài liệu">
          {describeApiError(detailQuery.error, "Vui lòng thử lại sau.")}
        </Alert>
      ) : data ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={STATUS_VARIANTS[data.status.toUpperCase()] ?? "secondary"}>
              {documentStatusLabel(data.status)}
            </Badge>
            <Badge variant="outline" className="uppercase">{data.file_type || "TXT"}</Badge>
            <Badge variant="info">
              <Layers className="h-3 w-3" /> {formatNumber(data.parent_chunk_count)} parent
            </Badge>
            <Badge variant="violet">{formatNumber(data.child_chunk_count)} child</Badge>
            <span className="text-[11px] text-muted-foreground">
              {formatBytes(data.file_size_bytes)} · {formatNumber(data.token_count)} tokens ·{" "}
              {formatDateTime(data.created_at)}
            </span>
          </div>

          <p className="font-mono text-[10px] text-muted-foreground break-all">
            SHA-256: {data.file_hash}
          </p>

          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="structure">
                <Layers className="h-3.5 w-3.5" /> Phân cấp Chunks
              </TabsTrigger>
              <TabsTrigger value="raw">
                <ScrollText className="h-3.5 w-3.5" /> Văn bản gốc
              </TabsTrigger>
              <TabsTrigger value="metadata">
                <Tags className="h-3.5 w-3.5" /> Metadata
              </TabsTrigger>
            </TabsList>

            <TabsContent value="structure">
              {data.parent_chunks.length === 0 ? (
                <p className="py-8 text-center text-xs text-muted-foreground">Tài liệu chưa có phân đoạn.</p>
              ) : (
                <div className="space-y-2">
                  {data.parent_chunks.map((parent, index) => {
                    const highlighted = index === targetParentIndex;
                    return (
                      <details
                        key={parent.id}
                        open={highlighted || undefined}
                        ref={highlighted ? targetRef : undefined}
                        className={cn(
                          "group rounded-lg border bg-card",
                          highlighted && "border-primary/60 ring-1 ring-primary/30",
                        )}
                      >
                        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2.5">
                          <div className="flex min-w-0 items-center gap-2">
                            <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                            <span className="truncate text-xs font-medium">
                              {parent.heading || `Phân đoạn #${parent.chunk_index + 1}`}
                            </span>
                          </div>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <Badge variant="outline">{formatNumber(parent.token_count)} tokens</Badge>
                            <Badge variant="secondary">{parent.child_chunks.length} child</Badge>
                          </div>
                        </summary>
                        <div className="space-y-2.5 border-t px-3 py-3">
                          <div className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded-md bg-muted/20 p-2.5 text-xs leading-relaxed text-muted-foreground">
                            {parent.content}
                          </div>
                          <div className="space-y-1.5">
                            {parent.child_chunks.map((child) => {
                              const isCitationTarget = child.id === highlightChildChunkId;
                              return (
                                <div
                                  key={child.id}
                                  className={cn(
                                    "rounded-md border bg-muted/30 px-2.5 py-2",
                                    isCitationTarget && "border-primary/60 bg-primary/5",
                                  )}
                                >
                                  <div className="mb-1 flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
                                    <span>
                                      Child #{child.chunk_index + 1} · {formatNumber(child.token_count)} tokens
                                    </span>
                                    <span className="font-mono">
                                      faiss={child.faiss_id === null ? "—" : child.faiss_id}
                                    </span>
                                  </div>
                                  <p className="whitespace-pre-wrap text-[11px] leading-relaxed">
                                    {child.content}
                                  </p>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </details>
                    );
                  })}
                </div>
              )}
            </TabsContent>

            <TabsContent value="raw">
              <pre className="max-h-[52vh] overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/20 p-3 text-xs leading-relaxed">
                {data.raw_text}
              </pre>
            </TabsContent>

            <TabsContent value="metadata">
              <dl className="grid gap-2 text-xs sm:grid-cols-2">
                <div className="rounded-lg border bg-muted/20 px-3 py-2">
                  <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">Phòng ban</dt>
                  <dd className="mt-0.5">{departmentName ?? (data.department_id ? shortId(data.department_id) : "Dùng chung toàn doanh nghiệp")}</dd>
                </div>
                <div className="rounded-lg border bg-muted/20 px-3 py-2">
                  <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">ID tài liệu</dt>
                  <dd className="mt-0.5 font-mono text-[10px] break-all">{data.id}</dd>
                </div>
                <div className="rounded-lg border bg-muted/20 px-3 py-2">
                  <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">Kích thước</dt>
                  <dd className="mt-0.5">{formatBytes(data.file_size_bytes)}</dd>
                </div>
                <div className="rounded-lg border bg-muted/20 px-3 py-2">
                  <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">Token</dt>
                  <dd className="mt-0.5">{formatNumber(data.token_count)}</dd>
                </div>
                {metadataEntries.map((entry) => (
                  <div key={entry.key} className="rounded-lg border bg-muted/20 px-3 py-2">
                    <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">{entry.key}</dt>
                    <dd className="mt-0.5 break-all">{entry.value}</dd>
                  </div>
                ))}
              </dl>
            </TabsContent>
          </Tabs>
        </div>
      ) : null}
    </Dialog>
  );
}
