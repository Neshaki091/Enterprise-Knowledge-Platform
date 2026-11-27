"""
MÔ TẢ KIẾN TRÚC:
File này quản lý kết nối CSDL PostgreSQL 16 ở chế độ bất đồng bộ (Async).
Trên máy chủ GCP 1GB RAM (e2-micro), cấu hình Connection Pool siêu nhẹ:
- pool_size=10: Tối đa 10 kết nối thường trực trong pool.
- max_overflow=5: Cho phép mở thêm tối đa 5 kết nối tạm thời khi có đợt tải ngắn.
- pool_pre_ping=True: Tự động gửi lệnh 'SELECT 1' để kiểm tra kết nối còn sống trước khi sử dụng.

Ngữ cảnh Row-Level Security được gắn lại vào MỌI transaction (after_begin) bằng
`set_config(..., is_local=true)`: sau COMMIT/ROLLBACK, GUC tự hủy nên connection
trả về pool không thể mang ngữ cảnh Tenant cũ sang request khác.
"""

import uuid
from typing import AsyncGenerator, Optional

from sqlalchemy import Connection, event, text
from sqlalchemy.orm import Session, SessionTransaction
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import settings
from app.core import rls

# 1. Khởi tạo async_engine tối ưu hóa bộ nhớ
engine = create_async_engine(
    settings.async_database_url,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
)

# 2. Khởi tạo AsyncSessionFactory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


def _uuid_literal(value: Optional[str]) -> str:
    """Chuyển giá trị ngữ cảnh thành literal UUID an toàn; giá trị không hợp lệ hạ về rỗng (fail-closed)."""
    if not value:
        return "''"
    try:
        return f"'{uuid.UUID(str(value))}'"
    except (ValueError, AttributeError, TypeError):
        return "''"


@event.listens_for(Session, "after_begin")
def _apply_rls_on_transaction_begin(
    session: Session,
    transaction: SessionTransaction,
    connection: Connection,
) -> None:
    """
    Gắn ngữ cảnh RLS vào từng transaction PostgreSQL.

    Vì `get_db`/service có thể commit giữa chừng (ví dụ commit rồi refresh), session
    sẽ mở transaction mới trên một connection bất kỳ trong pool. Gắn GUC ở after_begin
    bảo đảm mọi transaction đều thấy đúng Tenant, không phụ thuộc connection vật lý.
    `is_local=true` khiến GUC tự hết hiệu lực sau COMMIT/ROLLBACK.
    """
    if not settings.DB_RLS_ENABLED:
        return
    if connection.dialect.name != "postgresql":
        return
    tenant_literal = _uuid_literal(rls.current_tenant_id.get())
    user_literal = _uuid_literal(rls.current_user_id.get())
    bypass_literal = "on" if rls.rls_bypass.get() else "off"
    connection.exec_driver_sql(
        "SELECT set_config('app.current_tenant', "
        f"{tenant_literal}, true), "
        "set_config('app.current_user', "
        f"{user_literal}, true), "
        "set_config('app.rls_bypass', "
        f"'{bypass_literal}', true)"
    )


async def verify_rls_database_role() -> None:
    """
    Fail-closed: RLS chỉ có hiệu lực khi role kết nối KHÔNG phải superuser và không có BYPASSRLS.

    PostgreSQL luôn cho superuser (và role có thuộc tính BYPASSRLS) đi xuyên qua policy,
    kể cả khi bảng được bật FORCE ROW LEVEL SECURITY. Do đó production bắt buộc dùng
    role ứng dụng riêng (ví dụ `saas_app`) thay vì `postgres`.
    """
    if not settings.DB_RLS_ENABLED:
        return
    if engine.dialect.name != "postgresql":
        raise RuntimeError("DB_RLS_ENABLED yêu cầu PostgreSQL 16; dialect hiện tại không hỗ trợ RLS.")
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        )
        bypasses_rls = result.scalar()
        if bypasses_rls is not False:
            raise RuntimeError(
                "RLS đang bật nhưng role kết nối CSDL là superuser hoặc có BYPASSRLS. "
                "Hãy cấu hình POSTGRES_USER thành role ứng dụng không đặc quyền (ví dụ saas_app)."
            )


# 3. FastAPI Dependency cung cấp AsyncSession theo từng request
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Cung cấp AsyncSession cho từng HTTP request; RLS được gắn tự động mỗi transaction."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            # SSE có thể đã đóng session sớm để trả connection về pool trước khi stream LLM;
            # chỉ commit khi session còn transaction mở, tránh mở lại connection vô ích.
            if session.in_transaction():
                await session.commit()
        except Exception:
            await session.rollback()
            raise
            raise
        finally:
            await session.close()
