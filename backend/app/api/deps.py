"""
MÔ TẢ KIẾN TRÚC:
Tập trung toàn bộ FastAPI Dependencies của hệ thống:
- get_db: Phiên làm việc CSDL AsyncSession
- get_current_user: Xác thực người dùng qua JWT (Sprint 2)
- get_current_tenant_context: Trích xuất TenantContext bất biến (Sprint 2)
"""

from app.core.database import get_db

__all__ = ["get_db"]
