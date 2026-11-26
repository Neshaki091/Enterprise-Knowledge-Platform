"""
MÔ TẢ KIẾN TRÚC:
Pydantic Schemas phục vụ xác thực người dùng, cấp phát token, và quản lý Tenant Context.
Tuân thủ Pydantic v2 với đầy đủ validation và type hints.
"""

import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegisterRequest(BaseModel):
    """Schema đăng ký tài khoản người dùng mới kèm Tenant ban đầu."""
    email: EmailStr = Field(..., description="Email định danh người dùng")
    password: str = Field(..., min_length=8, max_length=128, description="Mật khẩu tối thiểu 8 ký tự")
    full_name: str = Field(..., min_length=2, max_length=255, description="Họ và tên đầy đủ")
    tenant_name: str = Field(..., min_length=2, max_length=255, description="Tên công ty / Doanh nghiệp khởi tạo")
    tenant_slug: Optional[str] = Field(None, min_length=2, max_length=100, description="Slug định danh URL")


class UserLoginRequest(BaseModel):
    """Schema đăng nhập nhận Token."""
    email: EmailStr = Field(..., description="Email đăng nhập")
    password: str = Field(..., description="Mật khẩu")
    tenant_id: Optional[uuid.UUID] = Field(None, description="ID Tenant muốn truy cập (nếu thuộc nhiều tenant)")


class TokenResponse(BaseModel):
    """Schema phản hồi Token khi đăng nhập hoặc làm mới thành công."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    tenant_id: uuid.UUID
    role: str


class TokenRefreshRequest(BaseModel):
    """Schema gửi Refresh Token để lấy cặp Token mới (Xoay vòng)."""
    refresh_token: str = Field(..., description="Refresh Token hợp lệ còn hạn")


class MembershipSummary(BaseModel):
    """Thông tin một Tenant mà người dùng có thể chuyển sang làm việc."""
    tenant_id: uuid.UUID
    tenant_name: str
    tenant_slug: str
    role: str
    department_id: Optional[uuid.UUID] = None
    is_active: bool = True


class TenantSwitchRequest(BaseModel):
    """Schema yêu cầu chuyển Tenant làm việc hiện tại."""
    tenant_id: uuid.UUID = Field(..., description="ID Tenant muốn chuyển sang")


class UserResponse(BaseModel):
    """Schema thông tin người dùng an toàn (không chứa hashed_password)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    is_active: bool
    is_superuser: bool
    created_at: datetime


class TenantContextResponse(BaseModel):
    """Schema thông tin phiên làm việc hiện tại (Tenant Context)."""
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str
    department_id: Optional[uuid.UUID] = None
    email: EmailStr
    is_superuser: bool = False
    permissions: List[str] = []
