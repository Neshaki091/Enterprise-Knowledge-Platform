"""0009_add_perf_composite_indexes

Revision ID: 0009_add_perf_composite_indexes
Revises: 0007_add_external_db_connectors
Create Date: 2026-11-27 14:00:00

Bổ sung composite index cho các truy vấn nóng:
- Danh sách tài liệu: documents (tenant_id, created_at DESC).
- Đếm chunk theo trang tài liệu: parent/child chunks (tenant_id, document_id).
- Dashboard telemetry theo tenant + thời gian: telemetry_logs (tenant_id, created_at DESC).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0009_add_perf_composite_indexes"
down_revision: Union[str, None] = "0007_add_external_db_connectors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEXES = (
    ("ix_documents_tenant_created", "documents", "(tenant_id, created_at DESC)"),
    ("ix_parent_chunks_tenant_document", "document_parent_chunks", "(tenant_id, document_id)"),
    ("ix_child_chunks_tenant_document", "document_child_chunks", "(tenant_id, document_id)"),
    ("ix_telemetry_logs_tenant_created", "telemetry_logs", "(tenant_id, created_at DESC)"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for index_name, table_name, columns in _INDEXES:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} {columns}"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for index_name, _table_name, _columns in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
