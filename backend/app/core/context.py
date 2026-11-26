"""
MÔ TẢ KIẾN TRÚC:
Module Tenant Context & Dependencies.
Quản lý bảo mật đa khách thuê (Multi-tenancy Security), chống tấn công BOLA/IDOR (SEC-004),
và phân quyền theo vai trò RBAC (Role-Based Access Control).
"""

import uuid
from dataclasses import dataclass
from typing import Optional, List, Callable
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_jwt_token
from app.models.user import User
from app.models.tenant import Tenant
from app.models.membership import Membership
from app.models.token import RevokedToken

security_scheme = HTTPBearer(auto_error=True)


@dataclass(frozen=True)
class TenantContext:
    """Đối tượng bất biến mang thông tin phiên làm việc và ngữ cảnh Tenant của người dùng."""
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    department_id: Optional[uuid.UUID]
    email: str
    is_superuser: bool

    @property
    def is_admin(self) -> bool:
        """Kiểm tra người dùng có quyền quản trị Tenant không."""
        return self.is_superuser or self.role.upper() in ["OWNER", "ADMIN", "SUPER_ADMIN"]

    @property
    def is_compliance_officer(self) -> bool:
        """Kiểm tra vai trò kiểm toán / tuân thủ."""
        return self.is_superuser or self.role.upper() in ["OWNER", "ADMIN", "COMPLIANCE_OFFICER"]


async def get_current_tenant_context(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    db: AsyncSession = Depends(get_db)
) -> TenantContext:
    """
    Dependency trích xuất và xác thực Tenant Context.
    
    Quy trình kiểm tra bảo mật Fail-Closed:
    1. Giải mã JWT và kiểm tra hạn sử dụng (EXP).
    2. Kiểm tra token có nằm trong danh sách thu hồi (RevokedToken) không.
    3. Xác minh User còn hoạt động (is_active).
    4. Xác định tenant_id: ưu tiên claim trong token, hoặc X-Tenant-ID.
    5. ĐỐI SOÁT CSDL: Kiểm tra User có Membership hợp lệ và đang active tại Tenant đó không (BOLA Prevention).
    """
    token = credentials.credentials
    try:
        payload = decode_jwt_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token đã hết hạn sử dụng (Token expired)",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token không hợp lệ hoặc chữ ký sai (Invalid token)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Kiểm tra loại token phải là 'access'
    token_type = payload.get("type")
    if token_type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu Access Token hợp lệ",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. Kiểm tra Blacklist / Revocation cho cả JTI lẫn mã phiên (sid/token family)
    jti = payload.get("jti")
    family_id = payload.get("sid")
    revocation_keys = [key for key in (jti, family_id) if key]
    if revocation_keys:
        revocation_query = select(RevokedToken).where(RevokedToken.jti.in_(revocation_keys))
        revoked_result = await db.execute(revocation_query)
        if revoked_result.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token đã bị thu hồi hoặc phiên đã đăng xuất (Token revoked)",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 3. Trích xuất User ID
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token thiếu định danh người dùng (sub claim)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID trong token không đúng định dạng UUID",
        )

    user_query = select(User).where(User.id == user_uuid)
    user_result = await db.execute(user_query)
    user = user_result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Người dùng không tồn tại hoặc đã bị khóa",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 4. Xác định Tenant ID mục tiêu: Nếu client truyền X-Tenant-ID thì ưu tiên kiểm tra, ngược lại dùng claim trong token
    target_tenant_str = x_tenant_id if x_tenant_id is not None else payload.get("tenant_id")
    if not target_tenant_str:
        # Nếu không có, tìm membership đầu tiên của user
        membership_first = await db.execute(
            select(Membership).where(
                Membership.user_id == user_uuid,
                Membership.is_active.is_(True)
            )
        )
        membership = membership_first.scalars().first()
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Người dùng chưa thuộc bất kỳ Tenant nào hoạt động",
            )
        target_tenant_uuid = membership.tenant_id
    else:
        try:
            target_tenant_uuid = uuid.UUID(str(target_tenant_str))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tenant ID không đúng định dạng UUID",
            )

        # 5. ĐỐI SOÁT MEMBERSHIP & TRẠNG THÁI TENANT TRONG CSDL (Chặn BOLA & Tenant bị khóa)
        membership_query = select(Membership).where(
            Membership.user_id == user_uuid,
            Membership.tenant_id == target_tenant_uuid,
            Membership.is_active.is_(True)
        )
        membership_res = await db.execute(membership_query)
        membership = membership_res.scalar_one_or_none()

        if not membership and not user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Truy cập bị từ chối: Bạn không có quyền trong Tenant này (BOLA Blocked)",
            )

    if not user.is_superuser:
        tenant_res = await db.execute(select(Tenant).where(Tenant.id == target_tenant_uuid))
        tenant_obj = tenant_res.scalar_one_or_none()
        if not tenant_obj or not tenant_obj.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Doanh nghiệp (Tenant) đã bị tạm khóa hoặc không tồn tại",
            )

    role = "SUPER_ADMIN" if user.is_superuser else (membership.role if membership else "EMPLOYEE")
    dept_id = membership.department_id if membership else None

    return TenantContext(
        user_id=user.id,
        tenant_id=target_tenant_uuid,
        role=role,
        department_id=dept_id,
        email=user.email,
        is_superuser=user.is_superuser
    )


async def require_superuser(
    context: TenantContext = Depends(get_current_tenant_context)
) -> TenantContext:
    """Dependency bắt buộc quyền Quản trị viên Nền tảng (Platform Super-Admin)."""
    if not context.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Yêu cầu quyền Quản trị viên Nền tảng (Platform Super-Admin)",
        )
    return context


def require_roles(allowed_roles: List[str]) -> Callable:
    """Dependency Factory kiểm tra phân quyền RBAC."""
    allowed_set = {r.upper() for r in allowed_roles}

    async def role_checker(context: TenantContext = Depends(get_current_tenant_context)) -> TenantContext:
        if context.is_superuser:
            return context
        if context.role.upper() not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Quyền bị từ chối: Yêu cầu một trong các vai trò {allowed_roles}",
            )
        return context

    return role_checker
