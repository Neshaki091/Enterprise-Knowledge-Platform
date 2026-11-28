import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  CircleStop,
  FileText,
  Layers,
  MessageSquareText,
  Paperclip,
  RefreshCw,
  Send,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Timer,
  Undo2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/features/auth/auth-context";
import { chatApi, departmentApi, systemApi } from "@/lib/api";
import { ApiError, describeApiError, streamChat } from "@/lib/api-client";
import { formatNumber } from "@/lib/utils";
import type {
  ChatAttachment,
  ChatCitation,
  ChatHistoryTurn,
  ChatMode,
  ChatStreamDone,
  CompressionStats,
  SearchResultItem,
} from "@/lib/types";
import { PageHeader } from "@/components/layout/page-header";
import { Alert } from "@/components/feedback/alert";
import { EmptyState } from "@/components/feedback/empty-state";
import { Spinner } from "@/components/feedback/spinner";
import { MarkdownContent } from "@/components/knowledge/markdown-content";
import { DocumentDetailDialog } from "@/components/knowledge/document-detail-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

type FeedbackRating = "THUMBS_UP" | "THUMBS_DOWN";
type EntryStatus = "streaming" | "done" | "error" | "aborted";

interface ConversationEntry {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Câu hỏi gốc (assistant) phục vụ gửi feedback. */
  query?: string;
  mode?: ChatMode;
  chunks?: SearchResultItem[];
  citations?: ChatCitation[];
  compression?: CompressionStats;
  done?: ChatStreamDone;
  status?: EntryStatus;
  error?: string;
  feedback?: FeedbackRating;
  startedAt?: number;
  firstTokenMs?: number | null;
}

interface CitationTarget {
  documentId: string;
  childChunkId: string | null;
  heading: string | null;
}

interface FeedbackVariables {
  entryId: string;
  query: string;
  answer: string;
  rating: FeedbackRating;
}

const MODE_LABELS: Record<ChatMode, string> = {
  hybrid: "Hybrid",
  real: "Real LLM",
  retrieval_only: "Chỉ truy xuất",
};

const TOP_K_OPTIONS = [1, 2, 3, 5, 8, 10];
const CONTEXT_TOKEN_OPTIONS = [250, 400, 550, 800, 1200];

const EXAMPLE_QUESTIONS = [
  "Tóm tắt quy trình nghỉ phép của nhân viên",
  "Chính sách bảo mật dữ liệu khách hàng gồm những gì?",
  "Điều kiện bảo hành sản phẩm trong bao lâu?",
];

function createEntryId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const SESSION_STORAGE_PREFIX = "ekp.chat.session.";

/** UUID hợp lệ cho session_id backend; fallback cho trình duyệt cũ. */
function createSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const random = (Math.random() * 16) | 0;
    const value = char === "x" ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

function isUuidLike(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function sessionStorageKey(tenantId: string, userId: string): string {
  return `${SESSION_STORAGE_PREFIX}${tenantId}.${userId}`;
}

function historyMode(raw: string | null): ChatMode {
  return raw === "real" || raw === "retrieval_only" ? raw : "hybrid";
}

/** Chuyển các lượt đã lưu thành cặp user/assistant để render lại sau khi reload. */
function historyToEntries(turns: ChatHistoryTurn[]): ConversationEntry[] {
  const result: ConversationEntry[] = [];
  for (const turn of [...turns].reverse()) {
    result.push({
      id: `${turn.id}-user`,
      role: "user",
      content: turn.query,
      status: "done",
    });

    const chunks = (turn.contexts ?? [])
      .map((raw) => {
        const context = raw as Record<string, unknown>;
        const text =
          typeof context.text === "string"
            ? context.text
            : typeof context.parent_content === "string"
              ? context.parent_content
              : typeof context.child_content === "string"
                ? context.child_content
                : "";
        return {
          child_chunk_id: typeof context.child_chunk_id === "string" ? context.child_chunk_id : "",
          parent_chunk_id: typeof context.parent_chunk_id === "string" ? context.parent_chunk_id : "",
          document_id: typeof context.document_id === "string" ? context.document_id : "",
          document_title: typeof context.document_title === "string" ? context.document_title : "",
          heading: typeof context.heading === "string" ? context.heading : null,
          child_content: text,
          parent_content: text,
          rrf_score: typeof context.rrf_score === "number" ? context.rrf_score : 0,
          dense_score: typeof context.dense_score === "number" ? context.dense_score : null,
          sparse_score: typeof context.sparse_score === "number" ? context.sparse_score : null,
        } satisfies SearchResultItem;
      })
      .filter((chunk) => chunk.child_chunk_id && chunk.document_id);

    const mode = historyMode(turn.mode);
    result.push({
      id: turn.id,
      role: "assistant",
      content: turn.answer,
      query: turn.query,
      mode,
      chunks: chunks.length > 0 ? chunks : undefined,
      citations: turn.citations ?? [],
      compression: {
        original_tokens: turn.input_tokens,
        compressed_tokens: turn.compressed_tokens,
        savings_percent: turn.compression_savings_percent,
        retained_chunks: chunks.length,
      },
      done: {
        type: "done",
        mode,
        tokens_saved_percent: turn.compression_savings_percent,
        retained_chunks: chunks.length,
        output_tokens: turn.output_tokens,
        session_id: turn.session_id,
      },
      status: "done",
      feedback: turn.feedback_rating ?? undefined,
      firstTokenMs: turn.ttfb_ms,
    });
  }
  return result;
}

/** Hiển thị thống kê nén ngữ cảnh 3 tầng do backend trả về trong sự kiện chunks/done. */
function CompressionSummary({
  stats,
  done,
}: {
  stats?: CompressionStats;
  done?: ChatStreamDone;
}) {
  if (!stats) return null;
  const savingsRaw = done?.tokens_saved_percent ?? stats.savings_percent;
  const savings = typeof savingsRaw === "number" ? savingsRaw : null;
  const original = typeof stats.original_tokens === "number" ? stats.original_tokens : null;
  const compressed = typeof stats.compressed_tokens === "number" ? stats.compressed_tokens : null;
  const retainedRaw = done?.retained_chunks ?? stats.retained_chunks;
  const retained = typeof retainedRaw === "number" ? retainedRaw : null;
  const promptTokens = typeof stats.prompt_input_tokens === "number" ? stats.prompt_input_tokens : null;

  return (
    <div className="flex flex-wrap items-center gap-1">
      {savings !== null ? (
        <Badge variant="success" className="font-mono" title="Tỷ lệ token tiết kiệm sau Dynamic Threshold ➔ Dedup ➔ Sentence Compression">
          nén {savings.toFixed(1)}%
        </Badge>
      ) : null}
      {original !== null && compressed !== null ? (
        <Badge variant="outline" className="font-mono">
          {formatNumber(original)} → {formatNumber(compressed)} tok
        </Badge>
      ) : null}
      {retained !== null ? <Badge variant="secondary">{retained} chunks</Badge> : null}
      {promptTokens !== null ? (
        <Badge variant="info" className="font-mono">
          prompt {formatNumber(promptTokens)} tok
        </Badge>
      ) : null}
    </div>
  );
}

/** Chấm nhấp nháy khi chờ token đầu tiên từ LLM. */
function StreamingDots({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
      <span className="flex items-center gap-1">
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-400" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-400 [animation-delay:120ms]" />
        <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-400 [animation-delay:240ms]" />
      </span>
      {label}
    </span>
  );
}

/** Trang RAG Streaming Chat: chunks/citations hiển thị trước, token chảy sau, feedback và trích dẫn mở đúng đoạn. */
export function ChatPage() {
  const { me, isAdmin } = useAuth();
  const [entries, setEntries] = useState<ConversationEntry[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<ChatMode>("hybrid");
  const [topK, setTopK] = useState("3");
  const [contextTokens, setContextTokens] = useState("550");
  const [departmentId, setDepartmentId] = useState("");
  const [target, setTarget] = useState<CitationTarget | null>(null);
  const [cooldownUntil, setCooldownUntil] = useState<number | null>(null);
  const [clock, setClock] = useState(Date.now());
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [rollingBack, setRollingBack] = useState(false);
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [attachmentUploading, setAttachmentUploading] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const attachmentInputRef = useRef<HTMLInputElement | null>(null);
  const tenantId = me?.tenant_id;

  const departmentsQuery = useQuery({ queryKey: ["departments"], queryFn: departmentApi.list });
  const healthQuery = useQuery({
    queryKey: ["system", "health"],
    queryFn: systemApi.health,
    staleTime: 60_000,
    retry: false,
  });

  const departments = departmentsQuery.data ?? [];
  const health = healthQuery.data;
  const selectableDepartments = isAdmin
    ? departments
    : departments.filter((department) => department.id === me?.department_id);

  const isStreaming = entries.some((entry) => entry.status === "streaming");
  const lastEntry = entries.length > 0 ? entries[entries.length - 1] : undefined;
  const canRollback =
    !isStreaming && lastEntry?.role === "assistant" && lastEntry.status === "done";

  useEffect(() => {
    const handle = window.setInterval(() => setClock(Date.now()), 500);
    return () => window.clearInterval(handle);
  }, []);

  const cooldownSeconds = cooldownUntil ? Math.max(0, Math.ceil((cooldownUntil - clock) / 1000)) : 0;

  useEffect(() => {
    if (cooldownUntil && cooldownSeconds === 0) setCooldownUntil(null);
  }, [cooldownUntil, cooldownSeconds]);

  // Đổi tenant/user phải dừng luồng đang chạy và xóa hội thoại cũ để tránh rò rỉ ngữ cảnh chéo Tenant.
  useEffect(() => {
    abortRef.current?.abort();
    setEntries([]);
    setTarget(null);
    setInput("");
    setCooldownUntil(null);
    setSessionId(null);
    setHistoryLoading(false);
    setAttachments([]);
  }, [tenantId, me?.user_id]);

  // Mỗi cặp tenant/user có session riêng trong localStorage để reload vẫn nối đúng hội thoại.
  useEffect(() => {
    if (!tenantId || !me?.user_id) return;
    const key = sessionStorageKey(tenantId, me.user_id);
    let stored: string | null = window.localStorage.getItem(key);
    if (!stored || !isUuidLike(stored)) {
      stored = createSessionId();
      window.localStorage.setItem(key, stored);
    }
    setSessionId(stored);
  }, [tenantId, me?.user_id]);

  // Nạp lại các lượt đã lưu của phiên hiện tại (chỉ khi người dùng chưa gửi lượt mới).
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    setHistoryLoading(true);
    chatApi
      .history(sessionId, 50)
      .then((response) => {
        if (cancelled) return;
        const loaded = historyToEntries(response.turns);
        setEntries((current) => (current.length === 0 ? loaded : current));
      })
      .catch((error) => {
        if (!cancelled) {
          toast.error(describeApiError(error, "Không tải được lịch sử hội thoại."));
        }
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // Nạp danh sách tài liệu đính kèm còn hạn của phiên (chỉ metadata, không nội dung).
  useEffect(() => {
    if (!sessionId) {
      setAttachments([]);
      return;
    }
    let cancelled = false;
    chatApi
      .attachments(sessionId)
      .then((response) => {
        if (!cancelled) setAttachments(response.attachments);
      })
      .catch(() => {
        if (!cancelled) setAttachments([]);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const nearBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight < 220;
    if (nearBottom) container.scrollTop = container.scrollHeight;
  }, [entries]);

  const updateEntry = (
    entryId: string,
    updater: (entry: ConversationEntry) => ConversationEntry,
  ): void => {
    setEntries((current) =>
      current.map((entry) => (entry.id === entryId ? updater(entry) : entry)),
    );
  };

  const feedbackMutation = useMutation({
    mutationFn: (variables: FeedbackVariables) =>
      chatApi.feedback({
        query: variables.query,
        answer: variables.answer,
        rating: variables.rating,
      }),
    onSuccess: (_response, variables) => {
      updateEntry(variables.entryId, (entry) => ({ ...entry, feedback: variables.rating }));
      toast.success("Đã ghi nhận đánh giá của bạn");
    },
    onError: (error) => {
      toast.error(describeApiError(error, "Không gửi được đánh giá"));
    },
  });

  const persistSession = (id: string): void => {
    if (!tenantId || !me?.user_id) return;
    try {
      window.localStorage.setItem(sessionStorageKey(tenantId, me.user_id), id);
    } catch {
      // localStorage bị chặn: vẫn dùng session trong bộ nhớ của tab hiện tại.
    }
  };

  const ensureSessionId = (): string => {
    if (sessionId) return sessionId;
    const next = createSessionId();
    setSessionId(next);
    persistSession(next);
    return next;
  };

  const uploadAttachment = async (file: File): Promise<void> => {
    if (!file || attachmentUploading) return;
    const activeSessionId = ensureSessionId();
    setAttachmentUploading(true);
    try {
      const attachment = await chatApi.uploadAttachment(activeSessionId, file);
      setAttachments((current) => [...current, attachment]);
      toast.success(`Đã đính kèm ${attachment.file_name} (${attachment.chunk_count} phân đoạn)`);
    } catch (error) {
      toast.error(describeApiError(error, "Không tải được tài liệu đính kèm."));
    } finally {
      setAttachmentUploading(false);
      if (attachmentInputRef.current) attachmentInputRef.current.value = "";
    }
  };

  const removeAttachment = async (attachmentId: string): Promise<void> => {
    if (attachmentUploading) return;
    try {
      await chatApi.deleteAttachment(attachmentId);
      setAttachments((current) => current.filter((item) => item.id !== attachmentId));
      toast.success("Đã xoá tài liệu đính kèm.");
    } catch (error) {
      toast.error(describeApiError(error, "Không xoá được tài liệu đính kèm."));
    }
  };

  const sendQuery = async (rawQuery: string): Promise<void> => {
    const trimmed = rawQuery.trim();
    if (trimmed.length < 2 || trimmed.length > 2000) return;
    if (abortRef.current || cooldownSeconds > 0 || attachmentUploading) return;

    const activeSessionId = ensureSessionId();

    const assistantId = createEntryId();
    const userEntry: ConversationEntry = {
      id: createEntryId(),
      role: "user",
      content: trimmed,
    };
    const assistantEntry: ConversationEntry = {
      id: assistantId,
      role: "assistant",
      content: "",
      query: trimmed,
      mode,
      status: "streaming",
      startedAt: performance.now(),
      firstTokenMs: null,
    };

    setEntries((current) => [...current, userEntry, assistantEntry]);
    setInput("");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(
        {
          query: trimmed,
          top_k: Number(topK),
          max_context_tokens: Number(contextTokens),
          department_id: departmentId || null,
          mode,
          session_id: activeSessionId,
        },
        {
          onChunks: (chunks, stats) => {
            updateEntry(assistantId, (entry) => ({ ...entry, chunks, compression: stats }));
          },
          onCitations: (citations) => {
            updateEntry(assistantId, (entry) => ({ ...entry, citations }));
          },
          onToken: (token) => {
            updateEntry(assistantId, (entry) => ({
              ...entry,
              content: entry.content + token,
              firstTokenMs:
                entry.firstTokenMs ??
                Math.round(performance.now() - (entry.startedAt ?? performance.now())),
            }));
          },
          onDone: (done) => {
            if (done.session_id && done.session_id !== activeSessionId) {
              setSessionId(done.session_id);
              persistSession(done.session_id);
            }
            updateEntry(assistantId, (entry) => ({ ...entry, done, status: "done" }));
          },
          onError: (message) => {
            updateEntry(assistantId, (entry) => ({ ...entry, status: "error", error: message }));
          },
        },
        controller.signal,
      );
      updateEntry(assistantId, (entry) =>
        entry.status === "streaming" ? { ...entry, status: "done" } : entry,
      );
    } catch (error) {
      if (controller.signal.aborted) {
        updateEntry(assistantId, (entry) => ({ ...entry, status: "aborted" }));
      } else {
        let message = describeApiError(error, "Không thể kết nối luồng trả lời.");
        if (error instanceof ApiError && error.status === 429) {
          const retryAfter = error.retryAfter && error.retryAfter > 0 ? error.retryAfter : 30;
          setCooldownUntil(Date.now() + retryAfter * 1000);
          message = `${message} Hạn mức 30 RPM / 16K TPM được chia sẻ theo Tenant.`;
        }
        updateEntry(assistantId, (entry) => ({ ...entry, status: "error", error: message }));
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendQuery(input);
    }
  };

  const resetConversation = (): void => {
    abortRef.current?.abort();
    const nextSession = createSessionId();
    persistSession(nextSession);
    setSessionId(nextSession);
    setEntries([]);
    setTarget(null);
    setCooldownUntil(null);
    setHistoryLoading(false);
    setAttachments([]);
  };

  const rollbackLastTurn = async (): Promise<void> => {
    if (!sessionId || !canRollback || rollingBack) return;
    setRollingBack(true);
    try {
      const result = await chatApi.rollback(sessionId);
      if (!result.deleted) {
        toast.info("Phiên này không còn lượt nào để hoàn tác.");
        return;
      }
      setEntries((current) => {
        const next = [...current];
        for (let index = next.length - 1; index >= 0; index -= 1) {
          if (next[index].role !== "assistant") continue;
          next.splice(index, 1);
          if (index > 0 && next[index - 1].role === "user") {
            next.splice(index - 1, 1);
          }
          break;
        }
        return next;
      });
      toast.success("Đã hoàn tác lượt hỏi-đáp cuối.");
    } catch (error) {
      toast.error(describeApiError(error, "Không thể hoàn tác lượt chat."));
    } finally {
      setRollingBack(false);
    }
  };

  const sendDisabled =
    input.trim().length < 2 || isStreaming || cooldownSeconds > 0 || attachmentUploading;

  const providerBadge = healthQuery.isError ? (
    <Badge variant="destructive">Không đọc được trạng thái LLM</Badge>
  ) : !health ? (
    <Badge variant="outline">Đang kiểm tra LLM…</Badge>
  ) : health.llm_real_configured ? (
    <Badge variant="success">
      <span className="mr-1 h-1.5 w-1.5 rounded-full bg-emerald-500" /> LLM thật sẵn sàng
    </Badge>
  ) : (
    <Badge variant="warning">
      <span className="mr-1 h-1.5 w-1.5 rounded-full bg-amber-500" /> Mock grounded (chưa cấu hình khóa)
    </Badge>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Trợ lý tri thức"
        description="Hỏi đáp RAG theo thời gian thực: truy xuất FAISS + FTS, lọc ngữ cảnh 3 tầng rồi mới sinh câu trả lời. Session memory dùng các lượt gần nhất; Hoàn tác xoá lượt cuối khỏi lịch sử."
        actions={
          <>
            <Button
              variant="outline"
              onClick={() => void rollbackLastTurn()}
              disabled={!canRollback || rollingBack}
              title="Xoá lượt hỏi-đáp gần nhất khỏi phiên và session memory"
            >
              <Undo2 /> {rollingBack ? "Đang hoàn tác..." : "Hoàn tác lượt cuối"}
            </Button>
            <Button
              variant="outline"
              onClick={resetConversation}
              disabled={entries.length === 0 || attachmentUploading}
            >
              <RefreshCw /> Cuộc trò chuyện mới
            </Button>
          </>
        }
      />

      {health && !health.llm_real_configured ? (
        <Alert variant="info" title="Backend chưa cấu hình khóa LLM">
          Chế độ <span className="font-medium">Hybrid</span> tự chuyển sang Mock grounded answer dựa trên ngữ cảnh
          truy xuất; chế độ <span className="font-medium">Real</span> sẽ báo lỗi 503 sau khi trả chunks. Cấu hình
          API key trên server (không nhập vào trình duyệt) để bật LLM thật.
        </Alert>
      ) : null}

      {mode === "real" && health && !health.llm_real_configured ? (
        <Alert variant="warning" title="Real LLM chưa khả dụng">
          Chế độ này bắt buộc cấu hình LLM_API_URL và GOOGLE_API_KEY/LLM_API_KEY ở backend. Luồng SSE vẫn trả
          chunks/citations trước khi phát sự kiện lỗi để bạn kiểm chứng.
        </Alert>
      ) : null}

      <Card className="overflow-hidden">
        <div className="grid gap-2 border-b bg-muted/30 px-4 py-3 sm:grid-cols-2 lg:grid-cols-[170px_120px_150px_190px_auto] lg:items-end">
          <div className="space-y-1">
            <Label htmlFor="chat-mode" className="text-[10px] uppercase tracking-wide">
              Chế độ suy luận
            </Label>
            <Select
              id="chat-mode"
              className="h-8 text-xs"
              value={mode}
              onChange={(event) => setMode(event.target.value as ChatMode)}
              disabled={isStreaming}
            >
              <option value="hybrid">Hybrid (khuyến nghị)</option>
              <option value="retrieval_only">Chỉ truy xuất</option>
              <option value="real">Real LLM (bắt buộc)</option>
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="chat-top-k" className="text-[10px] uppercase tracking-wide">
              Top-K
            </Label>
            <Select
              id="chat-top-k"
              className="h-8 text-xs"
              value={topK}
              onChange={(event) => setTopK(event.target.value)}
              disabled={isStreaming}
            >
              {TOP_K_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value} chunks
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="chat-context" className="text-[10px] uppercase tracking-wide">
              Trần ngữ cảnh
            </Label>
            <Select
              id="chat-context"
              className="h-8 text-xs"
              value={contextTokens}
              onChange={(event) => setContextTokens(event.target.value)}
              disabled={isStreaming}
            >
              {CONTEXT_TOKEN_OPTIONS.map((value) => (
                <option key={value} value={value}>
                  {value} tokens
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="chat-department" className="text-[10px] uppercase tracking-wide">
              Phòng ban
            </Label>
            <Select
              id="chat-department"
              className="h-8 text-xs"
              value={departmentId}
              onChange={(event) => setDepartmentId(event.target.value)}
              disabled={isStreaming}
            >
              <option value="">Toàn bộ phạm vi được phép</option>
              {selectableDepartments.map((department) => (
                <option key={department.id} value={department.id}>
                  {department.name}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex items-center justify-start lg:justify-end">{providerBadge}</div>
        </div>

        <div
          ref={scrollRef}
          className="h-[54vh] min-h-[400px] space-y-5 overflow-y-auto px-4 py-5 lg:px-5"
          aria-live="polite"
        >
          {historyLoading && entries.length === 0 ? (
            <div className="flex h-full min-h-[240px] items-center justify-center gap-2 text-xs text-muted-foreground">
              <Spinner /> Đang tải lịch sử hội thoại…
            </div>
          ) : entries.length === 0 ? (
            <EmptyState
              icon={MessageSquareText}
              title="Bắt đầu cuộc trò chuyện với kho tri thức"
              description="Câu trả lời chỉ dựa trên tài liệu trong phạm vi được cấp quyền. Nếu ngữ cảnh không đủ, trợ lý sẽ từ chối suy đoán thay vì bịa câu trả lời."
              action={
                <div className="flex max-w-xl flex-wrap items-center justify-center gap-2">
                  {EXAMPLE_QUESTIONS.map((question) => (
                    <Button
                      key={question}
                      variant="outline"
                      size="sm"
                      onClick={() => void sendQuery(question)}
                    >
                      {question}
                    </Button>
                  ))}
                </div>
              }
            />
          ) : (
            entries.map((entry) =>
              entry.role === "user" ? (
                <div key={entry.id} className="flex justify-end">
                  <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-indigo-600 px-4 py-2.5 text-sm leading-relaxed text-white shadow-sm">
                    {entry.content}
                  </div>
                </div>
              ) : (
                <div key={entry.id} className="flex gap-2.5 sm:gap-3">
                  <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-950 text-indigo-300">
                    <Sparkles className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1 space-y-2">
                    {entry.chunks && entry.chunks.length > 0 ? (
                      <div className="overflow-hidden rounded-lg border bg-muted/20">
                        <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
                          <p className="flex items-center gap-1.5 text-[11px] font-medium">
                            <Layers className="h-3.5 w-3.5 text-indigo-500" />
                            Ngữ cảnh đã truy xuất ({entry.chunks.length} chunks)
                          </p>
                          <CompressionSummary stats={entry.compression} done={entry.done} />
                        </div>
                        <ol className="max-h-44 divide-y overflow-y-auto">
                          {entry.chunks.map((chunk, index) => (
                            <li key={chunk.child_chunk_id}>
                              <button
                                type="button"
                                className="flex w-full items-start gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/50"
                                onClick={() =>
                                  setTarget({
                                    documentId: chunk.document_id,
                                    childChunkId: chunk.child_chunk_id,
                                    heading: chunk.heading,
                                  })
                                }
                              >
                                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[9px] font-semibold text-indigo-600">
                                  {index + 1}
                                </span>
                                <span className="min-w-0 flex-1">
                                  <span className="block truncate text-[11px] font-medium">
                                    {chunk.document_title}
                                    {chunk.heading ? ` — ${chunk.heading}` : ""}
                                  </span>
                                  <span className="block truncate text-[10px] text-muted-foreground">
                                    {chunk.child_content}
                                  </span>
                                </span>
                                <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                                  {chunk.rrf_score.toFixed(3)}
                                </span>
                              </button>
                            </li>
                          ))}
                        </ol>
                      </div>
                    ) : null}

                    <div className="rounded-xl border bg-card px-4 py-3 text-sm shadow-sm">
                      {entry.content ? (
                        <MarkdownContent content={entry.content} />
                      ) : entry.status === "streaming" ? (
                        <StreamingDots
                          label={
                            entry.chunks
                              ? "Đã có ngữ cảnh, đang chờ LLM sinh token đầu tiên…"
                              : "Đang truy xuất FAISS + FTS và lọc ngữ cảnh 3 tầng…"
                          }
                        />
                      ) : entry.status === "done" && entry.mode === "retrieval_only" ? (
                        <p className="text-xs leading-relaxed text-muted-foreground">
                          Chế độ chỉ truy xuất không gọi mô hình sinh. Hãy đọc các phân đoạn ngữ cảnh phía trên
                          hoặc mở tài liệu gốc để kiểm chứng.
                        </p>
                      ) : entry.status === "done" ? (
                        <p className="text-xs text-muted-foreground">Mô hình không trả về nội dung.</p>
                      ) : null}

                      {entry.status === "streaming" && entry.content ? (
                        <div className="mt-2 flex items-center gap-2 text-[10px] text-muted-foreground">
                          <Spinner className="h-3 w-3" /> Đang sinh câu trả lời…
                        </div>
                      ) : null}

                      {entry.error ? (
                        <Alert
                          variant="destructive"
                          title="Không thể hoàn tất câu trả lời"
                          className="mt-3"
                        >
                          {entry.error}
                        </Alert>
                      ) : null}

                      {entry.status === "aborted" ? (
                        <Alert variant="info" title="Đã dừng phản hồi" className="mt-3">
                          Bạn đã dừng luồng stream. Phần nội dung đã nhận vẫn được giữ nguyên.
                        </Alert>
                      ) : null}
                    </div>

                    {entry.citations && entry.citations.length > 0 ? (
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                          Trích dẫn
                        </span>
                        {entry.citations.map((citation, index) => (
                          <button
                            key={`${citation.document_id}-${citation.child_chunk_id ?? citation.heading ?? index}`}
                            type="button"
                            title={`Mở ${citation.document_title}${citation.heading ? ` — ${citation.heading}` : ""}`}
                            onClick={() =>
                              setTarget({
                                documentId: citation.document_id,
                                childChunkId: citation.child_chunk_id ?? null,
                                heading: citation.heading,
                              })
                            }
                            className="inline-flex max-w-[300px] items-center gap-1.5 rounded-full border bg-card px-2 py-1 text-[10px] text-muted-foreground transition-colors hover:border-indigo-300 hover:bg-indigo-50 hover:text-foreground"
                          >
                            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[9px] font-semibold text-indigo-600">
                              {index + 1}
                            </span>
                            <span className="truncate">
                              {citation.document_title}
                              {citation.heading ? ` — ${citation.heading}` : ""}
                            </span>
                            <span className="shrink-0 font-mono">{citation.score.toFixed(3)}</span>
                          </button>
                        ))}
                      </div>
                    ) : null}

                    {entry.done?.attachment_sources && entry.done.attachment_sources.length > 0 ? (
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                          Nguồn đính kèm
                        </span>
                        {entry.done.attachment_sources.map((source) => (
                          <span
                            key={`${source.attachment_id}-${source.chunk_id}`}
                            className="inline-flex max-w-[300px] items-center gap-1.5 rounded-full border border-dashed bg-card px-2 py-1 text-[10px] text-muted-foreground"
                            title="Tài liệu riêng của phiên chat; người khác không truy cập được."
                          >
                            <Paperclip className="h-3 w-3 shrink-0 text-indigo-500" />
                            <span className="truncate">{source.file_name}</span>
                            <span className="shrink-0 font-mono">{source.score.toFixed(3)}</span>
                          </span>
                        ))}
                      </div>
                    ) : null}

                    <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted-foreground">
                      <Badge variant="outline">{MODE_LABELS[entry.mode ?? "hybrid"]}</Badge>
                      {typeof entry.firstTokenMs === "number" ? (
                        <span className="inline-flex items-center gap-1">
                          <Timer className="h-3 w-3" /> Token đầu {formatNumber(entry.firstTokenMs)} ms
                        </span>
                      ) : null}
                      {entry.done ? (
                        <span>≈{formatNumber(entry.done.output_tokens)} output tokens</span>
                      ) : null}
                      {entry.status === "error" ? (
                        <span className="inline-flex items-center gap-1 text-red-600">
                          <FileText className="h-3 w-3" /> Luồng bị gián đoạn
                        </span>
                      ) : null}
                      {entry.status === "done" && entry.content.trim() && entry.query ? (
                        <span className="ml-auto flex items-center gap-1">
                          <span className="hidden sm:inline">Hữu ích?</span>
                          <Button
                            variant={entry.feedback === "THUMBS_UP" ? "secondary" : "ghost"}
                            size="icon"
                            className="h-7 w-7"
                            aria-label="Đánh giá hữu ích"
                            aria-pressed={entry.feedback === "THUMBS_UP"}
                            disabled={feedbackMutation.isPending || Boolean(entry.feedback)}
                            onClick={() =>
                              feedbackMutation.mutate({
                                entryId: entry.id,
                                query: entry.query as string,
                                answer: entry.content,
                                rating: "THUMBS_UP",
                              })
                            }
                          >
                            <ThumbsUp
                              className={
                                entry.feedback === "THUMBS_UP"
                                  ? "h-3.5 w-3.5 text-emerald-600"
                                  : "h-3.5 w-3.5"
                              }
                            />
                          </Button>
                          <Button
                            variant={entry.feedback === "THUMBS_DOWN" ? "secondary" : "ghost"}
                            size="icon"
                            className="h-7 w-7"
                            aria-label="Đánh giá chưa hữu ích"
                            aria-pressed={entry.feedback === "THUMBS_DOWN"}
                            disabled={feedbackMutation.isPending || Boolean(entry.feedback)}
                            onClick={() =>
                              feedbackMutation.mutate({
                                entryId: entry.id,
                                query: entry.query as string,
                                answer: entry.content,
                                rating: "THUMBS_DOWN",
                              })
                            }
                          >
                            <ThumbsDown
                              className={
                                entry.feedback === "THUMBS_DOWN"
                                  ? "h-3.5 w-3.5 text-red-600"
                                  : "h-3.5 w-3.5"
                              }
                            />
                          </Button>
                        </span>
                      ) : null}
                    </div>
                  </div>
                </div>
              ),
            )
          )}
        </div>

        <div className="border-t bg-card p-3">
          {cooldownSeconds > 0 ? (
            <Alert variant="warning" title="Tạm thời chạm hạn mức" className="mb-2">
              Tenant đã dùng hết 30 RPM / 16K TPM. Vui lòng chờ{" "}
              <span className="font-mono font-semibold">{cooldownSeconds}s</span> rồi gửi lại.
            </Alert>
          ) : null}
          {attachments.length > 0 ? (
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Đính kèm riêng
              </span>
              {attachments.map((attachment) => (
                <span
                  key={attachment.id}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-full border bg-muted/40 px-2.5 py-1 text-[10px]"
                  title={`Chỉ bạn thấy file này. Tự hết hạn lúc ${new Date(attachment.expires_at).toLocaleString()}.`}
                >
                  <Paperclip className="h-3 w-3 shrink-0 text-indigo-500" />
                  <span className="max-w-[220px] truncate font-medium">{attachment.file_name}</span>
                  <span className="shrink-0 text-muted-foreground">{attachment.chunk_count} đoạn</span>
                  <button
                    type="button"
                    className="rounded-full p-0.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                    aria-label={`Xoá ${attachment.file_name}`}
                    disabled={attachmentUploading}
                    onClick={() => void removeAttachment(attachment.id)}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          ) : null}
          <input
            ref={attachmentInputRef}
            type="file"
            className="hidden"
            accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.md,.csv"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void uploadAttachment(file);
            }}
          />
          <div className="relative">
            <Textarea
              rows={2}
              className="min-h-[76px] resize-none pb-9 pr-36 text-sm"
              placeholder="Đặt câu hỏi về tài liệu doanh nghiệp (2–2000 ký tự)…"
              value={input}
              maxLength={2000}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleComposerKeyDown}
            />
            <div className="absolute bottom-2 right-2 flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7"
                title="Đính kèm tài liệu riêng cho phiên chat (chỉ bạn thấy, tự hết hạn)"
                aria-label="Đính kèm tài liệu"
                disabled={isStreaming || attachmentUploading}
                onClick={() => attachmentInputRef.current?.click()}
              >
                {attachmentUploading ? (
                  <Spinner className="h-3.5 w-3.5" />
                ) : (
                  <Paperclip className="h-3.5 w-3.5" />
                )}
              </Button>
              <span className="text-[10px] tabular-nums text-muted-foreground">{input.length}/2000</span>
              {isStreaming ? (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => abortRef.current?.abort()}
                >
                  <CircleStop /> Dừng
                </Button>
              ) : (
                <Button size="sm" onClick={() => void sendQuery(input)} disabled={sendDisabled}>
                  <Send /> Gửi
                </Button>
              )}
            </div>
          </div>
          <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
            Enter để gửi · Shift+Enter xuống dòng. Mỗi câu trả lời đều chịu kiểm soát hạn mức Tenant và chỉ dùng
            tài liệu trong phạm vi ACL. File đính kèm chỉ thuộc phiên của bạn, không vào kho tri thức chung
            và tự hết hạn.
          </p>
        </div>
      </Card>

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
