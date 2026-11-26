"""
MÔ TẢ KIẾN TRÚC:
Trung tâm chính sách Kiểm soát Truy cập theo Phòng ban (Department-Level ACL).
Quy tắc Fail-Closed:
- OWNER / ADMIN / SUPER_ADMIN / COMPLIANCE_OFFICER có quyền đọc toàn bộ tài liệu trong Tenant.
- Nhân viên chỉ đọc tài liệu thuộc phòng ban của mình + tài liệu dùng chung (department_id IS NULL).
- Nhân viên chưa thuộc phòng ban chỉ đọc tài liệu dùng chung.
- Mọi yêu cầu lọc/chọn department_id nằm ngoài phạm vi đều bị từ chối 403.
"""

import uuid
from typing import FrozenSet, Optional, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.sql import Select

from app.core.context import TenantContext

T = TypeVar("T", bound=Select)

TENANT_WIDE_READ_ROLES: FrozenSet[str] = frozenset(
    {"OWNER", "ADMIN", "SUPER_ADMIN", "COMPLIANCE_OFFICER"}
)


def allowed_department_ids(context: TenantContext) -> Optional[FrozenSet[uuid.UUID]]:
    """
    Trả về tập department_id được phép đọc.

    Returns:
        None nếu được đọc toàn Tenant; ngược lại là tập UUID (có thể rỗng).
    """
    if context.is_superuser or context.role.upper() in TENANT_WIDE_READ_ROLES:
        return None
    if context.department_id is None:
        return frozenset()
    return frozenset({context.department_id})


def can_read_department(
    allowed: Optional[FrozenSet[uuid.UUID]],
    department_id: Optional[uuid.UUID],
) -> bool:
    """Kiểm tra một tài liệu (theo department_id) có nằm trong phạm vi đọc hay không."""
    if allowed is None:
        return True
    if department_id is None:
        return True
    return department_id in allowed


def apply_document_department_scope(
    stmt: T,
    department_column,
    allowed: Optional[FrozenSet[uuid.UUID]],
) -> T:
    """Gắn điều kiện phòng ban vào câu truy vấn tài liệu."""
    if allowed is None:
        return stmt
    if not allowed:
        return stmt.where(department_column.is_(None))
    return stmt.where(or_(department_column.is_(None), department_column.in_(list(allowed))))


def ensure_department_allowed(
    context: TenantContext,
    requested_department_id: Optional[uuid.UUID],
) -> Optional[FrozenSet[uuid.UUID]]:
    """
    Xác thực department_id do client yêu cầu và trả về phạm vi đọc của context.

    Raises:
        HTTPException 403 nếu client yêu cầu phòng ban ngoài phạm vi.
    """
    allowed = allowed_department_ids(context)
    if (
        requested_department_id is not None
        and allowed is not None
        and requested_department_id not in allowed
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Truy cập bị từ chối: Bạn không có quyền truy cập tài liệu của phòng ban khác",
        )
    return allowed


def ensure_can_write_department(
    context: TenantContext,
    target_department_id: Optional[uuid.UUID],
) -> None:
    """
    Xác thực quyền ghi tài liệu vào một phòng ban.

    - Quản trị viên / Super-Admin được chọn bất kỳ phòng ban hoặc tài liệu dùng chung.
    - Nhân viên bắt buộc ghi vào đúng phòng ban của mình (không được tạo tài liệu dùng chung).
    """
    if context.is_superuser or context.role.upper() in {"OWNER", "ADMIN", "SUPER_ADMIN"}:
        return
    if context.department_id is None or target_department_id != context.department_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản trị viên mới được tải tài liệu dùng chung hoặc vào phòng ban khác. "
            "Nhân viên chỉ được tải tài liệu vào đúng phòng ban của mình.",
        )
