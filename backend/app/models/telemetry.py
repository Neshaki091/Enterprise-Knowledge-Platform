"""
MÔ TẢ KIẾN TRÚC:
Model TelemetryLog lưu trữ nhật ký vận hành, lưu lượng token, độ trễ và biến động lưu trữ
phục vụ Analytics Dashboard thời gian thực & Audit Logs (SEC-026).
Nguyên tắc bảo mật: KHÔNG lưu nội dung văn bản thô tài liệu hay nội dung tin nhắn riêng tư
của người dùng để bảo vệ quyền riêng tư khi Platform Admin giám sát.
"""

import uuid
from typing import Optional, Dict, Any
from sqlalchemy import String, Integer, Float, ForeignKey, JSON, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, UUIDMixin, TimestampMixin


class TelemetryLog(Base, UUIDMixin, TimestampMixin):
    """Bảng lưu trữ nhật ký giám sát vận hành hệ thống và sử dụng API."""
    __tablename__ = "telemetry_logs"

    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    mode: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, default=200, index=True, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    compressed_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    storage_delta_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    details: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
