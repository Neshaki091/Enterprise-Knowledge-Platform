"""
MÔ TẢ KIẾN TRÚC:
Ngữ cảnh Row-Level Security (RLS) cho PostgreSQL 16.
- Middleware trích xuất tenant/user từ JWT đã ký và X-Tenant-ID.
- `database._apply_rls_on_transaction_begin` áp `set_config('app.current_tenant'|'app.current_user'|'app.rls_bypass')`
  ở mọi transaction (transaction-local), để các policy RLS (migration 0007) tự động lọc dữ liệu.
- Các tuyến xác thực công khai (/auth/*) chạy ở chế độ bypass vì chưa có tenant context.
"""

import logging
from contextvars import ContextVar
from typing import Optional, Tuple

import jwt

from app.core.security import decode_jwt_token

logger = logging.getLogger(__name__)

current_tenant_id: ContextVar[Optional[str]] = ContextVar("current_tenant_id", default=None)
current_user_id: ContextVar[Optional[str]] = ContextVar("current_user_id", default=None)
rls_bypass: ContextVar[bool] = ContextVar("rls_bypass", default=False)

AUTH_BYPASS_PREFIX = "/api/v1/auth"


def resolve_rls_context(
    authorization: Optional[str],
    x_tenant_id: Optional[str],
    path: str,
) -> Tuple[Optional[str], Optional[str], bool]:
    """Tính bộ ba (tenant_id, user_id, bypass) cho một HTTP request."""
    # Auth công khai và tài liệu OpenAPI cần truy cập bảng toàn cục trước khi có tenant context
    if path.startswith(AUTH_BYPASS_PREFIX) or path in {"/", "/docs", "/redoc", "/openapi.json"}:
        return None, None, True

    if not authorization or not authorization.startswith("Bearer "):
        return None, None, False

    try:
        payload = decode_jwt_token(authorization.split(" ", 1)[1])
    except jwt.InvalidTokenError:
        return None, None, False

    role = str(payload.get("role") or "").upper()
    bypass = role == "SUPER_ADMIN"
    tenant_id = x_tenant_id or payload.get("tenant_id")
    user_id = payload.get("sub")
    return (str(tenant_id) if tenant_id else None, str(user_id) if user_id else None, bypass)
