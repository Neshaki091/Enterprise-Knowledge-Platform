"""Service lưu và truy vấn trace hội thoại RAG phục vụ lịch sử + RAGAS."""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat_history import ChatTurn
from app.services.chunking_service import estimate_tokens

logger = logging.getLogger(__name__)

_HISTORY_STOPWORDS: Set[str] = {
    "là", "của", "và", "có", "được", "trong", "về", "cho", "các", "những", "một",
    "không", "này", "đó", "khi", "nào", "gì", "sao", "với", "từ", "đến", "tại",
    "bởi", "theo", "bao", "nhiêu", "mấy", "hãy", "vui", "lòng", "biết", "thì",
    "the", "is", "are", "was", "were", "what", "how", "why", "when", "where",
    "in", "on", "at", "of", "to", "for", "and", "or", "a", "an", "by", "with",
}
_HISTORY_QUANT_HINTS = (
    "bao nhiêu", "mấy", "số lượng", "tối đa", "tối thiểu", "bao lâu",
    "khi nào", "hạn", "ngày", "giờ", "tháng", "năm", "%", "phần trăm",
)


def _clean_history_sentence(sentence: str) -> str:
    """Làm sạch Markdown cơ bản nhưng giữ nguyên số liệu/trích dẫn."""
    cleaned = re.sub(r"^\s{0,3}(?:#{1,6}|[-*•]+)\s+", "", sentence)
    cleaned = re.sub(r"\*{2,}|__|`+", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _history_terms(query: str) -> tuple[Set[str], Set[str], bool]:
    """Trích keyword, bigram và cờ câu hỏi số liệu từ câu hỏi hiện tại."""
    lower_q = query.lower()
    is_quant = any(hint in lower_q for hint in _HISTORY_QUANT_HINTS) or bool(re.search(r"\d+", lower_q))
    raw_tokens = [t for t in re.findall(r"[^\W_]+", lower_q) if len(t) >= 2 or t.isdigit()]
    content = [t for t in raw_tokens if t not in _HISTORY_STOPWORDS] or raw_tokens
    bigrams = {f"{raw_tokens[i]} {raw_tokens[i + 1]}" for i in range(len(raw_tokens) - 1)}
    return set(content), bigrams, is_quant


def _compress_history_answer(
    answer: str,
    query: str,
    max_sentences: int,
    seen: Set[str],
) -> str:
    """
    Nén câu trả lời cũ theo hướng extractive, query-aware.

    Luôn giữ câu đầu (thường chứa kết luận), sau đó chọn thêm tối đa
    `max_sentences - 1` câu khớp keyword/bigram/số liệu của câu hỏi hiện tại.
    Không gọi LLM, không làm mất số liệu quan trọng.
    """
    raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", (answer or "").strip())
    cleaned: List[str] = []
    for raw in raw_sentences:
        sentence = _clean_history_sentence(raw)
        if len(sentence) < 8:
            continue
        cleaned.append(sentence[:320])
    if not cleaned:
        return ""

    query_words, query_bigrams, is_quant = _history_terms(query)
    chosen: List[tuple[int, str]] = []
    local_seen: Set[str] = set()

    def _key(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().lower()

    # Câu đầu tiên luôn được giữ nếu chưa xuất hiện ở lượt trước.
    first_key = _key(cleaned[0])
    if first_key not in seen:
        chosen.append((0, cleaned[0]))
        local_seen.add(first_key)

    scored: List[tuple[int, str, float]] = []
    for idx, sentence in enumerate(cleaned[1:], start=1):
        key = _key(sentence)
        if key in seen or key in local_seen:
            continue
        words = set(re.findall(r"[^\W_]+", key))
        score = float(len(query_words.intersection(words)))
        score += 2.0 * sum(1 for bg in query_bigrams if bg in key)
        if is_quant and re.search(r"\d+", sentence):
            score += 1.0
        scored.append((idx, sentence, score))

    if query_words:
        ranked = sorted(
            [item for item in scored if item[2] > 0],
            key=lambda item: (item[2], -item[0]),
            reverse=True,
        )
    else:
        # Không có query (fallback cũ): giữ thêm câu kế tiếp để không mất mạch.
        ranked = sorted(scored, key=lambda item: -item[0])[:1]

    for idx, sentence, _score in ranked[: max(0, max_sentences - len(chosen))]:
        key = _key(sentence)
        if key in local_seen:
            continue
        chosen.append((idx, sentence))
        local_seen.add(key)

    if not chosen:
        return ""
    # Trả lại đúng thứ tự xuất hiện trong câu trả lời gốc.
    chosen.sort(key=lambda item: item[0])
    for _idx, sentence in chosen:
        seen.add(_key(sentence))
    return " ".join(sentence for _idx, sentence in chosen)


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
        session_memory: Optional[str] = None,
        has_attachment: bool = False,
    ) -> Optional[ChatTurn]:
        """Lưu lượt chat; dùng savepoint để lỗi trace không rollback nghiệp vụ chat."""
        if not settings.CHAT_HISTORY_ENABLED or not (query or "").strip() or not (answer or "").strip():
            return None

        memory_value = (session_memory or "").strip()
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
            session_memory=memory_value[:4000] if memory_value else None,
            has_attachment=bool(has_attachment),
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
    async def get_session_memory(
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> str:
        """
        Đọc session memory mới nhất của đúng owner/session.

        Memory được LLM trả về trong cùng call của lượt trước; rollback xoá lượt
        cuối nên lượt trước đó tự trở thành memory hiện hành.
        """
        stmt = (
            select(ChatTurn.session_memory)
            .where(
                ChatTurn.tenant_id == tenant_id,
                ChatTurn.user_id == user_id,
                ChatTurn.session_id == session_id,
                ChatTurn.session_memory.is_not(None),
                ChatTurn.session_memory != "",
            )
            .order_by(ChatTurn.created_at.desc(), ChatTurn.id.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none() or ""

    @staticmethod
    def format_for_prompt(
        turns: List[ChatTurn],
        max_tokens: int,
        query: str = "",
    ) -> str:
        """
        Fallback khi session chưa có LLM memory (session cũ/mock hoặc model lỗi).

        Nén extractive từng câu trả lời theo câu hỏi hiện tại: giữ câu đầu + tối đa
        2 câu khớp keyword/số liệu, dedup giữa các lượt, rồi ép ngân sách token.
        Nhờ đó 3 lượt thường vừa 500 token thay vì chỉ còn 1 lượt.
        """
        if not turns or max_tokens <= 0:
            return ""

        blocks: List[str] = []
        seen: Set[str] = set()
        for position, turn in enumerate(reversed(turns)):
            turn_query = (turn.query or "").strip()
            answer = (turn.answer or "").strip()
            if not turn_query or not answer:
                continue

            sentence_limit = 3 if position == 0 else 2
            block = ""
            for limit in (sentence_limit, 2, 1):
                compressed = _compress_history_answer(
                    answer,
                    query=query,
                    max_sentences=max(1, limit),
                    seen=set(seen),
                )
                if not compressed:
                    continue
                candidate_block = f"Người dùng: {turn_query}\nTrợ lý: {compressed}"
                if estimate_tokens("\n\n".join([candidate_block] + blocks)) <= max_tokens:
                    block = candidate_block
                    _compress_history_answer(
                        answer,
                        query=query,
                        max_sentences=max(1, limit),
                        seen=seen,
                    )
                    break

            if not block:
                if blocks:
                    break
                # Một lượt quá dài so với ngân sách: giữ phần đầu vừa đủ.
                compact = f"Người dùng: {turn_query}\nTrợ lý: {answer[:600]}"
                block = compact[: max(80, max_tokens * 4)]
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
                # Tuyệt đối không đưa lượt có tài liệu đính kèm private vào dataset chung.
                ChatTurn.has_attachment.is_(False),
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
                # Metadata phục vụ phân tích multi-turn; memory KHÔNG nằm trong retrieved_contexts.
                # Lượt private đã bị lọc khỏi export, nhưng vẫn che memory để phòng thủ nhiều lớp.
                "has_attachment": bool(turn.has_attachment),
                "session_memory": None if turn.has_attachment else turn.session_memory,
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