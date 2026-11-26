import uuid
from typing import Optional
from sqlalchemy import String, Boolean, ForeignKey, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, UUIDMixin, TimestampMixin


class Membership(Base, UUIDMixin, TimestampMixin):
    """Bảng quan hệ User - Tenant (Membership)."""
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant_membership"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False
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
        nullable=True
    )
    role: Mapped[str] = mapped_column(String(50), default="EMPLOYEE", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="memberships")
    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="memberships")
    department: Mapped[Optional["Department"]] = relationship("Department", back_populates="memberships")
