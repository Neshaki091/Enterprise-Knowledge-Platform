"""
MÔ TẢ KIẾN TRÚC:
Tầng Service (Business Logic) cho Quản lý Tenant.
Tách biệt hoàn toàn business logic khỏi router:
- Xử lý kiểm tra tính duy nhất của slug.
- Kiểm soát trạng thái kích hoạt/tạm khóa tenant.
- Quản lý giao dịch và truy vấn CSDL qua AsyncSession.
"""

import uuid
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.tenant import Tenant
from app.schemas.tenant import TenantCreate, TenantUpdate


class TenantService:
    """Service xử lý nghiệp vụ cho Tenant."""

    @staticmethod
    async def create_tenant(session: AsyncSession, data: TenantCreate) -> Tenant:
        """Tạo mới một Tenant, kiểm tra trùng lặp slug."""
        # 1. Kiểm tra slug đã tồn tại chưa
        existing = await TenantService.get_tenant_by_slug(session, data.slug)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant với slug '{data.slug}' đã tồn tại trong hệ thống."
            )

        # 2. Khởi tạo và lưu Tenant
        tenant = Tenant(
            name=data.name,
            slug=data.slug,
            is_active=data.is_active,
            quota_limits=data.quota_limits
        )
        session.add(tenant)
        await session.flush()
        await session.refresh(tenant)
        return tenant

    @staticmethod
    async def get_tenant_by_id(session: AsyncSession, tenant_id: uuid.UUID) -> Optional[Tenant]:
        """Lấy thông tin Tenant theo ID."""
        stmt = select(Tenant).where(Tenant.id == tenant_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_tenant_by_slug(session: AsyncSession, slug: str) -> Optional[Tenant]:
        """Lấy thông tin Tenant theo slug định danh."""
        stmt = select(Tenant).where(Tenant.slug == slug)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_tenants(
        session: AsyncSession,
        skip: int = 0,
        limit: int = 50
    ) -> List[Tenant]:
        """Lấy danh sách các Tenants có phân trang."""
        stmt = select(Tenant).order_by(Tenant.created_at.desc()).offset(skip).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def update_tenant(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        data: TenantUpdate
    ) -> Tenant:
        """Cập nhật thông tin cấu hình Tenant."""
        tenant = await TenantService.get_tenant_by_id(session, tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy Tenant với ID: {tenant_id}"
            )

        update_dict = data.model_dump(exclude_unset=True)
        for key, value in update_dict.items():
            setattr(tenant, key, value)

        await session.flush()
        await session.refresh(tenant)
        return tenant

    @staticmethod
    async def set_tenant_status(
        session: AsyncSession,
        tenant_id: uuid.UUID,
        is_active: bool
    ) -> Tenant:
        """Kích hoạt hoặc tạm khóa (Suspend) một Tenant (SR-001)."""
        tenant = await TenantService.get_tenant_by_id(session, tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Không tìm thấy Tenant với ID: {tenant_id}"
            )

        tenant.is_active = is_active
        await session.flush()
        await session.refresh(tenant)
        return tenant
