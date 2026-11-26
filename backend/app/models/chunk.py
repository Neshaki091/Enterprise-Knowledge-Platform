"""
MÔ TẢ KIẾN TRÚC:
Model Chunks triển khai cơ chế Hierarchical Chunking (Parent-Child) theo ADR-005.
- Parent Chunk (~1.000 tokens): Lưu ngữ cảnh toàn diện trong PostgreSQL.
- Child Chunk (~200 tokens): Khớp vector chính xác trong FAISS mmap, trỏ ngược về Parent Chunk.
"""

import uuid
from typing import Optional, List
from sqlalchemy import BigInteger, String, Integer, Text, ForeignKey, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, UUIDMixin, TimestampMixin


class DocumentParentChunk(Base, UUIDMixin, TimestampMixin):
    """Bảng lưu trữ các đoạn văn bản lớn (Parent Chunks) dùng để nạp vào prompt của LLM."""
    __tablename__ = "document_parent_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    heading: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    document: Mapped["Document"] = relationship("Document", back_populates="parent_chunks")
    child_chunks: Mapped[List["DocumentChildChunk"]] = relationship(
        "DocumentChildChunk",
        back_populates="parent_chunk",
        cascade="all, delete-orphan",
        order_by="DocumentChildChunk.chunk_index"
    )


class DocumentChildChunk(Base, UUIDMixin, TimestampMixin):
    """Bảng lưu trữ các đoạn văn bản nhỏ (Child Chunks) dùng để sinh Embedding và tra cứu FAISS."""
    __tablename__ = "document_child_chunks"

    parent_chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("document_parent_chunks.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        index=True,
        nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # FAISS IndexIDMap2 dùng ID int64; cột phải là BIGINT để chứa đủ miền sinh ID.
    faiss_id: Mapped[Optional[int]] = mapped_column(BigInteger, index=True, nullable=True)

    # Relationships
    parent_chunk: Mapped["DocumentParentChunk"] = relationship("DocumentParentChunk", back_populates="child_chunks")
    document: Mapped["Document"] = relationship("Document", back_populates="child_chunks")
