"""
MÔ TẢ KIẾN TRÚC:
Service Layer quản lý toàn bộ quy trình Nạp tài liệu (Document Ingestion) và Cắt đoạn phân cấp (Hierarchical Chunking).
Tuân thủ nghiêm ngặt:
- ADR-003: Zero Raw Storage - Hủy file nhị phân sau khi parse, chống lưu trữ dư thừa.
- SEC-003: Phân lập dữ liệu hoàn toàn theo tenant_id.
- SEC-004: Chặn BOLA - Mọi thao tác đều bắt buộc có tenant_id từ TenantContext.
"""

import logging
import time
import uuid
from typing import FrozenSet, Optional, List, Tuple
from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.access import apply_document_department_scope
from app.models.document import Document
from app.models.department import Department
from app.models.tenant import Tenant
from app.models.chunk import DocumentParentChunk, DocumentChildChunk
from app.services.parsers import calculate_sha256, extract_document_content
from app.services.chunking_service import hierarchical_chunk, estimate_tokens
from app.services.embedding_service import EmbeddingService
from app.services.faiss_service import FaissService
from app.services.telemetry_service import TelemetryService

logger = logging.getLogger(__name__)


class DocumentService:
    """Xử lý nạp tài liệu, băm SHA-256, phân đoạn Parent-Child và quản lý vòng đời tài liệu."""

    @staticmethod
    async def ingest_document(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        file_name: str,
        file_bytes: bytes,
        content_type: Optional[str] = None,
        title: Optional[str] = None,
        department_id: Optional[uuid.UUID] = None,
        allowed_department_ids: Optional[FrozenSet[uuid.UUID]] = None,
    ) -> Document:
        """
        Quy trình nạp tài liệu Ephemeral:
        1. Tính mã băm SHA-256 ngay trong RAM.
        2. Kiểm tra trùng lặp trong phạm vi Tenant (Conflict 409).
        3. Bóc tách văn bản sạch (raw_text).
        4. Giải phóng buffer nhị phân.
        5. Phân đoạn Hierarchical Chunking (Parent ~1000 tokens, Child ~200 tokens).
        6. Lưu vào PostgreSQL và trả về kết quả.
        """
        # 0. Nếu có department_id thì kiểm tra phòng ban thuộc đúng tenant_id (tránh lỗi FK trên Postgres & chặn BOLA)
        started = time.perf_counter()
        if department_id is not None:
            dept_check = await db.execute(
                select(Department).where(
                    Department.id == department_id,
                    Department.tenant_id == tenant_id,
                )
            )
            if not dept_check.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Phòng ban không tồn tại trong doanh nghiệp của bạn.",
                )

        # 0.1. Chặn ghi tài liệu sang phòng ban ngoài phạm vi (Department ACL)
        if allowed_department_ids is not None:
            if department_id is None or department_id not in allowed_department_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Bạn chỉ được tải tài liệu vào đúng phòng ban của mình.",
                )

        # 0.2. Thực thi hạn ngạch số lượng tài liệu của Tenant (quota_limits.max_documents)
        tenant = await db.get(Tenant, tenant_id)
        if tenant is not None:
            max_documents = int((tenant.quota_limits or {}).get("max_documents", 0) or 0)
            if max_documents > 0:
                current_documents = (await db.execute(
                    select(func.count(Document.id)).where(Document.tenant_id == tenant_id)
                )).scalar_one() or 0
                if int(current_documents) >= max_documents:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Doanh nghiệp đã đạt hạn ngạch tối đa {max_documents} tài liệu. "
                        "Vui lòng xóa bớt tài liệu cũ hoặc nâng cấp gói dịch vụ.",
                    )

        # 1. Tính mã băm SHA-256
        file_size = len(file_bytes)
        file_hash = calculate_sha256(file_bytes)

        # 2. Kiểm tra trùng lặp mã băm trong cùng Tenant
        dup_query = select(Document).where(
            Document.tenant_id == tenant_id,
            Document.file_hash == file_hash
        )
        dup_res = await db.execute(dup_query)
        if dup_res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tài liệu '{file_name}' đã tồn tại trong doanh nghiệp của bạn (Trùng mã băm SHA-256: {file_hash[:12]}...)",
            )

        # 3. Bóc tách văn bản sạch
        raw_text, meta = extract_document_content(file_name, file_bytes, content_type)
        if not raw_text.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Không thể trích xuất văn bản từ tài liệu '{file_name}' hoặc tài liệu không có nội dung chữ.",
            )

        # 4. Hủy buffer nhị phân khỏi RAM (Zero Raw Storage)
        del file_bytes

        total_tokens = estimate_tokens(raw_text)

        # 5. Khởi tạo Document Entity
        doc = Document(
            tenant_id=tenant_id,
            department_id=department_id,
            uploaded_by=user_id,
            title=title or file_name,
            file_name=file_name,
            file_type=content_type or "application/octet-stream",
            file_size_bytes=file_size,
            file_hash=file_hash,
            raw_text=raw_text,
            token_count=total_tokens,
            status="PROCESSED",
            doc_metadata=meta
        )
        db.add(doc)
        await db.flush()

        # 6. Thuật toán phân đoạn phân cấp (Hierarchical Chunking)
        parent_raw_list = hierarchical_chunk(raw_text)
        created_child_chunks: List[DocumentChildChunk] = []
        contextual_embed_texts: List[str] = []

        for p_raw in parent_raw_list:
            parent_chunk = DocumentParentChunk(
                document_id=doc.id,
                tenant_id=tenant_id,
                chunk_index=p_raw.index,
                content=p_raw.content,
                token_count=p_raw.token_count,
                heading=p_raw.heading
            )
            db.add(parent_chunk)
            await db.flush()

            heading_label = p_raw.heading or "Tổng quát"
            parent_summary = p_raw.content[:200].replace("\n", " ").strip()

            for c_raw in p_raw.children:
                faiss_id = abs(int(uuid.uuid4().int % (2**62)))
                child_chunk = DocumentChildChunk(
                    parent_chunk_id=parent_chunk.id,
                    document_id=doc.id,
                    tenant_id=tenant_id,
                    chunk_index=c_raw.index,
                    content=c_raw.content,
                    token_count=c_raw.token_count,
                    faiss_id=faiss_id
                )
                db.add(child_chunk)
                created_child_chunks.append(child_chunk)
                # Nhúng kết hợp ngữ cảnh Parent (tiêu đề + đoạn mở đầu) + Child để tìm kiếm chính xác cao nhất
                contextual_embed_texts.append(
                    f"[Tài liệu: {doc.title} | Phân mục: {heading_label} | Ngữ cảnh cha: {parent_summary}]\n{c_raw.content}"
                )

        await db.flush()

        # 7. Tự động batching (tối đa 10 chunks/call) sinh embedding và nạp vào FAISS mmap index
        faiss_ids = [c.faiss_id for c in created_child_chunks]
        if created_child_chunks:
            embeddings = await EmbeddingService.get_embeddings(
                contextual_embed_texts,
                task_type="RETRIEVAL_DOCUMENT",
            )
            await FaissService.add_vectors(tenant_id, embeddings, faiss_ids)

        # 8. Commit có bù trừ: mọi lỗi sau khi ghi FAISS đều phải xóa vector để tránh rác mồ côi
        try:
            await TelemetryService.record_event(
                db,
                event_type="document_upload",
                tenant_id=tenant_id,
                user_id=user_id,
                model_name=settings.EMBEDDING_MODEL if settings.EMBEDDING_API_URL else "offline_hash",
                status_code=201,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                input_tokens=total_tokens,
                storage_delta_bytes=len(raw_text.encode("utf-8")),
                details={
                    "file_type": content_type or "unknown",
                    "file_size_bytes": file_size,
                    "parent_chunks": len(parent_raw_list),
                    "child_chunks": len(created_child_chunks),
                },
                persist=True,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            if faiss_ids:
                try:
                    await FaissService.remove_vectors(tenant_id, faiss_ids)
                except Exception as cleanup_exc:  # pragma: no cover - phụ thuộc lỗi đĩa
                    logger.error("Không thể dọn vector FAISS sau khi rollback DB: %s", cleanup_exc)
            raise
            raise

        # Tải lại doc kèm eager loading quan hệ để tránh lỗi MissingGreenlet khi truy cập lazy attributes
        reload_query = (
            select(Document)
            .where(Document.id == doc.id)
            .options(
                selectinload(Document.parent_chunks),
                selectinload(Document.child_chunks)
            )
        )
        reloaded_res = await db.execute(reload_query)
        return reloaded_res.scalar_one()

    @staticmethod
    async def list_documents(
        db: AsyncSession,
        tenant_id: uuid.UUID,
        skip: int = 0,
        limit: int = 20,
        allowed_department_ids: Optional[FrozenSet[uuid.UUID]] = None,
    ) -> List[Tuple[Document, int, int]]:
        """Lấy danh sách tài liệu của Tenant kèm số lượng Parent/Child chunks (đã áp Department ACL)."""
        query = (
            select(Document)
            .where(Document.tenant_id == tenant_id)
            .order_by(Document.created_at.desc())
            .offset(skip)
            .limit(limit)
            .options(
                selectinload(Document.parent_chunks),
                selectinload(Document.child_chunks)
            )
        )
        query = apply_document_department_scope(query, Document.department_id, allowed_department_ids)
        res = await db.execute(query)
        docs = res.scalars().all()

        results = []
        for doc in docs:
            p_count = len(doc.parent_chunks)
            c_count = len(doc.child_chunks)
            results.append((doc, p_count, c_count))
        return results

    @staticmethod
    async def get_document(
        db: AsyncSession,
        document_id: uuid.UUID,
        tenant_id: uuid.UUID,
        allowed_department_ids: Optional[FrozenSet[uuid.UUID]] = None,
    ) -> Document:
        """Lấy chi tiết tài liệu theo ID, đối soát Tenant (BOLA) và phòng ban (Department ACL)."""
        query = (
            select(Document)
            .where(Document.id == document_id, Document.tenant_id == tenant_id)
            .options(
                selectinload(Document.parent_chunks).selectinload(DocumentParentChunk.child_chunks)
            )
        )
        query = apply_document_department_scope(query, Document.department_id, allowed_department_ids)
        res = await db.execute(query)
        doc = res.scalar_one_or_none()
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Không tìm thấy tài liệu trong doanh nghiệp của bạn",
            )
        return doc

    @staticmethod
    async def delete_document(
        db: AsyncSession,
        document_id: uuid.UUID,
        tenant_id: uuid.UUID
    ) -> None:
        """Xóa tài liệu, xóa cascade toàn bộ chunks và đồng bộ dọn dẹp vector trên FAISS index."""
        query = (
            select(Document)
            .where(Document.id == document_id, Document.tenant_id == tenant_id)
            .options(
                selectinload(Document.parent_chunks),
                selectinload(Document.child_chunks)
            )
        )
        res = await db.execute(query)
        doc = res.scalar_one_or_none()
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Không tìm thấy tài liệu để xóa",
            )
        faiss_ids = [c.faiss_id for c in doc.child_chunks if c.faiss_id is not None]
        deleted_raw_bytes = len((doc.raw_text or "").encode("utf-8"))
        await TelemetryService.record_event(
            db,
            event_type="document_delete",
            tenant_id=tenant_id,
            status_code=200,
            storage_delta_bytes=-deleted_raw_bytes,
            details={"vectors_removed": len(faiss_ids)},
            persist=True,
        )
        await db.delete(doc)
        await db.commit()
        if faiss_ids:
            try:
                await FaissService.remove_vectors(tenant_id, faiss_ids)
            except Exception as exc:
                # DB đã xóa thành công; vector mồ côi không thể trả về vì bước ánh xạ faiss_id -> DB loại bỏ chúng.
                # Ghi log để vận hành rebuild index về sau thay vì làm hỏng phản hồi xóa tài liệu.
                logger.error("Xóa tài liệu thành công nhưng không dọn được vector FAISS: %s", exc)
