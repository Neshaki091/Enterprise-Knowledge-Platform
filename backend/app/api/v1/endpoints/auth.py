"""
MÔ TẢ KIẾN TRÚC:
Router Endpoints cho Authentication & Tenant Context.
Cung cấp các cổng giao tiếp RESTful cho Đăng ký, Đăng nhập, Làm mới Token, Đăng xuất và Lấy thông tin phiên.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.context import get_current_tenant_context, TenantContext, security_scheme
from app.core.login_guard import login_guard
from app.schemas.auth import (
    UserRegisterRequest,
    UserLoginRequest,
    TokenResponse,
    TokenRefreshRequest,
    UserResponse,
    TenantContextResponse,
    MembershipSummary,
    TenantSwitchRequest,
)
from app.services.auth_service import AuthService

router = APIRouter()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Đăng ký tài khoản người dùng và khởi tạo Tenant"
)
async def register(
    data: UserRegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Đăng ký tài khoản người dùng và thiết lập Tenant ban đầu:
    - Tạo Tenant với gói dịch vụ FREE.
    - Tạo danh tính User với mật khẩu băm Bcrypt.
    - Thiết lập quyền OWNER cho người dùng tại Tenant này.
    """
    user, _, _ = await AuthService.register(db, data)
    return user


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Đăng nhập xác thực và nhận cặp Access/Refresh Token"
)
async def login(
    data: UserLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Đăng nhập bằng Email và Password:
    - Chống dò mật khẩu (5 lần sai / 5 phút cho mỗi email+IP, 30 lần / IP).
    - Xác thực mật khẩu Bcrypt.
    - Đối soát tư cách thành viên trong Tenant.
    - Cấp phát Access Token (15 phút) và Refresh Token (7 ngày) kèm JTI + mã phiên.
    """
    client_ip = request.client.host if request.client else "unknown"
    retry_after = login_guard.check(data.email, client_ip)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau {retry_after} giây.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        user, tenant_id, role = await AuthService.authenticate(db, data)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            login_guard.record_failure(data.email, client_ip)
        raise

    login_guard.reset(data.email, client_ip)
    return AuthService.create_tokens(user.id, tenant_id, role)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Xoay vòng Refresh Token (Token Rotation)"
)
async def refresh_token(
    data: TokenRefreshRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Làm mới Token với cơ chế Token Rotation:
    - Thu hồi ngay lập tức Refresh Token cũ vào bảng `revoked_tokens`.
    - Phát hiện nỗ lực tái sử dụng (Reuse Detection) và hủy toàn bộ phiên nếu có bất thường.
    - Trả về cặp Token mới.
    """
    return await AuthService.refresh_tokens(db, data.refresh_token)


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Đăng xuất và thu hồi Token"
)
async def logout(
    data: Optional[TokenRefreshRequest] = None,
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db)
):
    """
    Đăng xuất an toàn:
    - Đưa JTI của Access Token vào danh sách thu hồi.
    - Đưa JTI của Refresh Token (nếu có) vào danh sách thu hồi.
    """
    access_token = credentials.credentials
    refresh_token = data.refresh_token if data else None
    await AuthService.logout(db, access_token, refresh_token)
    return {"message": "Đăng xuất thành công. Token đã bị vô hiệu hóa."}


@router.get(
    "/me",
    response_model=TenantContextResponse,
    status_code=status.HTTP_200_OK,
    summary="Lấy thông tin ngữ cảnh phiên làm việc hiện tại"
)
async def get_me(
    context: TenantContext = Depends(get_current_tenant_context)
):
    """
    Trả về thông tin ngữ cảnh Tenant hiện tại của người dùng sau khi đã xác thực an toàn.
    """
    return TenantContextResponse(
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        role=context.role,
        department_id=context.department_id,
        email=context.email,
        is_superuser=context.is_superuser,
        permissions=["ADMIN"] if context.is_admin else ["READ_KNOWLEDGE", "QUERY"]
    )


@router.get(
    "/memberships",
    response_model=List[MembershipSummary],
    summary="Liệt kê các Tenant người dùng có thể chuyển sang"
)
async def list_memberships(
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Phục vụ Multi-Tenant Context Switcher trên giao diện."""
    return await AuthService.list_memberships(db, context.user_id, context.is_superuser)


@router.post(
    "/switch-tenant",
    response_model=TokenResponse,
    summary="Chuyển Tenant làm việc và cấp lại cặp Token"
)
async def switch_tenant(
    data: TenantSwitchRequest,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Đổi workspace sang Tenant khác mà người dùng là thành viên hợp lệ."""
    return await AuthService.switch_tenant(db, context.user_id, data.tenant_id)
