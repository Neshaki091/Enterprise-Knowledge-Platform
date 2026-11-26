export const ROLE_LABELS: Record<string, string> = {
  OWNER: "Chủ sở hữu",
  ADMIN: "Quản trị viên",
  EMPLOYEE: "Nhân viên",
  COMPLIANCE_OFFICER: "Kiểm soát tuân thủ",
  SUPER_ADMIN: "Quản trị nền tảng",
};

/** Nhãn tiếng Việt cho vai trò RBAC, giữ nguyên mã gốc nếu chưa được ánh xạ. */
export function roleLabel(role: string | null | undefined): string {
  if (!role) return "—";
  return ROLE_LABELS[role.toUpperCase()] ?? role;
}

export const EVENT_TYPE_LABELS: Record<string, string> = {
  chat_query: "Hỏi đáp RAG",
  chat_stream: "Chat streaming",
  search_query: "Tìm kiếm lai",
  document_upload: "Tải tài liệu",
  document_delete: "Xóa tài liệu",
  rate_limit_blocked: "Chặn hạn mức",
  llm_upstream_error: "Lỗi upstream LLM",
  login: "Đăng nhập",
  login_failed: "Đăng nhập thất bại",
  tenant_switch: "Chuyển workspace",
};

/** Nhãn tiếng Việt cho loại sự kiện telemetry. */
export function eventTypeLabel(eventType: string | null | undefined): string {
  if (!eventType) return "—";
  return EVENT_TYPE_LABELS[eventType] ?? eventType;
}

export const DOC_STATUS_LABELS: Record<string, string> = {
  PROCESSED: "Đã xử lý",
  PROCESSING: "Đang xử lý",
  FAILED: "Thất bại",
};

/** Nhãn trạng thái tài liệu. */
export function documentStatusLabel(status: string | null | undefined): string {
  if (!status) return "—";
  return DOC_STATUS_LABELS[status.toUpperCase()] ?? status;
}
