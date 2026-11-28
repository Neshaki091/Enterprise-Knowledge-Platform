"""Service lưu và truy vấn trace hội thoại RAG phục vụ lịch sử + RAGAS."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat_history import ChatTurn
from app.services.chunking_service import estimate_tokens

logger = logging.getLogger(__name__)


class ChatHistoryService:
    """Ghi một lượt Q/A kèm snapshot context; lỗi trace không được làm hỏng chat."""

    @staticmethod
    async def record_turn(
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: Optional[uuid.UUID],
        session_id: uuid.UUID,
        query: str,
        answer: str,
        contexts: Optional[List[Dict[str, Any]]] = None,
        citations: Optional[List[Dict[str, Any]]] = None,
        chunk_ids: Optional[List[str]] = None,
        llm_context: Optional[str] = None,
        model_name: Optional[str] = None,
        mode: Optional[str] = None,
        latency_ms: float = 0.0,
        ttfb_ms: Optional[float] = None,
        input_tokens: int = 0,
        compressed_tokens: int = 0,
        output_tokens: int = 0,
        compression_savings_percent: float = 0.0,
    ) -> Optional[ChatTurn]:
        """Lưu lượt chat; dùng savepoint để lỗi trace không rollback nghiệp vụ chat."""
        if not settings.CHAT_HISTORY_ENABLED or not (query or "").strip() or not (answer or "").strip():
            return None

        turn = ChatTurn(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            query=query,
            answer=answer,
            contexts=contexts or [],
            citations=citations or [],
            chunk_ids=chunk_ids or [],
            llm_context=llm_context,
            model_name=model_name,
            mode=mode,
            latency_ms=round(float(latency_ms), 2),
            ttfb_ms=round(float(ttfb_ms), 2) if ttfb_ms is not None else None,
            input_tokens=max(0, int(input_tokens)),
            compressed_tokens=max(0, int(compressed_tokens)),
            output_tokens=max(0, int(output_tokens)),
            compression_savings_percent=round(float(compression_savings_percent), 2),
        )
        try:
            async with db.begin_nested():
                db.add(turn)
                await db.flush()
            return turn
        except SQLAlchemyError as exc:
            logger.warning("Không thể lưu chat_turns (bỏ qua trace): %s", exc)
            return None

    @staticmethod
    async def list_turns(
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: Optional[uuid.UUID] = None,
        limit: int = 50,
    ) -> List[ChatTurn]:
        """Lịch sử của chính người dùng; RLS vẫn là lớp bảo vệ thứ hai."""
        stmt = select(ChatTurn).where(
            ChatTurn.tenant_id == tenant_id,
            ChatTurn.user_id == user_id,
        )
        if session_id is not None:
            stmt = stmt.where(ChatTurn.session_id == session_id)
        stmt = stmt.order_by(ChatTurn.created_at.desc()).limit(max(1, min(limit, 200)))
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_recent_turns(
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        limit: int = 3,
    ) -> List[ChatTurn]:
        """Lấy tối đa N lượt gần nhất của phiên, trả theo thứ tự thời gian tăng dần."""
        stmt = (
            select(ChatTurn)
            .where(
                ChatTurn.tenant_id == tenant_id,
                ChatTurn.user_id == user_id,
                ChatTurn.session_id == session_id,
                ChatTurn.answer != "",
            )
            .order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc())
            .limit(max(1, min(limit, 10)))
        )
        rows = list((await db.execute(stmt)).scalars().all())
        rows.reverse()
        return rows

    @staticmethod
    def format_for_prompt(turns: List[ChatTurn], max_tokens: int) -> str:
        """
        Ghép các lượt cũ thành block hội thoại ngắn cho prompt.

        Lịch sử chỉ giúp hiểu câu hỏi nối tiếp; không phải nguồn chứng cứ.
        Cắt bớt theo token budget để không làm phình 16K TPM.
        """
        if not turns or max_tokens <= 0:
            return ""
        blocks: List[str] = []
        for turn in reversed(turns):
            query = (turn.query or "").strip()
            answer = (turn.answer or "").strip()
            if not query or not answer:
                continue
            answer = answer[:1200]
            block = f"Người dùng: {query}\nTrợ lý: {answer}"
            candidate = "\n\n".join([block] + blocks)
            if estimate_tokens(candidate) > max_tokens:
                if not blocks:
                    # Một lượt quá dài: cắt bớt để vẫn giữ được ngữ cảnh gần nhất.
                    block = block[: max(80, max_tokens * 4)]
                else:
                    break
            blocks.insert(0, block)
        return "\n\n".join(blocks)

    @staticmethod
    async def delete_latest_turn(
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> Optional[uuid.UUID]:
        """Xoá lượt gần nhất của phiên; trả id đã xoá hoặc None."""
        stmt = (
            select(ChatTurn.id)
            .where(
                ChatTurn.tenant_id == tenant_id,
                ChatTurn.user_id == user_id,
                ChatTurn.session_id == session_id,
            )
            .order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc())
            .limit(1)
        )
        turn_id = (await db.execute(stmt)).scalar_one_or_none()
        if turn_id is None:
            return None
        result = await db.execute(
            delete(ChatTurn).where(
                ChatTurn.id == turn_id,
                ChatTurn.tenant_id == tenant_id,
                ChatTurn.user_id == user_id,
            )
        )
        return turn_id if (result.rowcount or 0) > 0 else None

    @staticmethod
    async def list_export_batch(
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        start: datetime,
        end: datetime,
        offset: int,
        limit: int = 200,
        include_mock: bool = False,
    ) -> List[ChatTurn]:
        """Đọc từng batch cho export JSONL, tránh nạp cả tháng vào RAM."""
        stmt = (
            select(ChatTurn)
            .where(
                ChatTurn.tenant_id == tenant_id,
                ChatTurn.created_at >= start,
                ChatTurn.created_at < end,
                ChatTurn.answer != "",
            )
            .order_by(ChatTurn.created_at.asc(), ChatTurn.id.asc())
            .limit(max(1, min(limit, 500)))
            .offset(max(0, offset))
        )
        if not include_mock:
            # RAGAS phải chấm câu trả lời thật; mock/retrieval_only chỉ dùng để test.
            stmt = stmt.where(
                or_(
                    ChatTurn.mode.is_(None),
                    ChatTurn.mode.notin_(("mock", "retrieval_only")),
                )
            )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def to_ragas_record(turn: ChatTurn) -> Dict[str, Any]:
        """Chuyển ChatTurn sang đúng schema dữ liệu RAGAS; không chấm điểm tại đây."""
        contexts = turn.contexts or []
        context_texts: List[str] = []
        for item in contexts:
            if isinstance(item, dict):
                text = item.get("text") or item.get("parent_content") or item.get("child_content") or ""
            else:
                text = item
            if str(text).strip():
                context_texts.append(str(text).strip())

        return {
            "user_input": turn.query,
            "response": turn.answer,
            "retrieved_contexts": context_texts,
            "reference": None,
            "metadata": {
                "turn_id": str(turn.id),
                "session_id": str(turn.session_id),
                "tenant_id": str(turn.tenant_id),
                "user_id": str(turn.user_id) if turn.user_id else None,
                "chunk_ids": turn.chunk_ids or [],
                "citations": turn.citations or [],
                "model": turn.model_name,
                "mode": turn.mode,
                "created_at": turn.created_at.isoformat() if turn.created_at else None,
                "llm_context": turn.llm_context,
                "retrieved_context_count": len(context_texts),
                "latency_ms": turn.latency_ms,
                "ttfb_ms": turn.ttfb_ms,
                "tokens": {
                    "input": turn.input_tokens,
                    "compressed": turn.compressed_tokens,
                    "output": turn.output_tokens,
                    "compression_savings_percent": turn.compression_savings_percent,
                },
                "feedback_rating": turn.feedback_rating,
            },
        }

    @staticmethod
    async def prune_expired(db: AsyncSession, retention_days: Optional[int] = None) -> int:
        """Xoá trace cũ hơn retention (tối đa 365 ngày); dùng cho cron/CLI, không chạy trong request."""
        days = retention_days if retention_days is not None else settings.CHAT_HISTORY_RETENTION_DAYS
        days = max(1, min(int(days), 365))
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        result = await db.execute(delete(ChatTurn).where(ChatTurn.created_at < cutoff))
        return int(result.rowcount or 0)