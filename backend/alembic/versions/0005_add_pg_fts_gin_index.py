"""0005_add_pg_fts_gin_index

Revision ID: 0005_add_pg_fts_gin_index
Revises: 0004_add_feedbacks
Create Date: 2026-11-25 22:30:00

"""
from typing import Sequence, Union
from alembic import op

revision: str = "0005_add_pg_fts_gin_index"
down_revision: Union[str, None] = "0004_add_feedbacks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Chỉ tạo GIN Full-Text Index khi chạy trên PostgreSQL 16
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_document_child_chunks_fts_gin "
            "ON document_child_chunks USING GIN (to_tsvector('simple', content))"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_document_child_chunks_fts_gin")
