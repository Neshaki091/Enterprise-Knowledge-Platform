"""
MÔ TẢ KIẾN TRÚC:
Router Endpoints cho Hỏi đáp Tri thức RAG (Chat Endpoints).
Được bảo vệ bởi TenantContext (SEC-004), Rate Limiting (Token Bucket), và Grounding Rules (SEC-006).
"""

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.context import get_current_tenant_context, TenantContext
from app.models.feedback import ChatFeedback
from app.schemas.chat import (
    ChatQueryRequest,
    ChatQueryResponse,
    ChatFeedbackCreate,
    ChatFeedbackResponse,
)
from app.services.chat_service import ChatService
from app.services.streaming_service import StreamingChatService

router = APIRouter()


@router.post(
    "/query",
    response_model=ChatQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Hỏi đáp tri thức doanh nghiệp qua RAG với Context Filtering và Trích dẫn nguồn"
)
async def query_knowledge_base(
    request: ChatQueryRequest,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """
    Quy trình xử lý RAG hoàn chỉnh:
    - Kiểm soát hạn mức 30 RPM / 16K TPM qua Token Bucket in-memory.
    - Tìm kiếm lai (FAISS mmap + FTS).
    - Lọc ngữ cảnh 3 tầng giảm 70% token.
    - Sinh câu trả lời có trích dẫn nguồn minh bạch.
    """
    return await ChatService.process_query(db, context, request)


@router.post(
    "/stream",
    summary="Truyền luồng câu trả lời thời gian thực qua Server-Sent Events (SSE)"
)
async def stream_chat_endpoint(
    request: ChatQueryRequest,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """
    Truyền luồng SSE với TTFT < 1.5 giây:
    - Event 1: 'citations' - Danh sách tài liệu và phân đoạn tham chiếu.
    - Event 2: 'token' - Từng từ của câu trả lời.
    - Event 3: 'done' - Tỷ lệ nén và hoàn tất.
    """
    # Kiểm tra quota trước khi mở stream để trả đúng HTTP 429 + Retry-After
    reserved_tokens = await StreamingChatService.reserve_quota(db, context, request)
    generator = StreamingChatService.stream_response(
        db,
        context,
        request,
        reserved_tokens=reserved_tokens,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post(
    "/feedback",
    response_model=ChatFeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Gửi đánh giá chất lượng câu trả lời của AI (Thumbs Up / Thumbs Down)"
)
async def submit_feedback(
    data: ChatFeedbackCreate,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Lưu phản hồi đánh giá kèm lý do để đo lường độ chính xác và cải tiến hệ thống."""
    feedback = ChatFeedback(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        query=data.query,
        answer=data.answer,
        rating=data.rating,
        comment=data.comment
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return feedback
