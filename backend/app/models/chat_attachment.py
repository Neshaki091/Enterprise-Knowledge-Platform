"""
MÔ TẢ KIẾN TRÚC:
Tài liệu đính kèm TRONG PHIÊN CHAT (Ephemeral Chat Attachment) - không thuộc kho tri thức chung.

Nguyên tắc bảo mật:
- Chỉ người tải lên, trong đúng session_id, được đọc và truy xuất.
- Không ghi vào FAISS index của tenant; vector lưu ngay trong PostgreSQL dưới dạng
  float32 BYTEA và chỉ so khớp trong phạm vi attachment của chính user/session.
- RLS theo tenant là lớp bảo vệ thứ hai; API luôn lọc thêm user_id + session_id.
- TTL ngắn (mặc định 24h) để không biến chat thành kho lưu trữ vĩnh viễn.
"""

import uuid
from datetime import datetime
from typing import List

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class ChatAttachment(Base, UUIDMixin, TimestampMixin):
    """Metadata một file người dùng đính kèm trong phiên chat."""

    __tablename__ = "chat_attachments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "session_id",
            "file_hash",
            name="uq_chat_attachment_session_hash",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True, nullable=False)

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PROCESSED", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)

    chunks: Mapped[List["ChatAttachmentChunk"]] = relationship(
        "ChatAttachmentChunk",
        back_populates="attachment",
        cascade="all, delete-orphan",
        order_by="ChatAttachmentChunk.chunk_index",
    )


class ChatAttachmentChunk(Base, UUIDMixin, TimestampMixin):
    """Một đoạn văn bản + vector embedding của file đính kèm chat."""

    __tablename__ = "chat_attachment_chunks"

    attachment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("chat_attachments.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # float32 little-endian, đã chuẩn hóa L2; đọc lại bằng np.frombuffer.
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    attachment: Mapped["ChatAttachment"] = relationship("ChatAttachment", back_populates="chunks")