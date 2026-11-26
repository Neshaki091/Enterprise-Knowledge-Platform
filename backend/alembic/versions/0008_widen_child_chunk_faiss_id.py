"""0008_widen_child_chunk_faiss_id

Mở rộng cột document_child_chunks.faiss_id từ INTEGER (int32) sang BIGINT (int64).

Lý do: DocumentService sinh faiss_id ngẫu nhiên trong miền [0, 2^62) để tránh va chạm
khi dùng FAISS IndexIDMap2. Kiểu INTEGER của PostgreSQL chỉ chứa tới 2^31-1 nên upload
thất bại với lỗi "value out of int32 range". SQLite (chỉ dùng cho kiểm thử) lưu số
nguyên 64-bit nên không cần ALTER.

Revision ID: 0008_widen_child_chunk_faiss_id
Revises: 0007_enable_postgres_rls
Create Date: 2026-11-26 21:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_widen_child_chunk_faiss_id"
down_revision: Union[str, None] = "0007_enable_postgres_rls"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.alter_column(
        "document_child_chunks",
        "faiss_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # Hạ kiểu có thể thất bại nếu đã có faiss_id vượt 2^31-1; đó là hành vi mong muốn
    # để không âm thầm cắt cụt định danh vector.
    op.alter_column(
        "document_child_chunks",
        "faiss_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
