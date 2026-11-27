"""
MÔ TẢ KIẾN TRÚC:
Service Layer quản lý Phòng ban (Departments) và Thành viên Doanh nghiệp (Memberships).
Đảm bảo cô lập chặt chẽ theo tenant_id (SEC-003, SEC-004).
"""

import asyncio
import uuid
from typing import List
from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import hash_password
from app.models.department import Department
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.department import DepartmentCreate, MemberInviteRequest, MemberResponse


class DepartmentService:
    """Xử lý nghiệp vụ Phòng ban & Thành viên trong phạm vi Tenant."""

    @staticmethod
    async def create_department(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        data: DepartmentCreate,
    ) -> Department:
        """Tạo phòng ban mới cho Tenant, chống trùng mã code nội bộ."""
        normalized_code = data.code.strip().upper()
        existing = await db.execute(
            select(Department).where(
                Department.tenant_id == tenant_id,
                Department.code == normalized_code,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Mã phòng ban '{normalized_code}' đã tồn tại trong doanh nghiệp.",
            )

        dept = Department(
            tenant_id=tenant_id,
            name=data.name.strip(),
            code=normalized_code,
        )
        db.add(dept)
        await db.commit()
        await db.refresh(dept)
        return dept

    @staticmethod
    async def list_departments(db: AsyncSession, tenant_id: uuid.UUID) -> List[Department]:
        """Lấy danh sách phòng ban thuộc Tenant."""
        res = await db.execute(
            select(Department)
            .where(Department.tenant_id == tenant_id)
            .order_by(Department.created_at.asc())
        )
        return list(res.scalars().all())

    @staticmethod
    async def add_member(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        data: MemberInviteRequest,
    ) -> MemberResponse:
        """
        Thêm người dùng vào doanh nghiệp với vai trò và phòng ban cụ thể.
        - Nếu email chưa tồn tại: tạo User mới với mật khẩu băm Bcrypt.
        - Nếu department_id được chỉ định: bắt buộc đối soát thuộc đúng tenant_id.
        - Thực thi hạn ngạch quota_limits.max_users của Tenant.
        """
        # 0. Kiểm tra hạn ngạch số thành viên
        tenant = await db.get(Tenant, tenant_id)
        if tenant is not None:
            max_users = int((tenant.quota_limits or {}).get("max_users", 0) or 0)
            if max_users > 0:
                active_members = (await db.execute(
                    select(func.count(Membership.id)).where(
                        Membership.tenant_id == tenant_id,
                        Membership.is_active.is_(True),
                    )
                )).scalar_one() or 0
                if int(active_members) >= max_users:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Doanh nghiệp đã đạt hạn ngạch tối đa {max_users} thành viên. "
                        "Vui lòng nâng cấp gói dịch vụ để thêm nhân sự.",
                    )

        if data.department_id:
            dept_res = await db.execute(
                select(Department).where(
                    Department.id == data.department_id,
                    Department.tenant_id == tenant_id,
                )
            )
            if not dept_res.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Phòng ban không tồn tại trong doanh nghiệp này.",
                )

        user_res = await db.execute(select(User).where(User.email == data.email))
        user = user_res.scalar_one_or_none()
        if not user:
            user = User(
                email=data.email,
                hashed_password=await asyncio.to_thread(hash_password, data.password),
                full_name=data.full_name,
                is_active=True,
                is_superuser=False,
            )
            db.add(user)
            await db.flush()
        else:
            existing_mem = await db.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.tenant_id == tenant_id,
                )
            )
            if existing_mem.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Người dùng '{data.email}' đã là thành viên của doanh nghiệp.",
                )

        membership = Membership(
            user_id=user.id,
            tenant_id=tenant_id,
            department_id=data.department_id,
            role=data.role.upper(),
            is_active=True,
        )
        db.add(membership)
        await db.commit()
        await db.refresh(membership)

        return MemberResponse(
            membership_id=membership.id,
            user_id=user.id,
            tenant_id=tenant_id,
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            department_id=membership.department_id,
            is_active=membership.is_active,
            created_at=membership.created_at,
        )

    @staticmethod
    async def list_members(db: AsyncSession, tenant_id: uuid.UUID) -> List[MemberResponse]:
        """Lấy danh sách thành viên trong phạm vi Tenant."""
        res = await db.execute(
            select(Membership)
            .where(Membership.tenant_id == tenant_id)
            .options(selectinload(Membership.user))
            .order_by(Membership.created_at.asc())
        )
        memberships = res.scalars().all()
        return [
            MemberResponse(
                membership_id=m.id,
                user_id=m.user_id,
                tenant_id=m.tenant_id,
                email=m.user.email,
                full_name=m.user.full_name,
                role=m.role,
                department_id=m.department_id,
                is_active=m.is_active,
                created_at=m.created_at,
            )
            for m in memberships
            if m.user
        ]
