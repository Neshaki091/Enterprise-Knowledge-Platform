/**
 * HTTP Client trung tâm:
 * - Gắn Bearer token + X-Tenant-ID vào mọi request nghiệp vụ.
 * - Single-Flight Refresh Queue: nhiều request 401 song song chỉ kích hoạt đúng 1 lần /auth/refresh.
 * - Refresh thất bại (reuse/revoked) → xóa phiên và phát sự kiện để Router đẩy về /login.
 * - Bộ phân tích SSE dùng chung cho POST /chat/stream và các luồng GET realtime.
 */

import type {
  ChatCitation,
  ChatQueryRequest,
  ChatStreamDone,
  CompressionStats,
  SearchResultItem,
  TokenResponse,
} from "./types";
import { readStoredSession, writeStoredSession, type StoredSession } from "./session";

const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? "";

export const AUTH_EXPIRED_EVENT = "kh:auth-expired";
export const SESSION_UPDATED_EVENT = "kh:session-updated";

export class ApiError extends Error {
  readonly status: number;
  readonly retryAfter?: number;

  constructor(status: number, message: string, retryAfter?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/** Chuyển lỗi bất kỳ thành thông điệp hiển thị cho người dùng, ưu tiên detail từ backend. */
export function describeApiError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

let currentSession: StoredSession | null = readStoredSession();

export function getSession(): StoredSession | null {
  return currentSession;
}

/** Cập nhật phiên bộ nhớ + localStorage và thông báo cho các thành phần lắng nghe. */
export function setSession(session: StoredSession | null): void {
  currentSession = session;
  writeStoredSession(session);
  window.dispatchEvent(new CustomEvent(SESSION_UPDATED_EVENT));
}

function expireSession(): void {
  currentSession = null;
  writeStoredSession(null);
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function toApiError(response: Response): Promise<ApiError> {
  let detail = `Yêu cầu thất bại (HTTP ${response.status})`;
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && "detail" in body) {
      const rawDetail = body.detail;
      if (typeof rawDetail === "string") {
        detail = rawDetail;
      } else if (Array.isArray(rawDetail)) {
        const messages = rawDetail.map((item) => {
          if (isRecord(item) && typeof item.msg === "string") return item.msg;
          return "Dữ liệu không hợp lệ";
        });
        if (messages.length > 0) detail = messages.join("; ");
      }
    }
  } catch {
    // Body không phải JSON (vd. lỗi HTML từ proxy): giữ thông điệp mặc định kèm mã trạng thái.
  }
  const retryAfterHeader = response.headers.get("Retry-After");
  const retryAfter = retryAfterHeader ? Number(retryAfterHeader) : undefined;
  return new ApiError(response.status, detail, retryAfter !== undefined && Number.isFinite(retryAfter) ? retryAfter : undefined);
}

let refreshPromise: Promise<StoredSession> | null = null;

async function performRefresh(): Promise<StoredSession> {
  const session = currentSession;
  if (!session) throw new ApiError(401, "Không có phiên đăng nhập để làm mới");

  const usedRefreshToken = session.refreshToken;
  const response = await fetch(`${API_BASE}/api/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ refresh_token: usedRefreshToken }),
  });
  if (!response.ok) {
    expireSession();
    throw await toApiError(response);
  }
  const tokens = (await response.json()) as TokenResponse;
  const next: StoredSession = {
    accessToken: tokens.access_token,
    refreshToken: tokens.refresh_token,
    tenantId: tokens.tenant_id,
    role: tokens.role,
    tokenType: tokens.token_type,
  };
  // Không ghi đè nếu người dùng vừa chuyển tenant/đăng xuất trong lúc refresh đang bay.
  if (currentSession?.refreshToken === usedRefreshToken) {
    setSession(next);
  }
  return next;
}

/** Gọi refresh dùng chung: các request đồng thời sẽ chờ đúng một Promise duy nhất. */
export function refreshSession(): Promise<StoredSession> {
  if (!refreshPromise) {
    refreshPromise = performRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

interface AuthFetchOptions {
  method?: string;
  body?: BodyInit;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  auth?: boolean;
}

async function fetchWithAuth(path: string, options: AuthFetchOptions = {}, allowRefresh = true): Promise<Response> {
  const url = `${API_BASE}${path}`;
  const buildInit = (): RequestInit => {
    const headers: Record<string, string> = { ...(options.headers ?? {}) };
    if (options.auth !== false && currentSession) {
      headers.Authorization = `Bearer ${currentSession.accessToken}`;
      headers["X-Tenant-ID"] = currentSession.tenantId;
    }
    return { method: options.method ?? "GET", headers, body: options.body, signal: options.signal };
  };

  let response = await fetch(url, buildInit());
  if (response.status === 401 && allowRefresh && options.auth !== false && currentSession) {
    try {
      await refreshSession();
    } catch {
      throw new ApiError(401, "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.");
    }
    response = await fetch(url, buildInit());
  }
  return response;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  formData?: FormData;
  headers?: Record<string, string>;
  auth?: boolean;
  signal?: AbortSignal;
}

/** Gửi request JSON/FormData có xác thực và trả về DTO đã định kiểu. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(options.headers ?? {}) };
  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetchWithAuth(path, {
    method: options.method ?? "GET",
    body,
    headers,
    auth: options.auth,
    signal: options.signal,
  });
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

type SseCallback = (payload: unknown) => void;

async function consumeSse(response: Response, onEvent: SseCallback, signal?: AbortSignal): Promise<void> {
  if (!response.body) {
    throw new ApiError(500, "Trình duyệt không hỗ trợ đọc luồng dữ liệu (ReadableStream)");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const handleBlock = (block: string): void => {
    const dataLines = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart());
    if (dataLines.length === 0) return;
    const jsonText = dataLines.join("\n").trim();
    if (!jsonText) return;
    try {
      onEvent(JSON.parse(jsonText) as unknown);
    } catch {
      onEvent({ type: "raw", content: jsonText });
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (signal?.aborted) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        handleBlock(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
    }
    const tail = `${buffer}${decoder.decode()}`.trim();
    if (tail) handleBlock(tail);
  } finally {
    reader.releaseLock();
  }
}

export interface ChatStreamCallbacks {
  onChunks?: (chunks: SearchResultItem[], stats: CompressionStats) => void;
  onCitations?: (citations: ChatCitation[]) => void;
  onToken?: (token: string) => void;
  onDone?: (done: ChatStreamDone) => void;
  onError?: (message: string) => void;
  onRaw?: (payload: unknown) => void;
}

/** Đọc luồng POST /api/v1/chat/stream bằng fetch + ReadableStream (EventSource không gửi được header). */
export async function streamChat(
  payload: ChatQueryRequest,
  callbacks: ChatStreamCallbacks,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetchWithAuth("/api/v1/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!response.ok) throw await toApiError(response);

  await consumeSse(
    response,
    (raw) => {
      if (!isRecord(raw)) {
        callbacks.onRaw?.(raw);
        return;
      }
      const type = typeof raw.type === "string" ? raw.type : "";
      if (type === "chunks" && Array.isArray(raw.data)) {
        const stats = isRecord(raw.compression_stats) ? (raw.compression_stats as CompressionStats) : {};
        callbacks.onChunks?.(raw.data as SearchResultItem[], stats);
      } else if (type === "citations" && Array.isArray(raw.data)) {
        callbacks.onCitations?.(raw.data as ChatCitation[]);
      } else if (type === "token" && typeof raw.content === "string") {
        callbacks.onToken?.(raw.content);
      } else if (type === "done") {
        callbacks.onDone?.(raw as unknown as ChatStreamDone);
      } else if (type === "error") {
        callbacks.onError?.(
          typeof raw.message === "string" ? raw.message : "Luồng trả lời gặp lỗi không xác định",
        );
      } else {
        callbacks.onRaw?.(raw);
      }
    },
    signal,
  );
}

/** Mở luồng SSE GET (dashboards realtime) có Bearer token và hỗ trợ abort. */
export async function openSseStream(
  path: string,
  onEvent: (payload: unknown) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetchWithAuth(path, {
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.ok) throw await toApiError(response);
  await consumeSse(response, onEvent, signal);
}

/** Tải file có xác thực (ndjson/JSON/CSV) và kích hoạt download của trình duyệt. */
export async function downloadFile(path: string, fallbackName = "export.jsonl.gz"): Promise<void> {
  const response = await fetchWithAuth(path, {
    headers: { Accept: "application/gzip" },
  });
  if (!response.ok) throw await toApiError(response);

  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  const filename = match?.[1] ? decodeURIComponent(match[1]) : fallbackName;

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
