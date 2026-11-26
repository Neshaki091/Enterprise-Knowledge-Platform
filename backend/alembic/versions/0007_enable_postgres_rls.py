"""0007_enable_postgres_rls

Bật Row-Level Security (RLS) thực sự trên PostgreSQL 16 cho toàn bộ bảng có tenant_id.
Ngữ cảnh phiên được thiết lập bởi app.core.database.get_db:
  - app.current_tenant: UUID tenant đang làm việc
  - app.current_user: UUID người dùng
  - app.rls_bypass: 'on' cho Super-Admin và các tuyến xác thực công khai

Revision ID: 0007_enable_postgres_rls
Revises: 0006_add_telemetry_logs
Create Date: 2026-11-26 08:00:00

"""
from typing import Sequence, Union
from alembic import op

revision: str = "0007_enable_postgres_rls"
down_revision: Union[str, None] = "0006_add_telemetry_logs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TENANT_TABLES = (
    "memberships",
    "departments",
    "documents",
    "document_parent_chunks",
    "document_child_chunks",
    "feedbacks",
    "telemetry_logs",
)


def _current_tenant_uuid() -> str:
    return "NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite chỉ dùng cho kiểm thử; RLS là cơ chế đặc thù PostgreSQL.
        return

    bypass = "current_setting('app.rls_bypass', true) = 'on'"

    # 1. Bảng tenants: mỗi phiên chỉ thấy Tenant đang làm việc (Super-Admin bypass)
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS rls_tenants ON tenants")
    op.execute(
        f"""
        CREATE POLICY rls_tenants ON tenants
        USING ({bypass} OR id = {_current_tenant_uuid()})
        WITH CHECK ({bypass} OR id = {_current_tenant_uuid()})
        """
    )

    # 2. Các bảng dữ liệu nghiệp vụ: bắt buộc tenant_id khớp ngữ cảnh phiên
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS rls_{table} ON {table}")
        op.execute(
            f"""
            CREATE POLICY rls_{table} ON {table}
            USING ({bypass} OR tenant_id = {_current_tenant_uuid()})
            WITH CHECK ({bypass} OR tenant_id = {_current_tenant_uuid()})
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS rls_{table} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS rls_tenants ON tenants")
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
