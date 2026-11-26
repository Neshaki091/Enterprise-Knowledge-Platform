"""
MÔ TẢ KIẾN TRÚC:
Model User đại diện cho danh tính người dùng (User Identity).
Một User có thể thuộc nhiều Doanh nghiệp khác nhau thông qua bảng Membership.
"""

from typing import List
from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, UUIDMixin, TimestampMixin


class User(Base, UUIDMixin, TimestampMixin):
    """Bảng lưu trữ danh tính người dùng."""
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    memberships: Mapped[List["Membership"]] = relationship(
        "Membership", back_populates="user", cascade="all, delete-orphan"
    )
