"""
MÔ TẢ KIẾN TRÚC:
Router Endpoints cung cấp dữ liệu cho Dashboards 3 cấp, Analytics thời gian thực (Snapshot + SSE),
Thống kê lưu trữ DB vs FAISS mmap theo từng Tenant, Giám sát hạn mức API Key/Model và Audit Logs (SEC-026).
"""

import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.context import get_current_tenant_context, TenantContext
from app.core.access import allowed_department_ids
from app.schemas.dashboard import (
    PlatformAdminDashboardResponse,
    BusinessAdminDashboardResponse,
    EmployeeWorkspaceDashboardResponse,
    PlatformAnalyticsResponse,
    TenantAnalyticsResponse,
    TelemetryLogsResponse,
)
from app.services.dashboard_service import DashboardService

router = APIRouter()


@router.get(
    "/platform",
    response_model=PlatformAdminDashboardResponse,
    summary="Dashboard cấp 1: Dành cho Quản trị viên Nền tảng (Super-Admin)"
)
async def get_platform_dashboard(
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Chỉ cho phép Super-Admin hệ thống truy cập (SEC-026)."""
    if not context.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên Nền tảng (Platform Super-Admin)",
        )
    return await DashboardService.get_platform_admin_dashboard(db)


@router.get(
    "/platform/analytics",
    response_model=PlatformAnalyticsResponse,
    summary="Analytics cấp Platform: Lưu trữ DB/FAISS từng Tenant, Limit API Key/Model & Realtime Metrics"
)
async def get_platform_analytics(
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Chỉ Super-Admin xem được toàn cảnh DB, FAISS theo từng Tenant, hạn mức 30 RPM/16K TPM và metrics realtime."""
    if not context.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên Nền tảng (Platform Super-Admin)",
        )
    return await DashboardService.get_platform_analytics(db)


@router.get(
    "/platform/logs",
    response_model=TelemetryLogsResponse,
    summary="Nhật ký vận hành & lưu trữ toàn nền tảng (Sanitized SEC-026)"
)
async def get_platform_logs(
    tenant_id: Optional[uuid.UUID] = Query(None, description="Lọc theo Tenant ID"),
    event_type: Optional[str] = Query(None, description="Lọc theo loại sự kiện (chat_query, document_upload, ...)"),
    limit: int = Query(50, ge=1, le=200),
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Truy vấn audit logs hệ thống (không chứa nội dung văn bản thô của khách hàng)."""
    if not context.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên Nền tảng (Platform Super-Admin)",
        )
    return await DashboardService.get_audit_logs(db, tenant_id=tenant_id, event_type=event_type, limit=limit)


@router.get(
    "/platform/realtime",
    summary="Luồng SSE Realtime Analytics cho Platform Super-Admin"
)
async def stream_platform_realtime(
    interval_sec: float = Query(2.0, ge=0.1, le=30.0),
    max_events: int = Query(0, ge=0, le=500),
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Truyền luồng SSE cập nhật tức thì QPS/RPM, độ trễ, dung lượng DB/FAISS và quota API Key."""
    if not context.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên Nền tảng (Platform Super-Admin)",
        )
    return StreamingResponse(
        DashboardService.stream_realtime_analytics(
            db,
            tenant_id=None,
            interval_sec=interval_sec,
            max_events=max_events,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/tenant",
    response_model=BusinessAdminDashboardResponse,
    summary="Dashboard cấp 2: Dành cho Quản trị viên Doanh nghiệp (Tenant Admin/Owner)"
)
async def get_tenant_dashboard(
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Chỉ cho phép Quản trị viên nội bộ của Tenant (Admin hoặc Owner) truy cập."""
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên Doanh nghiệp (Tenant Admin)",
        )
    return await DashboardService.get_business_admin_dashboard(db, context.tenant_id)


@router.get(
    "/tenant/analytics",
    response_model=TenantAnalyticsResponse,
    summary="Analytics cấp Tenant: Dung lượng DB/FAISS, Live RPM/TPM & Latency"
)
async def get_tenant_analytics(
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Trả về dung lượng DB vs FAISS, hạn mức token/phút còn lại và thống kê độ trễ riêng của Tenant."""
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên Doanh nghiệp (Tenant Admin)",
        )
    return await DashboardService.get_tenant_analytics(db, context.tenant_id)


@router.get(
    "/tenant/logs",
    response_model=TelemetryLogsResponse,
    summary="Nhật ký vận hành & lưu trữ của riêng Tenant"
)
async def get_tenant_logs(
    event_type: Optional[str] = Query(None, description="Lọc theo loại sự kiện"),
    limit: int = Query(50, ge=1, le=200),
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Chỉ cho phép Tenant Admin/Owner xem logs thuộc về Tenant hiện tại (chống BOLA)."""
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên Doanh nghiệp (Tenant Admin)",
        )
    return await DashboardService.get_audit_logs(db, tenant_id=context.tenant_id, event_type=event_type, limit=limit)


@router.get(
    "/tenant/realtime",
    summary="Luồng SSE Realtime Analytics cho Quản trị viên Doanh nghiệp"
)
async def stream_tenant_realtime(
    interval_sec: float = Query(2.0, ge=0.1, le=30.0),
    max_events: int = Query(0, ge=0, le=500),
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Truyền luồng SSE cập nhật tức thì lưu trữ, RPM/TPM và truy vấn riêng của Tenant."""
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên Doanh nghiệp (Tenant Admin)",
        )
    return StreamingResponse(
        DashboardService.stream_realtime_analytics(
            db,
            tenant_id=context.tenant_id,
            interval_sec=interval_sec,
            max_events=max_events,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/workspace",
    response_model=EmployeeWorkspaceDashboardResponse,
    summary="Dashboard cấp 3: Không gian làm việc cá nhân của Nhân viên (Employee Workspace)"
)
async def get_employee_workspace(
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Bất kỳ nhân viên nào có phiên làm việc hợp lệ trong Tenant đều có thể truy cập."""
    return await DashboardService.get_employee_workspace_dashboard(
        db,
        context.user_id,
        context.tenant_id,
        allowed_department_ids=allowed_department_ids(context),
    )
