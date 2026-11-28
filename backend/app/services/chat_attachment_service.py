"""
MÔ TẢ KIẾN TRÚC:
Service quản lý tài liệu đính kèm TRONG PHIÊN CHAT (Ephemeral Chat Attachment).

Khác với DocumentService:
- KHÔNG ghi vào kho tri thức tenant và KHÔNG ghi FAISS.
- Chỉ owner + session_id được đọc; vector lưu BYTEA trong PostgreSQL và so khớp
  trong phạm vi vài trăm chunk của chính session (không ảnh hưởng index chung).
- Có TTL ngắn; file gốc bị hủy ngay sau khi parse (Zero Raw Storage).

Luồng ingest: SHA-256 -> parse -> hierarchical chunk -> embedding -> lưu DB.
Luồng query: embedding câu hỏi dùng chung với SearchService -> cosine trên chunk
của session -> ContextFilterPipeline nén trước khi đưa vào prompt.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import numpy as np
from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.chat_attachment import ChatAttachment, ChatAttachmentChunk
from app.schemas.search import SearchResultItem
from app.services.chunking_service import estimate_tokens, hierarchical_chunk
from app.services.context_filter import ContextFilterPipeline
from app.services.embedding_service import EmbeddingService
from app.services.parsers import calculate_sha256, extract_document_content

logger = logging.getLogger(__name__)


class ChatAttachmentService:
    """Nạp, tìm kiếm và dọn dẹp tài liệu đính kèm riêng của một phiên chat."""

    # Semaphore theo event loop: uvicorn chỉ có 1 loop, nhưng test tạo loop mới mỗi case.
    _upload_semaphore: Optional[asyncio.Semaphore] = None
    _upload_semaphore_loop: Optional[asyncio.AbstractEventLoop] = None

    @classmethod
    def _get_upload_semaphore(cls) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if cls._upload_semaphore is None or cls._upload_semaphore_loop is not loop:
            limit = max(1, int(settings.CHAT_ATTACHMENT_MAX_CONCURRENT_UPLOADS))
            cls._upload_semaphore = asyncio.Semaphore(limit)
            cls._upload_semaphore_loop = loop
        return cls._upload_semaphore

    @classmethod
    async def acquire_upload_slot(cls) -> None:
        """
        Giới hạn số file được đọc/parse/embed song song.

        Phải gọi TRƯỚC khi đọc bytes từ UploadFile để 10 request lớn không cùng
        nằm trong RAM; phần còn lại xếp hàng thay vì OOM backend 350 MB.
        """
        await cls._get_upload_semaphore().acquire()

    @classmethod
    def release_upload_slot(cls) -> None:
        """Trả slot sau khi upload thành công/lỗi; bỏ qua nếu chưa từng acquire."""
        semaphore = cls._upload_semaphore
        if semaphore is not None:
            try:
                semaphore.release()
            except ValueError:
                # release thừa không được làm hỏng request.
                pass

    # ------------------------------------------------------------------ helpers
    @classmethod
    def _now(cls) -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def _owner_filter(cls, tenant_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID):
        """Điều kiện bắt buộc cho mọi truy vấn attachment: tenant + owner + session + còn hạn."""
        return (
            ChatAttachment.tenant_id == tenant_id,
            ChatAttachment.user_id == user_id,
            ChatAttachment.session_id == session_id,
            ChatAttachment.expires_at > cls._now(),
        )

    @classmethod
    async def _delete_expired_session(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> int:
        """Dọn attachment hết hạn của chính session trước khi upload (tránh đụng unique hash)."""
        expired_ids = (
            select(ChatAttachment.id)
            .where(
                ChatAttachment.tenant_id == tenant_id,
                ChatAttachment.user_id == user_id,
                ChatAttachment.session_id == session_id,
                ChatAttachment.expires_at <= cls._now(),
            )
            .scalar_subquery()
        )
        await db.execute(
            delete(ChatAttachmentChunk).where(ChatAttachmentChunk.attachment_id.in_(expired_ids))
        )
        result = await db.execute(
            delete(ChatAttachment).where(
                ChatAttachment.tenant_id == tenant_id,
                ChatAttachment.user_id == user_id,
                ChatAttachment.session_id == session_id,
                ChatAttachment.expires_at <= cls._now(),
            )
        )
        return int(result.rowcount or 0)

    @classmethod
    async def list_active(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> List[ChatAttachment]:
        """Liệt kê file đính kèm còn hạn của đúng owner + session."""
        stmt = (
            select(ChatAttachment)
            .where(*cls._owner_filter(tenant_id, user_id, session_id))
            .order_by(ChatAttachment.created_at.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    @classmethod
    async def has_active(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> bool:
        """Kiểm tra nhanh session có attachment còn hạn hay không (dùng để reserve TPM)."""
        stmt = (
            select(func.count(ChatAttachment.id))
            .where(*cls._owner_filter(tenant_id, user_id, session_id))
        )
        return int((await db.execute(stmt)).scalar_one() or 0) > 0

    @classmethod
    async def get_owned(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        attachment_id: uuid.UUID,
    ) -> Optional[ChatAttachment]:
        """Lấy attachment theo owner; không cho phép admin đọc file private của nhân viên."""
        stmt = select(ChatAttachment).where(
            ChatAttachment.id == attachment_id,
            ChatAttachment.tenant_id == tenant_id,
            ChatAttachment.user_id == user_id,
            ChatAttachment.expires_at > cls._now(),
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------ ingest
    @classmethod
    async def ingest(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        file_name: str,
        file_bytes: bytes,
        content_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> ChatAttachment:
        """
        Nạp một file đính kèm chat.

        Raises:
            HTTPException 400/403/409/413 nếu file rỗng, vượt hạn mức, trùng hash
            hoặc quá nhiều chunk.
        """
        if not settings.CHAT_ATTACHMENT_ENABLED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tính năng tài liệu đính kèm trong chat đang tắt.",
            )

        max_bytes = int(settings.CHAT_ATTACHMENT_MAX_SIZE_BYTES)
        if not file_bytes:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tập tin đính kèm rỗng")
        if len(file_bytes) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Tập tin đính kèm vượt quá giới hạn {max_bytes // (1024 * 1024)} MB của chat.",
            )

        await cls._delete_expired_session(db, tenant_id, user_id, session_id)

        active_count = int(
            (
                await db.execute(
                    select(func.count(ChatAttachment.id)).where(
                        *cls._owner_filter(tenant_id, user_id, session_id)
                    )
                )
            ).scalar_one()
            or 0
        )
        if active_count >= int(settings.CHAT_ATTACHMENT_MAX_FILES):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Mỗi phiên chat chỉ được đính kèm tối đa {settings.CHAT_ATTACHMENT_MAX_FILES} file. "
                    "Hãy xoá bớt file cũ hoặc mở cuộc trò chuyện mới."
                ),
            )

        # Chặn lạm dụng xuyên phiên: session_id do client sinh nên phải giới hạn theo user.
        user_active_count = int(
            (
                await db.execute(
                    select(func.count(ChatAttachment.id)).where(
                        ChatAttachment.tenant_id == tenant_id,
                        ChatAttachment.user_id == user_id,
                        ChatAttachment.expires_at > cls._now(),
                    )
                )
            ).scalar_one()
            or 0
        )
        if user_active_count >= int(settings.CHAT_ATTACHMENT_MAX_ACTIVE_PER_USER):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Bạn đang có tối đa {settings.CHAT_ATTACHMENT_MAX_ACTIVE_PER_USER} file đính kèm "
                    "còn hạn. Hãy xoá bớt hoặc chờ file cũ hết hạn."
                ),
            )

        file_name = (file_name or "unknown_file")[:255]
        file_size = len(file_bytes)
        file_hash = await asyncio.to_thread(calculate_sha256, file_bytes)

        duplicate = (
            await db.execute(
                select(ChatAttachment.id).where(
                    *cls._owner_filter(tenant_id, user_id, session_id),
                    ChatAttachment.file_hash == file_hash,
                )
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"File '{file_name}' đã được đính kèm trong phiên chat này.",
            )

        raw_text, _meta = await asyncio.to_thread(
            extract_document_content, file_name, file_bytes, content_type
        )
        if not raw_text.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Không thể trích xuất văn bản từ '{file_name}' hoặc file không có nội dung chữ.",
            )
        # Zero Raw Storage: buffer nhị phân không được giữ lại sau parse.
        del file_bytes

        # Chặn file text quá lớn trước khi chunk/embed để không đốt CPU/RAM e2-micro.
        if len(raw_text) > 400_000:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    "Tài liệu đính kèm quá dài cho chat (trên ~400.000 ký tự). "
                    "Hãy tải vào Knowledge Hub thay vì đính kèm trong phiên chat."
                ),
            )

        parent_chunks = await asyncio.to_thread(hierarchical_chunk, raw_text)
        flat_children: List[Tuple[Optional[str], str, int]] = []
        for parent in parent_chunks:
            for child in parent.children:
                flat_children.append((parent.heading, child.content, child.token_count))

        max_chunks = int(settings.CHAT_ATTACHMENT_MAX_CHUNKS)
        if len(flat_children) > max_chunks:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"Tài liệu tạo ra {len(flat_children)} phân đoạn, vượt giới hạn {max_chunks} "
                    "của chat. Hãy tải tài liệu dài vào Knowledge Hub thay vì đính kèm trong chat."
                ),
            )
        if not flat_children:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Tài liệu '{file_name}' không có nội dung đủ dài để phân đoạn.",
            )

        display_title = ((title or "").strip() or file_name)[:255]
        embed_texts = [
            f"[Tài liệu đính kèm: {display_title} | Phân mục: {heading or 'Tổng quát'}]\n{content}"
            for heading, content, _tokens in flat_children
        ]
        embeddings = await EmbeddingService.get_embeddings(
            embed_texts,
            task_type="RETRIEVAL_DOCUMENT",
        )
        if len(embeddings) != len(flat_children):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Embedding trả về không khớp số phân đoạn của tài liệu đính kèm.",
            )

        attachment = ChatAttachment(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            file_name=file_name,
            file_type=(content_type or "application/octet-stream")[:100],
            file_size_bytes=file_size,
            file_hash=file_hash,
            title=display_title,
            token_count=sum(tokens for _h, _c, tokens in flat_children),
            chunk_count=len(flat_children),
            status="PROCESSED",
            expires_at=cls._now() + timedelta(hours=int(settings.CHAT_ATTACHMENT_TTL_HOURS)),
        )
        db.add(attachment)
        await db.flush()

        for index, ((heading, content, tokens), vector) in enumerate(zip(flat_children, embeddings)):
            db.add(
                ChatAttachmentChunk(
                    attachment_id=attachment.id,
                    tenant_id=tenant_id,
                    chunk_index=index,
                    heading=heading,
                    content=content,
                    token_count=tokens,
                    embedding=np.asarray(vector, dtype=np.float32).tobytes(),
                )
            )

        await db.commit()
        await db.refresh(attachment)
        return attachment

    # ------------------------------------------------------------------ delete
    @classmethod
    async def delete_owned(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        attachment_id: uuid.UUID,
    ) -> bool:
        """Xoá cứng file đính kèm + chunks; chỉ owner mới được xoá."""
        attachment = await cls.get_owned(
            db, tenant_id=tenant_id, user_id=user_id, attachment_id=attachment_id
        )
        if attachment is None:
            return False
        # Bulk delete để không kích hoạt lazy-load quan hệ trong AsyncSession.
        await db.execute(
            delete(ChatAttachmentChunk).where(
                ChatAttachmentChunk.attachment_id == attachment.id,
                ChatAttachmentChunk.tenant_id == tenant_id,
            )
        )
        await db.execute(
            delete(ChatAttachment).where(
                ChatAttachment.id == attachment.id,
                ChatAttachment.tenant_id == tenant_id,
                ChatAttachment.user_id == user_id,
            )
        )
        await db.commit()
        return True

    # ------------------------------------------------------------------ search
    @classmethod
    def _search_sync(
        cls,
        rows: List[Tuple[ChatAttachmentChunk, str]],
        query_vector: np.ndarray,
        top_k: int,
        min_score: float,
    ) -> List[SearchResultItem]:
        """Cosine similarity trên đúng tập chunk của owner/session (CPU-bound, chạy trong thread)."""
        if not rows:
            return []

        query = np.asarray(query_vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if norm > 0:
            query = query / norm

        matrix = np.vstack(
            [np.frombuffer(bytes(chunk.embedding), dtype=np.float32) for chunk, _name in rows]
        )
        if matrix.shape[1] != query.shape[0]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Vector tài liệu đính kèm không khớp chiều với embedding câu hỏi.",
            )

        scores = matrix @ query
        order = np.argsort(scores)[::-1][: max(1, top_k)]

        results: List[SearchResultItem] = []
        for idx in order:
            score = float(scores[int(idx)])
            if score < min_score:
                continue
            chunk, file_name = rows[int(idx)]
            results.append(
                SearchResultItem(
                    child_chunk_id=chunk.id,
                    parent_chunk_id=chunk.id,
                    document_id=chunk.attachment_id,
                    document_title=f"Tài liệu đính kèm: {file_name}",
                    heading=chunk.heading,
                    child_content=chunk.content,
                    parent_content=chunk.content,
                    rrf_score=score,
                    dense_score=score,
                    sparse_score=None,
                )
            )
        return results

    @classmethod
    async def search(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        query_vector: np.ndarray,
        top_k: int = 3,
        min_score: float = 0.05,
    ) -> List[SearchResultItem]:
        """
        Tìm các chunk gần nhất trong file đính kèm của chính user/session.

        Không dùng FAISS: số chunk rất nhỏ (tối đa vài trăm) nên cosine in-process
        vừa nhanh vừa tránh làm bẩn index tenant hoặc tạo file index theo user.
        """
        if not settings.CHAT_ATTACHMENT_ENABLED:
            return []

        stmt = (
            select(ChatAttachmentChunk, ChatAttachment.file_name)
            .join(ChatAttachment, ChatAttachmentChunk.attachment_id == ChatAttachment.id)
            .where(
                ChatAttachmentChunk.tenant_id == tenant_id,
                ChatAttachment.tenant_id == tenant_id,
                ChatAttachment.user_id == user_id,
                ChatAttachment.session_id == session_id,
                ChatAttachment.expires_at > cls._now(),
            )
        )
        rows = [(row[0], row[1]) for row in (await db.execute(stmt)).all()]
        if not rows:
            return []
        return await asyncio.to_thread(
            cls._search_sync, rows, query_vector, max(1, min(top_k, 5)), min_score
        )

    @classmethod
    async def build_context(
        cls,
        query: str,
        hits: List[SearchResultItem],
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, dict]:
        """Nén các chunk đính kèm bằng đúng pipeline 3 tầng trước khi chèn vào prompt."""
        if not hits:
            return "", {
                "attachment_tokens": 0,
                "attachment_chunks": 0,
                "retained_chunk_ids": [],
                "attachment_context": "",
            }
        budget = int(max_tokens or settings.CHAT_ATTACHMENT_MAX_CONTEXT_TOKENS)
        context, stats = await asyncio.to_thread(
            ContextFilterPipeline.filter_and_compress,
            query=query,
            candidates=hits,
            max_chunks=2,
            max_context_tokens=budget,
        )
        return context, {
            "attachment_tokens": int(stats.get("compressed_tokens", 0)),
            "attachment_chunks": int(stats.get("retained_chunks", 0)),
            "retained_chunk_ids": stats.get("retained_chunk_ids", []),
            "attachment_context": context,
        }

    # ------------------------------------------------------------------ prune
    @classmethod
    async def prune_expired(
        cls,
        db: AsyncSession,
        *,
        tenant_id: Optional[uuid.UUID] = None,
        batch_size: int = 500,
    ) -> int:
        """Xoá attachment hết hạn theo batch; dùng cho cron/CLI trên VPS."""
        removed = 0
        while True:
            conditions = [ChatAttachment.expires_at <= cls._now()]
            if tenant_id is not None:
                conditions.append(ChatAttachment.tenant_id == tenant_id)
            ids = select(ChatAttachment.id).where(*conditions).limit(batch_size).scalar_subquery()
            chunk_result = await db.execute(
                delete(ChatAttachmentChunk).where(ChatAttachmentChunk.attachment_id.in_(ids))
            )
            result = await db.execute(delete(ChatAttachment).where(*conditions))
            count = int(result.rowcount or 0)
            await db.commit()
            removed += count
            if count < batch_size:
                break
            if count == 0 and (chunk_result.rowcount or 0) == 0:
                break
        return removed