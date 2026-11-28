"""0012_enable_rls_external_db_connectors

Bật PostgreSQL Row-Level Security (RLS) cho bảng `external_db_connectors`
để đồng bộ với toàn bộ các bảng tenant-scoped khác (`chat_turns`, `chat_attachments`).

Revision ID: 0012_enable_rls_external_db_connectors
Revises: 0011_add_chat_attachments
Create Date: 2026-11-28 20:05:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0012_rls_external_db"
down_revision: Union[str, None] = "0011_add_chat_attachments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _current_tenant_uuid() -> str:
    return "NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def _is_bypass() -> str:
    return "COALESCE(current_setting('app.rls_bypass', true), 'false') = 'true'"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("ALTER TABLE external_db_connectors ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE external_db_connectors FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_policy ON external_db_connectors
        USING ({_is_bypass()} OR tenant_id = {_current_tenant_uuid()})
        WITH CHECK ({_is_bypass()} OR tenant_id = {_current_tenant_uuid()});
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON external_db_connectors;")
    op.execute("ALTER TABLE external_db_connectors NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE external_db_connectors DISABLE ROW LEVEL SECURITY;")
