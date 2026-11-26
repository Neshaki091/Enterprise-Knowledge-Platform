"""
MÔ TẢ KIẾN TRÚC:
Router Endpoints quản lý Phòng ban (Departments) trong phạm vi từng doanh nghiệp (Tenant).
Mọi thao tác đều bị ràng buộc theo `context.tenant_id` (SEC-003, SEC-004).
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.context import get_current_tenant_context, TenantContext
from app.schemas.department import DepartmentCreate, DepartmentResponse
from app.services.department_service import DepartmentService

router = APIRouter()


@router.post(
    "",
    response_model=DepartmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tạo mới phòng ban trong doanh nghiệp"
)
async def create_department(
    data: DepartmentCreate,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Tạo phòng ban mới cho Tenant hiện tại (Yêu cầu Admin/Owner)."""
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản trị viên (Admin/Owner) mới có quyền tạo phòng ban",
        )
    return await DepartmentService.create_department(db, context.tenant_id, data)


@router.get(
    "",
    response_model=List[DepartmentResponse],
    summary="Lấy danh sách phòng ban thuộc doanh nghiệp"
)
async def list_departments(
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Liệt kê toàn bộ phòng ban trong Tenant của người dùng hiện tại."""
    return await DepartmentService.list_departments(db, context.tenant_id)
