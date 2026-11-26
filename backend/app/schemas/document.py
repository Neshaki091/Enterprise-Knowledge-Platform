"""
MÔ TẢ KIẾN TRÚC:
Pydantic Schemas phục vụ quản lý Tài liệu và các đoạn văn bản phân cấp (Hierarchical Chunks).
"""

import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class ChildChunkResponse(BaseModel):
    """Schema thông tin Child Chunk."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_chunk_id: uuid.UUID
    chunk_index: int
    content: str
    token_count: int
    faiss_id: Optional[int] = None


class ParentChunkResponse(BaseModel):
    """Schema thông tin Parent Chunk."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chunk_index: int
    heading: Optional[str] = None
    content: str
    token_count: int
    child_chunks: List[ChildChunkResponse] = []


class DocumentResponse(BaseModel):
    """Schema tóm tắt thông tin tài liệu đã nạp."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    department_id: Optional[uuid.UUID] = None
    title: str
    file_name: str
    file_type: str
    file_size_bytes: int
    file_hash: str
    token_count: int
    status: str
    created_at: datetime
    parent_chunk_count: int = 0
    child_chunk_count: int = 0


class DocumentDetailResponse(DocumentResponse):
    """Schema chi tiết tài liệu kèm văn bản thô và danh sách Parent Chunks."""
    raw_text: str
    doc_metadata: Dict[str, Any] = {}
    parent_chunks: List[ParentChunkResponse] = []
