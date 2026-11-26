import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class chuẩn cho toàn bộ ORM models trong dự án."""
    pass


class UUIDMixin:
    """Mixin cung cấp khóa chính UUID tự động sinh chuẩn RFC 4122.
    Sử dụng sqlalchemy.Uuid để tương thích hoàn hảo cả PostgreSQL (Native UUID)
    lẫn In-Memory SQLite khi chạy unit test.
    """
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True
    )


class TimestampMixin:
    """Mixin tự động quản lý thời điểm tạo và cập nhật bản ghi UTC."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )
