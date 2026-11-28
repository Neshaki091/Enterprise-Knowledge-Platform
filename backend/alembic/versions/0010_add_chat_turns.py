"""0010_add_chat_turns

Lưu một lượt hỏi-đáp RAG (query, answer, snapshot context, chunk_id, citations,
model, latency, token) để làm lịch sử hội thoại và xuất dataset RAGAS theo tháng.

Revision ID: 0010_add_chat_turns
Revises: 0009_add_perf_composite_indexes
Create Date: 2026-11-28 10:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_add_chat_turns"
down_revision: Union[str, None] = "0009_add_perf_composite_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _current_tenant_uuid() -> str:
    return "NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "chat_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("contexts", sa.JSON(), nullable=False),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("chunk_ids", sa.JSON(), nullable=False),
        sa.Column("llm_context", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=100), nullable=True),
        sa.Column("mode", sa.String(length=30), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ttfb_ms", sa.Float(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compressed_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("compression_savings_percent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("feedback_rating", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_chat_turns_tenant_id", "chat_turns", ["tenant_id"])
    op.create_index("ix_chat_turns_user_id", "chat_turns", ["user_id"])
    op.create_index("ix_chat_turns_session_id", "chat_turns", ["session_id"])
    op.create_index("ix_chat_turns_created_at", "chat_turns", ["created_at"])
    op.create_index("ix_chat_turns_tenant_created_at", "chat_turns", ["tenant_id", "created_at"])

    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    bypass = "current_setting('app.rls_bypass', true) = 'on'"
    op.execute("ALTER TABLE chat_turns ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_turns FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS rls_chat_turns ON chat_turns")
    op.execute(
        f"""
        CREATE POLICY rls_chat_turns ON chat_turns
        USING ({bypass} OR tenant_id = {_current_tenant_uuid()})
        WITH CHECK ({bypass} OR tenant_id = {_current_tenant_uuid()})
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS rls_chat_turns ON chat_turns")
    op.drop_table("chat_turns")