"""
MÔ TẢ KIẾN TRÚC:
Model ChatFeedback lưu trữ đánh giá của người dùng về câu trả lời của AI (Thumbs Up / Down).
Dữ liệu phục vụ đo lường chất lượng mô hình và cải thiện tri thức doanh nghiệp.
"""

import uuid
from typing import Optional
from sqlalchemy import String, Text, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, UUIDMixin, TimestampMixin


class ChatFeedback(Base, UUIDMixin, TimestampMixin):
    """Bảng lưu trữ phản hồi đánh giá của người dùng."""
    __tablename__ = "feedbacks"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[str] = mapped_column(String(20), nullable=False)  # 'THUMBS_UP' | 'THUMBS_DOWN'
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User")
    tenant: Mapped["Tenant"] = relationship("Tenant")
