"""
MÔ TẢ KIẾN TRÚC:
Service Layer điều phối Tìm kiếm Lai (Hybrid Search Service).
Kết hợp:
1. Dense Retrieval: Vector embedding qua FAISS mmap (IO_FLAG_MMAP_IFC cho IVF-PQ).
2. Sparse Retrieval: Full-Text Search (GIN tsvector trên PostgreSQL, ILIKE fallback trên SQLite).
3. Hợp nhất thứ hạng RRF (Reciprocal Rank Fusion k=60).
4. Truy xuất phân cấp (Hierarchical Retrieval): Tìm theo Child Chunk nhưng trả về Parent Chunk cho LLM.

Dense embedding (network I/O) và sparse FTS (DB I/O) chạy SONG SONG bằng asyncio.gather,
nhờ đó latency hybrid search = max(embedding, FTS) thay vì tổng hai giai đoạn.
"""

import asyncio
import re
import uuid
from typing import FrozenSet, List, Dict, Any, Optional, Tuple
from fastapi import HTTPException, status
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.access import apply_document_department_scope, can_read_department
from app.models.chunk import DocumentChildChunk, DocumentParentChunk
from app.models.document import Document
from app.services.embedding_service import EmbeddingService
from app.services.faiss_service import FaissService
from app.services.rrf_service import RRFService
from app.schemas.search import SearchRequest, SearchResponse, SearchResultItem

_LIKE_ESCAPE_RE = re.compile(r"([\%_])")


def _escape_like(value: str) -> str:
    """Escape ký tự đại diện của LIKE/ILIKE để từ khóa được khớp nguyên văn."""
    return _LIKE_ESCAPE_RE.sub(r"\\1", value)


class SearchService:
    """Điều phối toàn diện quy trình tìm kiếm tri thức doanh nghiệp."""

    @classmethod
    async def _sparse_search(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        request: SearchRequest,
        allowed_department_ids: Optional[FrozenSet[uuid.UUID]],
    ) -> List[Tuple[uuid.UUID, float]]:
        """Sparse retrieval: PostgreSQL GIN FTS hoặc ILIKE fallback cho SQLite test."""
        dialect_name = db.bind.dialect.name if db.bind is not None else "sqlite"
        stop_words = {
            "là", "của", "và", "có", "được", "trong", "về", "cho", "các", "những", "một",
            "bao", "nhiêu", "nào", "gì", "sao", "với", "từ", "đến", "tại", "khi",
        }
        raw_words = [w for w in re.findall(r"[^\W_/\-]+", request.query) if len(w) > 1]
        words = [w for w in raw_words if w.lower() not in stop_words] or raw_words
        sparse_chunk_results: List[Tuple[uuid.UUID, float]] = []

        if dialect_name == "postgresql":
            ts_vector = func.to_tsvector("simple", DocumentChildChunk.content)
            ts_query = func.plainto_tsquery("simple", request.query)
            ts_rank = func.ts_rank(ts_vector, ts_query)
            sparse_stmt = (
                select(DocumentChildChunk.id, DocumentChildChunk.content, ts_rank.label("rank"))
                .join(Document, DocumentChildChunk.document_id == Document.id)
                .where(DocumentChildChunk.tenant_id == tenant_id)
            )
            if request.department_id:
                sparse_stmt = sparse_stmt.where(Document.department_id == request.department_id)
            sparse_stmt = apply_document_department_scope(sparse_stmt, Document.department_id, allowed_department_ids)
            # FTS 'simple' tokenize mã hiệu (INV-2024, A/B-2024, ISO9001:2015, v2.1.0)
            # đồng nhất ở cả query và content, nên không cần OR ILIKE — tránh seq scan.
            sparse_stmt = sparse_stmt.where(ts_vector.op("@@")(ts_query))
            sparse_stmt = sparse_stmt.order_by(ts_rank.desc()).limit(max(20, request.top_k * 5))
            sparse_rows = (await db.execute(sparse_stmt)).all()
            for c_id, content, rank_val in sparse_rows:
                exact_bonus = sum(1 for w in words if w.lower() in content.lower()) / max(1, len(words))
                sparse_score = float(rank_val or 0.0) + exact_bonus
                sparse_chunk_results.append((c_id, sparse_score))
        else:
            sparse_stmt = (
                select(DocumentChildChunk.id, DocumentChildChunk.content)
                .join(Document, DocumentChildChunk.document_id == Document.id)
                .where(DocumentChildChunk.tenant_id == tenant_id)
            )
            if request.department_id:
                sparse_stmt = sparse_stmt.where(Document.department_id == request.department_id)
            sparse_stmt = apply_document_department_scope(sparse_stmt, Document.department_id, allowed_department_ids)
            if words:
                conditions = [
                    DocumentChildChunk.content.ilike(f"%{_escape_like(w)}%", escape="\\")
                    for w in words
                ]
                sparse_stmt = sparse_stmt.where(or_(*conditions))

            # Lấy tập ứng viên đủ rộng trước khi chấm điểm tần suất khớp từ khóa.
            sparse_stmt = sparse_stmt.limit(max(200, request.top_k * 10))
            sparse_rows = (await db.execute(sparse_stmt)).all()

            for c_id, content in sparse_rows:
                matches = sum(1 for w in words if w.lower() in content.lower())
                sparse_score = float(matches) / max(1, len(words))
                sparse_chunk_results.append((c_id, sparse_score))

        sparse_chunk_results.sort(key=lambda x: x[1], reverse=True)
        return sparse_chunk_results[:max(10, request.top_k * 3)]

    @classmethod
    async def hybrid_search(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        request: SearchRequest,
        allowed_department_ids: Optional[FrozenSet[uuid.UUID]] = None,
    ) -> SearchResponse:
        """Thực thi tìm kiếm lai: dense embedding và sparse FTS chạy song song."""
        if (
            request.department_id is not None
            and allowed_department_ids is not None
            and request.department_id not in allowed_department_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Truy cập bị từ chối: Bạn không có quyền tìm kiếm tài liệu của phòng ban khác",
            )

        # 1. DENSE (embedding API) + SPARSE (PostgreSQL FTS) SONG SONG.
        # Embedding là network I/O, FTS là DB I/O; gộp lại để latency = max thay vì sum.
        embedding_task = asyncio.create_task(
            EmbeddingService.get_embedding(request.query, task_type="RETRIEVAL_QUERY")
        )
        sparse_task = asyncio.create_task(
            cls._sparse_search(db, tenant_id, request, allowed_department_ids)
        )
        gathered = await asyncio.gather(embedding_task, sparse_task, return_exceptions=True)
        for outcome in gathered:
            if isinstance(outcome, BaseException):
                raise outcome
        query_vector = gathered[0]
        sparse_chunk_results = gathered[1]

        # 2. DENSE SEARCH (FAISS mmap/IVF-PQ) + ánh xạ faiss_id -> child_chunk_id.
        # Mở rộng tập ứng viên khi có lọc phòng ban để tránh rớt kết quả trước khi cắt top_k.
        is_scoped = request.department_id is not None or allowed_department_ids is not None
        candidate_k = request.top_k * 10 if is_scoped else request.top_k * 2
        dense_candidates = await FaissService.search_mmap(
            tenant_id=tenant_id,
            query_vector=query_vector,
            top_k=candidate_k
        )

        dense_faiss_id_map: Dict[int, float] = {faiss_id: score for faiss_id, score in dense_candidates}
        dense_faiss_ids = list(dense_faiss_id_map.keys())
        dense_chunk_results: List[Tuple[uuid.UUID, float]] = []

        if dense_faiss_ids:
            chunk_stmt = (
                select(DocumentChildChunk.id, DocumentChildChunk.faiss_id)
                .join(Document, DocumentChildChunk.document_id == Document.id)
                .where(
                    DocumentChildChunk.tenant_id == tenant_id,
                    DocumentChildChunk.faiss_id.in_(dense_faiss_ids)
                )
            )
            if request.department_id:
                chunk_stmt = chunk_stmt.where(Document.department_id == request.department_id)
            chunk_stmt = apply_document_department_scope(chunk_stmt, Document.department_id, allowed_department_ids)
            chunk_rows = (await db.execute(chunk_stmt)).all()
            for c_id, f_id in chunk_rows:
                dense_chunk_results.append((c_id, dense_faiss_id_map.get(f_id, 0.0)))
            dense_chunk_results.sort(key=lambda x: x[1], reverse=True)

        # 3. RECIPROCAL RANK FUSION (RRF)
        fused_items = RRFService.fuse(
            dense_results=dense_chunk_results,
            sparse_results=sparse_chunk_results,
            dense_weight=request.dense_weight,
            sparse_weight=request.sparse_weight,
            top_k=request.top_k
        )

        if not fused_items:
            return SearchResponse(query=request.query, total_found=0, results=[])

        # 4. HIERARCHICAL ENRICHMENT (Lấy thông tin Child + Parent Chunk + Document)
        top_chunk_ids = [item["chunk_id"] for item in fused_items]
        enrich_query = (
            select(DocumentChildChunk)
            .where(
                DocumentChildChunk.id.in_(top_chunk_ids),
                DocumentChildChunk.tenant_id == tenant_id,
            )
            .options(
                selectinload(DocumentChildChunk.parent_chunk),
                selectinload(DocumentChildChunk.document)
            )
        )
        loaded_chunks = (await db.execute(enrich_query)).scalars().all()
        chunk_map = {c.id: c for c in loaded_chunks}

        results: List[SearchResultItem] = []
        for item in fused_items:
            c_id = item["chunk_id"]
            child = chunk_map.get(c_id)
            if not child:
                continue

            if request.department_id and child.document.department_id != request.department_id:
                continue
            if not can_read_department(allowed_department_ids, child.document.department_id):
                continue

            results.append(
                SearchResultItem(
                    child_chunk_id=child.id,
                    parent_chunk_id=child.parent_chunk_id,
                    document_id=child.document_id,
                    document_title=child.document.title,
                    heading=child.parent_chunk.heading,
                    child_content=child.content,
                    parent_content=child.parent_chunk.content,
                    rrf_score=item["rrf_score"],
                    dense_score=item.get("dense_score"),
                    sparse_score=item.get("sparse_score"),
                )
            )

        return SearchResponse(
            query=request.query,
            total_found=len(results),
            results=results
        )
