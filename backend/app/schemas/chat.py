"""
MÔ TẢ KIẾN TRÚC:
Pydantic Schemas phục vụ yêu cầu hỏi đáp RAG (Chat Query) và trích dẫn nguồn (Citations).
"""

import uuid
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict, field_validator
from app.schemas.search import SearchResultItem


class ChatCitation(BaseModel):
    """Schema thông tin một nguồn trích dẫn chứng cứ."""
    model_config = ConfigDict(from_attributes=True)

    document_id: uuid.UUID
    document_title: str
    heading: Optional[str] = None
    child_chunk_id: uuid.UUID
    score: float


class ChatQueryRequest(BaseModel):
    """Schema yêu cầu hỏi đáp tri thức qua RAG."""
    query: str = Field(..., min_length=2, max_length=2000, description="Câu hỏi của người dùng")
    top_k: int = Field(3, ge=1, le=10, description="Số lượng phân đoạn cần tra cứu (mặc định 3 để tối ưu 16K TPM)")
    max_context_tokens: int = Field(550, ge=100, le=1500, description="Trần token tối đa cho phần ngữ cảnh đã nén gửi tới LLM")
    department_id: Optional[uuid.UUID] = Field(None, description="Lọc theo phòng ban")
    session_id: Optional[uuid.UUID] = Field(
        None,
        description="ID phiên hội thoại do client giữ; bỏ trống thì server tạo mới mỗi lượt",
    )
    mode: str = Field(
        "hybrid",
        description="Chế độ suy luận: 'hybrid' (mặc định: gọi Cloud LLM thật kèm fallback an toàn), 'real', 'mock', 'retrieval_only'"
    )

    @field_validator("query")
    @classmethod
    def validate_non_whitespace_query(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 2:
            raise ValueError("Câu hỏi phải có ít nhất 2 ký tự không phải khoảng trắng")
        return stripped


class ChatQueryResponse(BaseModel):
    """Schema phản hồi câu trả lời hoàn chỉnh kèm chunks đã truy xuất, trích dẫn và thống kê nén token."""
    answer: str
    query: str
    session_id: uuid.UUID
    retrieved_chunks: List[SearchResultItem] = []
    citations: List[ChatCitation]
    tokens_used: int
    compression_stats: Dict[str, Any]
    mode: str


class ChatHistoryTurn(BaseModel):
    """Một lượt hội thoại đã lưu, đủ trường để xuất dataset RAGAS."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    query: str
    answer: str
    contexts: List[Dict[str, Any]]
    citations: List[Dict[str, Any]]
    chunk_ids: List[str]
    model_name: Optional[str] = None
    mode: Optional[str] = None
    latency_ms: float
    ttfb_ms: Optional[float] = None
    input_tokens: int
    compressed_tokens: int
    output_tokens: int
    compression_savings_percent: float
    feedback_rating: Optional[str] = None
    created_at: Any


class ChatHistoryResponse(BaseModel):
    """Danh sách lượt hội thoại gần nhất của chính người dùng hiện tại."""
    total: int
    turns: List[ChatHistoryTurn]


class ChatFeedbackCreate(BaseModel):
    """Schema gửi đánh giá câu trả lời (Thumbs Up/Down)."""
    query: str = Field(..., description="Câu hỏi ban đầu")
    answer: str = Field(..., description="Câu trả lời của AI")
    rating: str = Field(..., pattern=r"^(THUMBS_UP|THUMBS_DOWN)$", description="'THUMBS_UP' hoặc 'THUMBS_DOWN'")
    comment: Optional[str] = Field(None, max_length=1000, description="Ý kiến đóng góp thêm")


class ChatFeedbackResponse(BaseModel):
    """Schema phản hồi kết quả lưu đánh giá."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    rating: str
    created_at: Any
