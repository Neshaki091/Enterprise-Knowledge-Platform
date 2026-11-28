/**
 * Hợp đồng dữ liệu DTO đồng bộ 1-1 với Pydantic Schemas của Backend (API v1).
 * Mọi thời điểm backend đổi schema, tệp này là điểm cập nhật duy nhất của Frontend.
 */

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in_seconds: number;
  tenant_id: string;
  role: string;
}

export interface TenantContextResponse {
  user_id: string;
  tenant_id: string;
  role: string;
  department_id: string | null;
  email: string;
  is_superuser: boolean;
  permissions: string[];
}

export interface MembershipSummary {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  role: string;
  department_id: string | null;
  is_active: boolean;
}

export interface UserResponse {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_superuser: boolean;
  created_at: string;
}

export interface TenantResponse {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  quota_limits: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface DepartmentResponse {
  id: string;
  tenant_id: string;
  name: string;
  code: string;
  created_at: string;
  updated_at: string;
}

export interface MemberResponse {
  membership_id: string;
  user_id: string;
  tenant_id: string;
  email: string;
  full_name: string;
  role: string;
  department_id: string | null;
  is_active: boolean;
  created_at: string;
}

export interface DocumentResponse {
  id: string;
  tenant_id: string;
  department_id: string | null;
  title: string;
  file_name: string;
  file_type: string;
  file_size_bytes: number;
  file_hash: string;
  token_count: number;
  status: string;
  created_at: string;
  parent_chunk_count: number;
  child_chunk_count: number;
}

export interface ChildChunkResponse {
  id: string;
  parent_chunk_id: string;
  chunk_index: number;
  content: string;
  token_count: number;
  faiss_id: number | null;
}

export interface ParentChunkResponse {
  id: string;
  chunk_index: number;
  heading: string | null;
  content: string;
  token_count: number;
  child_chunks: ChildChunkResponse[];
}

export interface DocumentDetailResponse extends DocumentResponse {
  raw_text: string;
  doc_metadata: Record<string, unknown>;
  parent_chunks: ParentChunkResponse[];
}

export interface SearchResultItem {
  child_chunk_id: string;
  parent_chunk_id: string;
  document_id: string;
  document_title: string;
  heading: string | null;
  child_content: string;
  parent_content: string;
  rrf_score: number;
  dense_score: number | null;
  sparse_score: number | null;
}

export interface SearchResponse {
  query: string;
  total_found: number;
  results: SearchResultItem[];
}

export interface ChatCitation {
  document_id: string;
  document_title: string;
  heading: string | null;
  /** Có ở /chat/query và SSE citations sau bản cập nhật; dùng để mở đúng đoạn tài liệu. */
  child_chunk_id?: string;
  score: number;
}

export interface CompressionStats {
  original_tokens?: number;
  compressed_tokens?: number;
  savings_percent?: number;
  retained_chunks?: number;
  prompt_input_tokens?: number;
  [key: string]: unknown;
}

export interface ChatQueryResponse {
  answer: string;
  query: string;
  session_id: string;
  retrieved_chunks: SearchResultItem[];
  citations: ChatCitation[];
  tokens_used: number;
  compression_stats: CompressionStats;
  mode: string;
}

export interface ChatFeedbackCreate {
  query: string;
  answer: string;
  rating: "THUMBS_UP" | "THUMBS_DOWN";
  comment?: string;
}

export interface ChatFeedbackResponse {
  id: string;
  tenant_id: string;
  user_id: string;
  rating: string;
  created_at: string;
}

export type ChatMode = "hybrid" | "real" | "retrieval_only";

export interface ChatQueryRequest {
  query: string;
  top_k?: number;
  max_context_tokens?: number;
  department_id?: string | null;
  mode?: ChatMode;
  /** ID phiên do client giữ để nạp lại lịch sử và session memory. */
  session_id?: string;
}

export interface ChatStreamDone {
  type: "done";
  mode: string;
  tokens_saved_percent: number;
  retained_chunks: number;
  output_tokens: number;
  session_id?: string;
}

export interface ChatHistoryTurn {
  id: string;
  session_id: string;
  query: string;
  answer: string;
  contexts: Record<string, unknown>[];
  citations: ChatCitation[];
  chunk_ids: string[];
  model_name: string | null;
  mode: string | null;
  latency_ms: number;
  ttfb_ms: number | null;
  input_tokens: number;
  compressed_tokens: number;
  output_tokens: number;
  compression_savings_percent: number;
  feedback_rating: "THUMBS_UP" | "THUMBS_DOWN" | null;
  created_at: string;
}

export interface ChatHistoryResponse {
  total: number;
  turns: ChatHistoryTurn[];
}

export interface ChatRollbackResponse {
  deleted: boolean;
  turn_id: string | null;
}

export interface PlatformAdminDashboard {
  total_tenants: number;
  active_tenants: number;
  total_users: number;
  total_documents: number;
  total_storage_bytes: number;
  system_health: string;
  security_notice: string;
}

export interface BusinessAdminDashboard {
  tenant_name: string;
  tenant_slug: string;
  total_members: number;
  departments_count: number;
  total_documents: number;
  total_storage_bytes: number;
  quota_limits: Record<string, unknown>;
  feedback_summary: Record<string, number>;
}

export interface EmployeeWorkspaceDashboard {
  user_name: string;
  email: string;
  tenant_name: string;
  role: string;
  department_name: string | null;
  accessible_documents_count: number;
}

export interface TenantStorageBreakdown {
  documents_count: number;
  parent_chunks_count: number;
  child_chunks_count: number;
  uploaded_files_bytes: number;
  db_raw_text_bytes: number;
  db_chunks_text_bytes: number;
  total_db_bytes: number;
  faiss_index_exists: boolean;
  faiss_index_bytes: number;
  faiss_vectors_count: number;
  faiss_dimension: number;
}

export interface LiveQuotaStatus {
  rpm_capacity: number;
  rpm_remaining: number;
  rpm_used_percent: number;
  tpm_capacity: number;
  tpm_remaining: number;
  tpm_used_percent: number;
  total_requests: number;
  total_blocked_rpm: number;
  total_blocked_tpm: number;
  total_tokens_consumed: number;
  last_request_at: string | null;
}

export interface TenantAnalyticsItem {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  is_active: boolean;
  tier: string;
  storage: TenantStorageBreakdown;
  live_quota: LiveQuotaStatus;
  total_logged_events: number;
}

export interface TelemetryLogItem {
  id: string;
  timestamp_utc: string;
  tenant_id: string | null;
  user_id: string | null;
  event_type: string;
  model_name: string | null;
  mode: string | null;
  status_code: number;
  latency_ms: number;
  input_tokens: number;
  compressed_tokens: number;
  output_tokens: number;
  storage_delta_bytes: number;
  details: Record<string, unknown>;
}

export interface TelemetryLogsResponse {
  total_returned: number;
  logs: TelemetryLogItem[];
}

export interface PlatformAnalytics {
  measured_at_utc: string;
  system_health: string;
  storage_summary: Record<string, number>;
  realtime_metrics: Record<string, unknown>;
  model_api_limits: Record<string, unknown>;
  tenants_breakdown: TenantAnalyticsItem[];
  security_notice: string;
}

export interface TenantAnalytics {
  measured_at_utc: string;
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  is_active: boolean;
  quota_limits: Record<string, unknown>;
  storage: TenantStorageBreakdown;
  live_quota: LiveQuotaStatus;
  realtime_metrics: Record<string, unknown>;
  model_api_limits: Record<string, unknown>;
}

export interface HealthResponse {
  status: string;
  database: string;
  faiss_storage_ready: boolean;
  llm_provider: string;
  llm_real_configured: boolean;
  embedding_real_configured: boolean;
  timestamp_utc: string;
  service: string;
  version: string;
}

export type ExternalDbType = "postgresql" | "sqlite";

export interface ExternalDBConnectorCreate {
  name: string;
  db_type: ExternalDbType;
  connection_uri: string;
  tenant_secret: string;
  allowed_tables: string[];
}

export interface ExternalDBConnectorResponse {
  id: string;
  tenant_id: string;
  name: string;
  db_type: ExternalDbType | string;
  host_masked: string;
  allowed_tables: string[];
  is_active: boolean;
  created_at: string;
}

export interface ExternalDBTokenRequest {
  connector_id: string;
  tenant_secret: string;
  expires_minutes?: number;
}

export interface ExternalDBTokenResponse {
  db_jwt: string;
  token_type: string;
  connector_id: string;
  allowed_tables: string[];
  expires_in_seconds: number;
}

export interface McpToolDefinition {
  name: string;
  description: string;
  inputSchema?: Record<string, unknown>;
}

export interface McpJsonRpcResponse<T = Record<string, unknown>> {
  jsonrpc: string;
  id: string | number | null;
  result?: T;
  error?: {
    code: number;
    message: string;
    data?: unknown;
  };
}

export interface ExternalDBQueryResult {
  connector_id: string;
  connector_name: string;
  row_count: number;
  rows: Record<string, unknown>[];
}
