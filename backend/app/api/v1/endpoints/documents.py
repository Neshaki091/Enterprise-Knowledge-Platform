"""
MÔ TẢ KIẾN TRÚC:
Router Endpoints quản lý Tài liệu (Documents) và Tải lên không lưu file gốc (Ephemeral Ingestion).
Mọi endpoint đều được bảo vệ bởi TenantContext nhằm ngăn chặn tấn công BOLA/IDOR (SEC-004).
"""

import uuid
from typing import Optional, List
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db
from app.core.context import get_current_tenant_context, TenantContext
from app.core.access import (
    allowed_department_ids,
    ensure_can_write_department,
    ensure_department_allowed,
)
from app.schemas.document import DocumentResponse, DocumentDetailResponse, ParentChunkResponse, ChildChunkResponse
from app.services.document_service import DocumentService

router = APIRouter()


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Tải lên tài liệu mới (Ephemeral Ingestion & Hierarchical Chunking)"
)
async def upload_document(
    file: UploadFile = File(..., description="File tài liệu (PDF, Word, Excel, TXT)"),
    title: Optional[str] = Form(None, description="Tiêu đề gợi nhớ cho tài liệu"),
    department_id: Optional[uuid.UUID] = Form(None, description="ID phòng ban trực thuộc (nếu có)"),
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """
    Quy trình xử lý tải lên an toàn:
    - Đọc nội dung file trực tiếp vào bộ nhớ RAM.
    - Tính mã băm SHA-256 để chống trùng lặp dữ liệu trong Tenant (SEC-003).
    - Bóc tách văn bản thô (raw_text).
    - Giải phóng toàn bộ buffer nhị phân (Zero Raw Storage - ADR-003).
    - Tự động phân đoạn Parent (~1000 tokens) và Child (~200 tokens).
    """
    # Chặn ghi tài liệu sang phòng ban ngoài phạm vi của nhân viên (Department ACL)
    ensure_can_write_department(context, department_id)
    write_scope = allowed_department_ids(context)

    max_bytes = int(settings.MAX_UPLOAD_SIZE_BYTES)
    file_bytes = await file.read(max_bytes + 1)
    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tập tin tải lên rỗng",
        )
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Tập tin vượt quá giới hạn {max_bytes // (1024 * 1024)} MB của hệ thống",
        )

    doc = await DocumentService.ingest_document(
        db=db,
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        file_name=file.filename or "unknown_file",
        file_bytes=file_bytes,
        content_type=file.content_type,
        title=title,
        department_id=department_id,
        allowed_department_ids=write_scope,
    )

    # Đếm số lượng chunks
    parent_count = len(doc.parent_chunks) if doc.parent_chunks else 0
    child_count = len(doc.child_chunks) if doc.child_chunks else 0

    return DocumentResponse(
        id=doc.id,
        tenant_id=doc.tenant_id,
        department_id=doc.department_id,
        title=doc.title,
        file_name=doc.file_name,
        file_type=doc.file_type,
        file_size_bytes=doc.file_size_bytes,
        file_hash=doc.file_hash,
        token_count=doc.token_count,
        status=doc.status,
        created_at=doc.created_at,
        parent_chunk_count=parent_count,
        child_chunk_count=child_count,
    )


@router.get(
    "",
    response_model=List[DocumentResponse],
    summary="Lấy danh sách tài liệu trong phạm vi Tenant"
)
async def list_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    department_id: Optional[uuid.UUID] = Query(None, description="Lọc theo phòng ban (nếu có)"),
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Lấy danh sách tài liệu trong phạm vi Tenant và phạm vi phòng ban được phép."""
    scope = ensure_department_allowed(context, department_id)
    docs_with_counts = await DocumentService.list_documents(
        db,
        context.tenant_id,
        skip=skip,
        limit=limit,
        allowed_department_ids=scope,
    )
    if department_id is not None:
        docs_with_counts = [item for item in docs_with_counts if item[0].department_id == department_id]
    response_items = []
    for doc, p_count, c_count in docs_with_counts:
        response_items.append(
            DocumentResponse(
                id=doc.id,
                tenant_id=doc.tenant_id,
                department_id=doc.department_id,
                title=doc.title,
                file_name=doc.file_name,
                file_type=doc.file_type,
                file_size_bytes=doc.file_size_bytes,
                file_hash=doc.file_hash,
                token_count=doc.token_count,
                status=doc.status,
                created_at=doc.created_at,
                parent_chunk_count=p_count,
                child_chunk_count=c_count,
            )
        )
    return response_items


@router.get(
    "/{document_id}",
    response_model=DocumentDetailResponse,
    summary="Lấy chi tiết tài liệu và danh sách Parent-Child Chunks"
)
async def get_document(
    document_id: uuid.UUID,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Truy xuất chi tiết một tài liệu kèm toàn bộ văn bản và phân đoạn phân cấp."""
    doc = await DocumentService.get_document(
        db,
        document_id,
        context.tenant_id,
        allowed_department_ids=allowed_department_ids(context),
    )
    parent_responses = []
    total_child = 0
    for p in doc.parent_chunks:
        c_responses = [
            ChildChunkResponse(
                id=c.id,
                parent_chunk_id=c.parent_chunk_id,
                chunk_index=c.chunk_index,
                content=c.content,
                token_count=c.token_count,
                faiss_id=c.faiss_id
            )
            for c in p.child_chunks
        ]
        total_child += len(c_responses)
        parent_responses.append(
            ParentChunkResponse(
                id=p.id,
                chunk_index=p.chunk_index,
                heading=p.heading,
                content=p.content,
                token_count=p.token_count,
                child_chunks=c_responses
            )
        )

    return DocumentDetailResponse(
        id=doc.id,
        tenant_id=doc.tenant_id,
        department_id=doc.department_id,
        title=doc.title,
        file_name=doc.file_name,
        file_type=doc.file_type,
        file_size_bytes=doc.file_size_bytes,
        file_hash=doc.file_hash,
        raw_text=doc.raw_text,
        token_count=doc.token_count,
        status=doc.status,
        doc_metadata=doc.doc_metadata,
        created_at=doc.created_at,
        parent_chunk_count=len(parent_responses),
        child_chunk_count=total_child,
        parent_chunks=parent_responses
    )


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_200_OK,
    summary="Xóa tài liệu và toàn bộ chunks liên kết"
)
async def delete_document(
    document_id: uuid.UUID,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Xóa tài liệu (Yêu cầu vai trò Quản trị viên Tenant)."""
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản trị viên (Admin/Owner) mới có quyền xóa tài liệu",
        )
    await DocumentService.delete_document(db, document_id, context.tenant_id)
    return {"message": "Đã xóa tài liệu và toàn bộ phân đoạn liên kết thành công"}
