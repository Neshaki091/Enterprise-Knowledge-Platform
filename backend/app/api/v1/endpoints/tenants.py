"""
MÔ TẢ KIẾN TRÚC:
API Endpoints cho phân hệ Quản lý Tenant (SR-001).
Tầng Presentation / Controller:
- Nhận HTTP Request
- Validate DTO qua Pydantic
- Inject AsyncSession qua dependency `get_db`
- Gọi TenantService thực thi nghiệp vụ
- Trả về HTTP Status Codes và Response DTO chuẩn
"""

import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.context import get_current_tenant_context, require_superuser, TenantContext
from app.schemas.tenant import TenantCreate, TenantUpdate, TenantResponse
from app.schemas.department import MemberInviteRequest, MemberResponse
from app.services.tenant_service import TenantService
from app.services.department_service import DepartmentService

router = APIRouter(prefix="/tenants", tags=["Tenants Management"])


@router.post(
    "/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Thêm nhân viên / thành viên vào doanh nghiệp hiện tại"
)
async def add_tenant_member(
    payload: MemberInviteRequest,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Thêm thành viên vào Tenant của người dùng hiện tại (Yêu cầu quyền Admin/Owner)."""
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản trị viên (Admin/Owner) mới có quyền thêm nhân viên",
        )
    return await DepartmentService.add_member(db, context.tenant_id, payload)


@router.get(
    "/members",
    response_model=List[MemberResponse],
    summary="Lấy danh sách nhân viên thuộc doanh nghiệp hiện tại"
)
async def list_tenant_members(
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Liệt kê thành viên trong Tenant của người dùng hiện tại (Yêu cầu Admin/Owner)."""
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản trị viên (Admin/Owner) mới có quyền xem danh sách nhân viên",
        )
    return await DepartmentService.list_members(db, context.tenant_id)


@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo mới một Tenant (Platform Super-Admin)"
)
async def create_tenant(
    payload: TenantCreate,
    context: TenantContext = Depends(require_superuser),
    db: AsyncSession = Depends(get_db)
):
    """Tạo mới một doanh nghiệp (Tenant) trên nền tảng SaaS (Chỉ Platform Super-Admin)."""
    return await TenantService.create_tenant(db, payload)


@router.get(
    "",
    response_model=List[TenantResponse],
    summary="Lấy danh sách các Tenants (Platform Super-Admin)"
)
async def list_tenants(
    skip: int = Query(0, ge=0, description="Số bản ghi bỏ qua"),
    limit: int = Query(50, ge=1, le=100, description="Số bản ghi tối đa lấy về"),
    context: TenantContext = Depends(require_superuser),
    db: AsyncSession = Depends(get_db)
):
    """Lấy danh sách các doanh nghiệp trên hệ thống phục vụ Platform Super-Admin."""
    return await TenantService.list_tenants(db, skip=skip, limit=limit)


@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Lấy thông tin chi tiết một Tenant"
)
async def get_tenant(
    tenant_id: uuid.UUID,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Lấy thông tin chi tiết của một Tenant (Super-Admin hoặc thành viên thuộc Tenant đó)."""
    if not context.is_superuser and context.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Không có quyền xem thông tin Tenant khác (BOLA Blocked)",
        )
    tenant = await TenantService.get_tenant_by_id(db, tenant_id)
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Không tìm thấy Tenant với ID: {tenant_id}"
        )
    return tenant


@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Cập nhật thông tin Tenant"
)
async def update_tenant(
    tenant_id: uuid.UUID,
    payload: TenantUpdate,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Cập nhật tên hoặc hạn ngạch của Tenant (Chỉ Super-Admin hoặc Admin của chính Tenant đó)."""
    if not context.is_superuser and (context.tenant_id != tenant_id or not context.is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên hợp lệ",
        )
    if not context.is_superuser and payload.quota_limits is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Platform Super-Admin mới được phép chỉnh sửa quota_limits",
        )
    if not context.is_superuser and payload.is_active is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Platform Super-Admin mới được phép thay đổi trạng thái hoạt động của Tenant",
        )
    return await TenantService.update_tenant(db, tenant_id, payload)


@router.post(
    "/{tenant_id}/suspend",
    response_model=TenantResponse,
    summary="Tạm khóa (Suspend) Tenant (Platform Super-Admin)"
)
async def suspend_tenant(
    tenant_id: uuid.UUID,
    context: TenantContext = Depends(require_superuser),
    db: AsyncSession = Depends(get_db)
):
    """Tạm khóa hoạt động của Tenant (SR-001 - Chỉ Platform Super-Admin)."""
    return await TenantService.set_tenant_status(db, tenant_id, is_active=False)


@router.post(
    "/{tenant_id}/activate",
    response_model=TenantResponse,
    summary="Kích hoạt lại (Activate) Tenant (Platform Super-Admin)"
)
async def activate_tenant(
    tenant_id: uuid.UUID,
    context: TenantContext = Depends(require_superuser),
    db: AsyncSession = Depends(get_db)
):
    """Kích hoạt lại Tenant sau khi bị tạm khóa (Chỉ Platform Super-Admin)."""
    return await TenantService.set_tenant_status(db, tenant_id, is_active=True)
