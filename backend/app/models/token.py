"""
MÔ TẢ KIẾN TRÚC:
Model RevokedToken đại diện cho danh sách các JWT đã bị thu hồi (Blacklist/Revocation list).
Áp dụng cho SEC-005: Thu hồi token khi người dùng Logout hoặc khi Xoay vòng Refresh Token.
Trên PostgreSQL production, bảng này có thể được cấu hình UNLOGGED để tối ưu I/O (không ghi WAL).
"""

import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDMixin, TimestampMixin


class RevokedToken(Base, UUIDMixin, TimestampMixin):
    """Bảng lưu trữ danh sách Token đã bị thu hồi."""
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    token_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'access' | 'refresh'
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
