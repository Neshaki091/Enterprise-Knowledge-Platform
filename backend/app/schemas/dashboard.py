"""
MÔ TẢ KIẾN TRÚC:
Pydantic Schemas phục vụ hệ thống Dashboards 3 cấp, Realtime Analytics,
Thống kê Lưu trữ DB/FAISS theo từng Tenant, Giám sát Hạn mức API Key/Model và Audit Logs (ADR-008, SEC-026).
"""

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, ConfigDict


class PlatformAdminDashboardResponse(BaseModel):
    """Tier 1: Dashboard cho Quản trị viên Nền tảng (Platform Super-Admin)."""
    total_tenants: int
    active_tenants: int
    total_users: int
    total_documents: int
    total_storage_bytes: int
    system_health: str
    security_notice: str = "Dữ liệu riêng tư và nội dung tài liệu của khách hàng được bảo vệ nghiêm ngặt (SEC-026)."


class BusinessAdminDashboardResponse(BaseModel):
    """Tier 2: Dashboard cho Quản trị viên Doanh nghiệp (Business Tenant Admin)."""
    tenant_name: str
    tenant_slug: str
    total_members: int
    departments_count: int
    total_documents: int
    total_storage_bytes: int
    quota_limits: Dict[str, Any]
    feedback_summary: Dict[str, int]


class EmployeeWorkspaceDashboardResponse(BaseModel):
    """Tier 3: Không gian làm việc của Nhân viên (Employee Workspace)."""
    user_name: str
    email: str
    tenant_name: str
    role: str
    department_name: Optional[str] = None
    accessible_documents_count: int


class TenantStorageBreakdown(BaseModel):
    """Chi tiết dung lượng lưu trữ tách bạch giữa PostgreSQL và FAISS mmap trên đĩa."""
    documents_count: int
    parent_chunks_count: int
    child_chunks_count: int
    uploaded_files_bytes: int
    db_raw_text_bytes: int
    db_chunks_text_bytes: int
    total_db_bytes: int
    faiss_index_exists: bool
    faiss_index_bytes: int
    faiss_vectors_count: int
    faiss_dimension: int


class LiveQuotaStatus(BaseModel):
    """Trạng thái Token Bucket thời gian thực (30 RPM / 16K TPM)."""
    rpm_capacity: int
    rpm_remaining: float
    rpm_used_percent: float
    tpm_capacity: int
    tpm_remaining: float
    tpm_used_percent: float
    total_requests: int
    total_blocked_rpm: int
    total_blocked_tpm: int
    total_tokens_consumed: int
    last_request_at: Optional[str] = None


class TenantAnalyticsItem(BaseModel):
    """Báo cáo tài nguyên, lưu trữ DB/FAISS và quota thời gian thực của từng Tenant (không lộ nội dung tài liệu)."""
    tenant_id: str
    tenant_name: str
    tenant_slug: str
    is_active: bool
    tier: str
    storage: TenantStorageBreakdown
    live_quota: LiveQuotaStatus
    total_logged_events: int


class TelemetryLogItem(BaseModel):
    """Một bản ghi nhật ký vận hành (không chứa nội dung văn bản nhạy cảm)."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    timestamp_utc: str
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    event_type: str
    model_name: Optional[str] = None
    mode: Optional[str] = None
    status_code: int
    latency_ms: float
    input_tokens: int
    compressed_tokens: int
    output_tokens: int
    storage_delta_bytes: int
    details: Dict[str, Any]


class TelemetryLogsResponse(BaseModel):
    """Danh sách nhật ký vận hành & lưu trữ có lọc."""
    total_returned: int
    logs: List[TelemetryLogItem]


class PlatformAnalyticsResponse(BaseModel):
    """Báo cáo Analytics thời gian thực cấp Platform Super-Admin (DB + FAISS + Model/API Key Limits + Per-Tenant)."""
    measured_at_utc: str
    system_health: str
    storage_summary: Dict[str, int]
    realtime_metrics: Dict[str, Any]
    model_api_limits: Dict[str, Any]
    tenants_breakdown: List[TenantAnalyticsItem]
    security_notice: str = "Chỉ hiển thị siêu dữ liệu lưu trữ & hạn mức kỹ thuật; cấm truy cập văn bản thô (SEC-026)."


class TenantAnalyticsResponse(BaseModel):
    """Báo cáo Analytics thời gian thực cấp Quản trị viên Doanh nghiệp."""
    measured_at_utc: str
    tenant_id: str
    tenant_name: str
    tenant_slug: str
    is_active: bool
    quota_limits: Dict[str, Any]
    storage: TenantStorageBreakdown
    live_quota: LiveQuotaStatus
    realtime_metrics: Dict[str, Any]
    model_api_limits: Dict[str, Any]
