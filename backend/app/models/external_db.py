"""
MÔ TẢ KIẾN TRÚC:
Model ExternalDBConnector lưu trữ cấu hình kết nối DB riêng của từng Doanh nghiệp.
Bảo mật Zero-Knowledge:
- Chuỗi kết nối (DSN) được mã hóa AES-256-GCM với khóa dẫn xuất từ `tenant_secret` do chính
  doanh nghiệp nắm giữ (không lưu `tenant_secret` trong CSDL).
- Chủ nền tảng (Platform Super-Admin) không thể giải mã hoặc truy vấn DB riêng của khách hàng.
"""

import uuid
from typing import List, Optional
from sqlalchemy import String, Text, Boolean, ForeignKey, JSON, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDMixin, TimestampMixin


class ExternalDBConnector(Base, UUIDMixin, TimestampMixin):
    """Bảng lưu trữ cấu hình kết nối DB riêng đã mã hóa của từng Tenant."""
    __tablename__ = "external_db_connectors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tenant_ext_db_name"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    db_type: Mapped[str] = mapped_column(String(30), nullable=False)  # "postgresql" | "sqlite"
    encrypted_dsn: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    host_masked: Mapped[str] = mapped_column(String(120), nullable=False)
    allowed_tables: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
