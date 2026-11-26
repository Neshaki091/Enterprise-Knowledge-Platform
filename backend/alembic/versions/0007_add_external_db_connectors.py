"""0007_add_external_db_connectors

Revision ID: 0007_add_external_db_connectors
Revises: 0006_add_telemetry_logs
Create Date: 2026-11-26 21:25:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007_add_external_db_connectors"
down_revision: Union[str, None] = "0008_widen_child_chunk_faiss_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_db_connectors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("db_type", sa.String(length=30), nullable=False),
        sa.Column("encrypted_dsn", sa.Text(), nullable=False),
        sa.Column("encryption_salt", sa.String(length=64), nullable=False),
        sa.Column("host_masked", sa.String(length=120), nullable=False),
        sa.Column("allowed_tables", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_ext_db_name"),
    )
    op.create_index("ix_external_db_connectors_tenant_id", "external_db_connectors", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("external_db_connectors")
