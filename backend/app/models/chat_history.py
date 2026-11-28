"""
MÔ TẢ KIẾN TRÚC:
Model ChatTurn lưu một lượt hỏi-đáp RAG để phục vụ:
1. Lịch sử hội thoại theo session/người dùng (có thể mở rộng UI).
2. Xuất dataset RAGAS theo tháng: query, answer, context chunks, chunk_id, citations,
   model, mode, latency, token và feedback.

Bảo mật: bảng có tenant_id + RLS; API history chỉ trả lượt của chính người dùng.
Không thay thế bảng feedbacks; feedback vẫn là nguồn đánh giá chất lượng riêng.
"""

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import Float, ForeignKey, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ChatTurn(Base, UUIDMixin, TimestampMixin):
    """Một lượt hỏi-đáp hoàn chỉnh kèm snapshot ngữ cảnh đã dùng cho LLM."""

    __tablename__ = "chat_turns"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
        default=uuid.uuid4,
    )

    query: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)

    # Snapshot đầy đủ để RAGAS chấm lại về sau, kể cả khi chunk gốc bị sửa/xoá.
    contexts: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    citations: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    chunk_ids: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    llm_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    mode: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ttfb_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    compressed_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    compression_savings_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    feedback_rating: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)