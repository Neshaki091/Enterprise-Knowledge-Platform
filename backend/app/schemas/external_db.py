"""
MÔ TẢ KIẾN TRÚC:
Pydantic Schemas cho cấu hình kết nối DB riêng của doanh nghiệp và cấp phát DB JWT (Zero-Knowledge).
"""

import uuid
from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


class ExternalDBConnectorCreate(BaseModel):
    """Schema đăng ký kết nối DB riêng của doanh nghiệp (chỉ Tenant Admin/Owner)."""
    name: str = Field(..., min_length=2, max_length=100, description="Tên gợi nhớ của kết nối DB")
    db_type: Literal["postgresql", "sqlite"] = Field(..., description="Loại CSDL: postgresql hoặc sqlite")
    connection_uri: str = Field(..., min_length=8, max_length=1000, description="Chuỗi kết nối DSN")
    tenant_secret: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Khóa bí mật riêng của doanh nghiệp dùng để mã hóa DSN (không lưu trên server)"
    )
    allowed_tables: List[str] = Field(
        ...,
        min_length=1,
        description="Danh sách tên bảng được phép truy vấn Read-Only qua MCP"
    )

    @field_validator("allowed_tables")
    @classmethod
    def validate_table_names(cls, tables: List[str]) -> List[str]:
        cleaned = []
        for t in tables:
            name = t.strip()
            if not name or not name.replace("_", "").isalnum():
                raise ValueError(f"Tên bảng không hợp lệ: '{t}'")
            cleaned.append(name)
        return cleaned


class ExternalDBConnectorResponse(BaseModel):
    """Schema phản hồi thông tin kết nối DB (đã che thông tin nhạy cảm)."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    db_type: str
    host_masked: str
    allowed_tables: List[str]
    is_active: bool
    created_at: datetime


class ExternalDBTokenRequest(BaseModel):
    """Schema yêu cầu cấp JWT giới hạn quyền truy vấn DB riêng qua MCP."""
    connector_id: uuid.UUID
    tenant_secret: str = Field(..., min_length=8, max_length=128)
    expires_minutes: int = Field(60, ge=5, le=1440)


class ExternalDBTokenResponse(BaseModel):
    """Schema phản hồi DB JWT phục vụ AI Agent gọi MCP tools."""
    db_jwt: str
    token_type: str = "mcp_external_db"
    connector_id: uuid.UUID
    allowed_tables: List[str]
    expires_in_seconds: int
