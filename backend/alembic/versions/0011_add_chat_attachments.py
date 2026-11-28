"""0011_add_chat_attachments

Tài liệu đính kèm trong phiên chat (ephemeral):
- chat_attachments: metadata file, owner + session, TTL.
- chat_attachment_chunks: nội dung đã chunk + embedding float32 (BYTEA), KHÔNG ghi FAISS.
- chat_turns.has_attachment: loại lượt chat private khỏi export RAGAS của admin.

Revision ID: 0011_add_chat_attachments
Revises: 0010_add_chat_turns
Create Date: 2026-11-28 16:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_add_chat_attachments"
down_revision: Union[str, None] = "0010_add_chat_turns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _current_tenant_uuid() -> str:
    return "NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def _enable_rls(table: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bypass = "current_setting('app.rls_bypass', true) = 'on'"
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


def upgrade() -> None:
    op.create_table(
        "chat_attachments",
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
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=100), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="PROCESSED"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "session_id",
            "file_hash",
            name="uq_chat_attachment_session_hash",
        ),
    )
    op.create_index("ix_chat_attachments_tenant_id", "chat_attachments", ["tenant_id"])
    op.create_index("ix_chat_attachments_user_id", "chat_attachments", ["user_id"])
    op.create_index("ix_chat_attachments_session_id", "chat_attachments", ["session_id"])
    op.create_index("ix_chat_attachments_expires_at", "chat_attachments", ["expires_at"])
    op.create_index(
        "ix_chat_attachments_owner_session",
        "chat_attachments",
        ["tenant_id", "user_id", "session_id", "expires_at"],
    )

    op.create_table(
        "chat_attachment_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attachment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_attachments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_chat_attachment_chunks_attachment_id", "chat_attachment_chunks", ["attachment_id"])
    op.create_index("ix_chat_attachment_chunks_tenant_id", "chat_attachment_chunks", ["tenant_id"])

    op.add_column(
        "chat_turns",
        sa.Column("has_attachment", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("chat_turns", sa.Column("session_memory", sa.Text(), nullable=True))

    _enable_rls("chat_attachments")
    _enable_rls("chat_attachment_chunks")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS rls_chat_attachment_chunks ON chat_attachment_chunks")
        op.execute("DROP POLICY IF EXISTS rls_chat_attachments ON chat_attachments")
    op.drop_column("chat_turns", "session_memory")
    op.drop_column("chat_turns", "has_attachment")
    op.drop_table("chat_attachment_chunks")
    op.drop_table("chat_attachments")