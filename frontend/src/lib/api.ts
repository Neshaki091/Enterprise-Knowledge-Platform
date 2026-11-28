/** Các hàm gọi API nghiệp vụ đã định kiểu, dùng chung toàn ứng dụng. */

import { downloadFile, request } from "./api-client";
import type {
  BusinessAdminDashboard,
  ChatFeedbackCreate,
  ChatFeedbackResponse,
  ChatHistoryResponse,
  ChatQueryRequest,
  ChatQueryResponse,
  ChatRollbackResponse,
  DepartmentResponse,
  DocumentDetailResponse,
  DocumentResponse,
  EmployeeWorkspaceDashboard,
  ExternalDBConnectorCreate,
  ExternalDBConnectorResponse,
  ExternalDBTokenRequest,
  ExternalDBTokenResponse,
  HealthResponse,
  McpJsonRpcResponse,
  MemberResponse,
  MembershipSummary,
  PlatformAdminDashboard,
  PlatformAnalytics,
  SearchResponse,
  TelemetryLogsResponse,
  TenantAnalytics,
  TenantContextResponse,
  TenantResponse,
  TokenResponse,
  UserResponse,
} from "./types";

export interface RegisterPayload {
  email: string;
  password: string;
  full_name: string;
  tenant_name: string;
  tenant_slug?: string;
}

export interface DocumentListParams {
  skip?: number;
  limit?: number;
  department_id?: string;
}

function withQuery(path: string, params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const queryString = search.toString();
  return queryString ? `${path}?${queryString}` : path;
}

export const authApi = {
  me: (): Promise<TenantContextResponse> => request<TenantContextResponse>("/api/v1/auth/me"),
  login: (email: string, password: string): Promise<TokenResponse> =>
    request<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
      auth: false,
    }),
  register: (payload: RegisterPayload): Promise<UserResponse> =>
    request<UserResponse>("/api/v1/auth/register", {
      method: "POST",
      body: payload,
      auth: false,
    }),
  logout: (refreshToken: string | null): Promise<{ message: string }> =>
    request<{ message: string }>("/api/v1/auth/logout", {
      method: "POST",
      body: refreshToken ? { refresh_token: refreshToken } : {},
    }),
  switchTenant: (tenantId: string): Promise<TokenResponse> =>
    request<TokenResponse>("/api/v1/auth/switch-tenant", {
      method: "POST",
      body: { tenant_id: tenantId },
    }),
  memberships: (): Promise<MembershipSummary[]> =>
    request<MembershipSummary[]>("/api/v1/auth/memberships"),
};

export const tenantApi = {
  list: (skip = 0, limit = 100): Promise<TenantResponse[]> =>
    request<TenantResponse[]>(withQuery("/api/v1/tenants", { skip, limit })),
  create: (payload: { name: string; slug: string; quota_limits?: Record<string, unknown> }): Promise<TenantResponse> =>
    request<TenantResponse>("/api/v1/tenants", { method: "POST", body: payload }),
  update: (
    tenantId: string,
    payload: { name?: string; is_active?: boolean; quota_limits?: Record<string, unknown> },
  ): Promise<TenantResponse> =>
    request<TenantResponse>(`/api/v1/tenants/${tenantId}`, { method: "PATCH", body: payload }),
  suspend: (tenantId: string): Promise<TenantResponse> =>
    request<TenantResponse>(`/api/v1/tenants/${tenantId}/suspend`, { method: "POST" }),
  activate: (tenantId: string): Promise<TenantResponse> =>
    request<TenantResponse>(`/api/v1/tenants/${tenantId}/activate`, { method: "POST" }),
};

export const departmentApi = {
  list: (): Promise<DepartmentResponse[]> => request<DepartmentResponse[]>("/api/v1/departments"),
  create: (payload: { name: string; code: string }): Promise<DepartmentResponse> =>
    request<DepartmentResponse>("/api/v1/departments", { method: "POST", body: payload }),
};

export const memberApi = {
  list: (): Promise<MemberResponse[]> => request<MemberResponse[]>("/api/v1/tenants/members"),
  add: (payload: {
    email: string;
    password: string;
    full_name: string;
    role: string;
    department_id?: string | null;
  }): Promise<MemberResponse> =>
    request<MemberResponse>("/api/v1/tenants/members", { method: "POST", body: payload }),
};

export const documentApi = {
  list: (params: DocumentListParams = {}): Promise<DocumentResponse[]> =>
    request<DocumentResponse[]>(
      withQuery("/api/v1/documents", {
        skip: params.skip,
        limit: params.limit ?? 50,
        department_id: params.department_id,
      }),
    ),
  get: (documentId: string): Promise<DocumentDetailResponse> =>
    request<DocumentDetailResponse>(`/api/v1/documents/${documentId}`),
  delete: (documentId: string): Promise<{ message: string }> =>
    request<{ message: string }>(`/api/v1/documents/${documentId}`, { method: "DELETE" }),
  upload: (file: File, title?: string, departmentId?: string | null): Promise<DocumentResponse> => {
    const formData = new FormData();
    formData.append("file", file, file.name);
    if (title?.trim()) formData.append("title", title.trim());
    if (departmentId) formData.append("department_id", departmentId);
    return request<DocumentResponse>("/api/v1/documents/upload", {
      method: "POST",
      formData,
    });
  },
};

export const searchApi = {
  hybrid: (payload: {
    query: string;
    top_k?: number;
    department_id?: string | null;
    dense_weight?: number;
    sparse_weight?: number;
  }): Promise<SearchResponse> =>
    request<SearchResponse>("/api/v1/search/hybrid", { method: "POST", body: payload }),
};

export const chatApi = {
  query: (payload: ChatQueryRequest): Promise<ChatQueryResponse> =>
    request<ChatQueryResponse>("/api/v1/chat/query", { method: "POST", body: payload }),
  history: (sessionId?: string, limit = 50): Promise<ChatHistoryResponse> =>
    request<ChatHistoryResponse>(
      withQuery("/api/v1/chat/history", { session_id: sessionId, limit }),
    ),
  rollback: (sessionId: string): Promise<ChatRollbackResponse> =>
    request<ChatRollbackResponse>("/api/v1/chat/rollback", {
      method: "POST",
      body: { session_id: sessionId },
    }),
  feedback: (payload: ChatFeedbackCreate): Promise<ChatFeedbackResponse> =>
    request<ChatFeedbackResponse>("/api/v1/chat/feedback", { method: "POST", body: payload }),
  exportRagas: (month: string, includeMock = false): Promise<void> =>
    downloadFile(
      withQuery("/api/v1/chat/export", {
        month,
        include_mock: includeMock ? "true" : undefined,
      }),
      `ragas_${month}.jsonl.gz`,
    ),
};

export const dashboardApi = {
  workspace: (): Promise<EmployeeWorkspaceDashboard> =>
    request<EmployeeWorkspaceDashboard>("/api/v1/dashboards/workspace"),
  tenant: (): Promise<BusinessAdminDashboard> =>
    request<BusinessAdminDashboard>("/api/v1/dashboards/tenant"),
  tenantAnalytics: (): Promise<TenantAnalytics> =>
    request<TenantAnalytics>("/api/v1/dashboards/tenant/analytics"),
  tenantLogs: (eventType?: string, limit = 50): Promise<TelemetryLogsResponse> =>
    request<TelemetryLogsResponse>(
      withQuery("/api/v1/dashboards/tenant/logs", { event_type: eventType, limit }),
    ),
  platform: (): Promise<PlatformAdminDashboard> =>
    request<PlatformAdminDashboard>("/api/v1/dashboards/platform"),
  platformAnalytics: (): Promise<PlatformAnalytics> =>
    request<PlatformAnalytics>("/api/v1/dashboards/platform/analytics"),
  platformLogs: (tenantId?: string, eventType?: string, limit = 50): Promise<TelemetryLogsResponse> =>
    request<TelemetryLogsResponse>(
      withQuery("/api/v1/dashboards/platform/logs", { tenant_id: tenantId, event_type: eventType, limit }),
    ),
  tenantRealtimePath: (intervalSec = 2): string =>
    withQuery("/api/v1/dashboards/tenant/realtime", { interval_sec: intervalSec }),
  platformRealtimePath: (intervalSec = 2): string =>
    withQuery("/api/v1/dashboards/platform/realtime", { interval_sec: intervalSec }),
};

export const systemApi = {
  health: (): Promise<HealthResponse> => request<HealthResponse>("/api/v1/health", { auth: false }),
};

export const mcpApi = {
  listExternalDbs: (): Promise<ExternalDBConnectorResponse[]> =>
    request<ExternalDBConnectorResponse[]>("/api/v1/mcp/external-db"),
  createExternalDb: (payload: ExternalDBConnectorCreate): Promise<ExternalDBConnectorResponse> =>
    request<ExternalDBConnectorResponse>("/api/v1/mcp/external-db", { method: "POST", body: payload }),
  deleteExternalDb: (connectorId: string): Promise<{ deleted: boolean; connector_id: string }> =>
    request<{ deleted: boolean; connector_id: string }>(`/api/v1/mcp/external-db/${connectorId}`, {
      method: "DELETE",
    }),
  issueDbToken: (payload: ExternalDBTokenRequest): Promise<ExternalDBTokenResponse> =>
    request<ExternalDBTokenResponse>("/api/v1/mcp/external-db/token", { method: "POST", body: payload }),
  rpc: <T = Record<string, unknown>>(method: string, params: Record<string, unknown> = {}): Promise<McpJsonRpcResponse<T>> =>
    request<McpJsonRpcResponse<T>>("/api/v1/mcp", {
      method: "POST",
      body: { jsonrpc: "2.0", id: Date.now(), method, params },
    }),
};
