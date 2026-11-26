"""
MÔ TẢ KIẾN TRÚC:
Pydantic Schemas phục vụ yêu cầu và kết quả của Tìm kiếm lai (Hybrid Search).
"""

import uuid
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, field_validator


class SearchRequest(BaseModel):
    """Schema yêu cầu tìm kiếm tri thức."""
    query: str = Field(..., min_length=2, max_length=1000, description="Câu hỏi hoặc từ khóa tìm kiếm")
    top_k: int = Field(5, ge=1, le=50, description="Số lượng kết quả cần trả về")
    department_id: Optional[uuid.UUID] = Field(None, description="Lọc theo phòng ban (nếu có)")
    dense_weight: float = Field(0.6, ge=0.0, le=1.0, description="Trọng số tìm kiếm ngữ nghĩa FAISS")
    sparse_weight: float = Field(0.4, ge=0.0, le=1.0, description="Trọng số tìm kiếm từ khóa FTS")

    @field_validator("query")
    @classmethod
    def validate_non_whitespace_query(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 2:
            raise ValueError("Truy vấn tìm kiếm phải có ít nhất 2 ký tự không phải khoảng trắng")
        return stripped


class SearchResultItem(BaseModel):
    """Schema một ứng viên tri thức được tìm thấy."""
    model_config = ConfigDict(from_attributes=True)

    child_chunk_id: uuid.UUID
    parent_chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    heading: Optional[str] = None
    child_content: str
    parent_content: str
    rrf_score: float
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None


class SearchResponse(BaseModel):
    """Schema phản hồi kết quả tìm kiếm kết hợp."""
    query: str
    total_found: int
    results: List[SearchResultItem]
