import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict


class TenantBase(BaseModel):
    """Schema cơ bản chứa thông tin Tenant."""
    name: str = Field(..., min_length=2, max_length=255, description="Tên công ty / doanh nghiệp")
    slug: str = Field(..., min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$", description="Định danh duy nhất (slug)")
    is_active: bool = Field(default=True, description="Trạng thái kích hoạt")
    quota_limits: Dict[str, Any] = Field(default_factory=dict, description="Cấu hình hạn ngạch tài nguyên")


class TenantCreate(TenantBase):
    """Schema yêu cầu tạo mới Tenant."""
    pass


class TenantUpdate(BaseModel):
    """Schema cập nhật thông tin Tenant."""
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    is_active: Optional[bool] = None
    quota_limits: Optional[Dict[str, Any]] = None


class TenantResponse(TenantBase):
    """Schema trả về thông tin Tenant chi tiết."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
