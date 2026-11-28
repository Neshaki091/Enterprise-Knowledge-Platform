"""
MÔ TẢ KIẾN TRÚC:
Router Endpoints cho Hỏi đáp Tri thức RAG (Chat Endpoints).
Được bảo vệ bởi TenantContext (SEC-004), Rate Limiting (Token Bucket), và Grounding Rules (SEC-006).
"""

import json
import logging
import uuid
import zlib
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.context import get_current_tenant_context, TenantContext
from app.models.feedback import ChatFeedback
from app.models.chat_history import ChatTurn
from app.schemas.chat import (
    ChatQueryRequest,
    ChatQueryResponse,
    ChatFeedbackCreate,
    ChatFeedbackResponse,
    ChatHistoryResponse,
)
from app.services.chat_service import ChatService
from app.services.streaming_service import StreamingChatService
from app.services.chat_history_service import ChatHistoryService
from app.services.telemetry_service import TelemetryService

router = APIRouter()
logger = logging.getLogger(__name__)


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


@router.get(
    "/export",
    summary="Xuất JSONL.GZ các lượt chat của tenant theo tháng để chấm RAGAS bên ngoài",
)
async def export_chat_traces(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="Tháng UTC, định dạng YYYY-MM"),
    include_mock: bool = Query(
        False,
        description="Bao gồm lượt mock/retrieval_only; mặc định loại bỏ để RAGAS chỉ chấm câu trả lời thật.",
    ),
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """
    Chỉ OWNER/ADMIN (hoặc superuser) được xuất dữ liệu của tenant hiện tại.

    Endpoint chỉ trả dữ liệu thô (query/answer/context/chunk IDs); không gọi RAGAS.
    Stream được nén gzip ngay trong lúc đọc DB để giảm băng thông tải về (egress VPS).
    File chứa nội dung hội thoại nên response được đánh dấu attachment + no-store.
    """
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ OWNER/ADMIN được xuất dữ liệu hội thoại.",
        )

    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tháng không hợp lệ; dùng định dạng YYYY-MM.",
        ) from exc
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)

    await TelemetryService.record_event(
        db,
        event_type="ragas_export",
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        status_code=200,
        details={"month": month, "include_mock": include_mock, "compression": "gzip"},
        persist=True,
    )
    await db.commit()

    async def generate_gzip() -> AsyncGenerator[bytes, None]:
        # zlib wbits=31 xuất định dạng gzip; nén từng batch để không nạp cả tháng vào RAM.
        compressor = zlib.compressobj(6, zlib.DEFLATED, wbits=31)
        offset = 0
        while True:
            batch = await ChatHistoryService.list_export_batch(
                db,
                tenant_id=context.tenant_id,
                start=start,
                end=end,
                offset=offset,
                include_mock=include_mock,
            )
            if not batch:
                break
            payload = "".join(
                json.dumps(ChatHistoryService.to_ragas_record(turn), ensure_ascii=False) + "\n"
                for turn in batch
            )
            chunk = compressor.compress(payload.encode("utf-8"))
            if chunk:
                yield chunk
            if len(batch) < 200:
                break
            offset += len(batch)
        tail = compressor.flush()
        if tail:
            yield tail

    filename = f"ragas_{context.tenant_id}_{month}.jsonl.gz"
    return StreamingResponse(
        generate_gzip(),
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "/history",
    response_model=ChatHistoryResponse,
    summary="Lịch sử các lượt hỏi-đáp của chính người dùng (phục vụ kiểm tra và RAGAS)",
)
async def get_chat_history(
    limit: int = Query(50, ge=1, le=200),
    session_id: Optional[uuid.UUID] = None,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    """Trả các lượt gần nhất của user hiện tại; RLS tenant vẫn được áp ở tầng DB."""
    turns = await ChatHistoryService.list_turns(
        db,
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        session_id=session_id,
        limit=limit,
    )
    return ChatHistoryResponse(total=len(turns), turns=turns)


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
    # Gắn rating vào lượt chat gần nhất khớp query/answer để dataset RAGAS có nhãn feedback.
    # Best-effort: lỗi trace không được làm hỏng việc gửi feedback.
    try:
        latest_turn_id = (
            select(ChatTurn.id)
            .where(
                ChatTurn.tenant_id == context.tenant_id,
                ChatTurn.user_id == context.user_id,
                ChatTurn.query == data.query,
                func.trim(ChatTurn.answer) == data.answer.strip(),
            )
            .order_by(ChatTurn.created_at.desc())
            .limit(1)
            .scalar_subquery()
        )
        async with db.begin_nested():
            await db.execute(
                update(ChatTurn)
                .where(ChatTurn.id == latest_turn_id)
                .values(feedback_rating=data.rating)
            )
    except SQLAlchemyError as exc:
        logger.warning("Không cập nhật được feedback_rating cho chat_turns: %s", exc)

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
