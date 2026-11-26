"""
MÔ TẢ KIẾN TRÚC:
Pydantic Schemas phục vụ quản lý Phòng ban (Departments) và Thành viên Doanh nghiệp (Memberships).
"""

import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class DepartmentCreate(BaseModel):
    """Schema tạo mới phòng ban trực thuộc Tenant."""
    name: str = Field(..., min_length=2, max_length=255, description="Tên phòng ban")
    code: str = Field(..., min_length=2, max_length=50, pattern=r"^[A-Za-z0-9_-]+$", description="Mã phòng ban")


class DepartmentResponse(BaseModel):
    """Schema phản hồi thông tin phòng ban."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    code: str
    created_at: datetime
    updated_at: datetime


class MemberInviteRequest(BaseModel):
    """Schema thêm nhân viên / thành viên mới vào Tenant."""
    email: EmailStr = Field(..., description="Email nhân viên")
    password: str = Field(..., min_length=8, max_length=128, description="Mật khẩu khởi tạo nếu là tài khoản mới")
    full_name: str = Field(..., min_length=2, max_length=255, description="Họ và tên nhân viên")
    role: str = Field(
        "EMPLOYEE",
        pattern=r"^(OWNER|ADMIN|COMPLIANCE_OFFICER|EMPLOYEE)$",
        description="Vai trò trong Tenant",
    )
    department_id: Optional[uuid.UUID] = Field(None, description="ID phòng ban trực thuộc")


class MemberResponse(BaseModel):
    """Schema thông tin thành viên trong doanh nghiệp."""
    membership_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: EmailStr
    full_name: str
    role: str
    department_id: Optional[uuid.UUID] = None
    is_active: bool
    created_at: datetime
