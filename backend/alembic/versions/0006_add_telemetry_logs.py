"""0006_add_telemetry_logs

Revision ID: 0006_add_telemetry_logs
Revises: 0005_add_pg_fts_gin_index
Create Date: 2026-11-25 23:25:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006_add_telemetry_logs"
down_revision: Union[str, None] = "0005_add_pg_fts_gin_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telemetry_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("mode", sa.String(length=30), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compressed_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_delta_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_telemetry_logs_tenant_id", "telemetry_logs", ["tenant_id"])
    op.create_index("ix_telemetry_logs_user_id", "telemetry_logs", ["user_id"])
    op.create_index("ix_telemetry_logs_event_type", "telemetry_logs", ["event_type"])
    op.create_index("ix_telemetry_logs_status_code", "telemetry_logs", ["status_code"])


def downgrade() -> None:
    op.drop_table("telemetry_logs")
