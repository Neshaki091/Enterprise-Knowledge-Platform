"""
MÔ TẢ KIẾN TRÚC:
Model Document đại diện cho một tài liệu tri thức đã được nạp vào hệ thống.
Tuân thủ ADR-003: Zero Raw Storage - Không lưu file nhị phân gốc, chỉ lưu hash SHA-256 và raw_text.
Tuân thủ SEC-003: Cách ly đa khách thuê bằng tenant_id và unique constraint (tenant_id, file_hash).
"""

import uuid
from typing import Optional, Dict, Any, List
from sqlalchemy import String, Integer, Text, ForeignKey, UniqueConstraint, JSON, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, UUIDMixin, TimestampMixin


class Document(Base, UUIDMixin, TimestampMixin):
    """Bảng lưu trữ thông tin tài liệu và văn bản đã bóc tách."""
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "file_hash", name="uq_tenant_file_hash"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    department_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
        nullable=True
    )
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)  # SHA-256

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PROCESSED", nullable=False)
    doc_metadata: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant")
    department: Mapped[Optional["Department"]] = relationship("Department")
    parent_chunks: Mapped[List["DocumentParentChunk"]] = relationship(
        "DocumentParentChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentParentChunk.chunk_index"
    )
    child_chunks: Mapped[List["DocumentChildChunk"]] = relationship(
        "DocumentChildChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentChildChunk.chunk_index"
    )
