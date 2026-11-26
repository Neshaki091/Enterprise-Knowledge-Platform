"""
MÔ TẢ KIẾN TRÚC:
Model Tenant là thực thể gốc (Root Entity) trong kiến trúc Multi-Tenant.
Mọi dữ liệu nghiệp vụ sau này (Users, Departments, KnowledgeBases, Documents)
đều được cách ly theo `tenant_id`.
"""

from typing import Dict, Any, List
from sqlalchemy import String, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, UUIDMixin, TimestampMixin


class Tenant(Base, UUIDMixin, TimestampMixin):
    """Bảng lưu trữ thông tin doanh nghiệp (Tenants) trên nền tảng SaaS."""
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    quota_limits: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    memberships: Mapped[List["Membership"]] = relationship(
        "Membership", back_populates="tenant", cascade="all, delete-orphan"
    )
    departments: Mapped[List["Department"]] = relationship(
        "Department", back_populates="tenant", cascade="all, delete-orphan"
    )
