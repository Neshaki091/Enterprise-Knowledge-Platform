"""
MÔ TẢ KIẾN TRÚC:
Router Endpoints cho Tìm kiếm Lai (Hybrid Search).
Cung cấp endpoint tìm kiếm tri thức nội bộ được bảo vệ bởi TenantContext (SEC-003, SEC-004).
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.context import get_current_tenant_context, TenantContext
from app.core.access import ensure_department_allowed
from app.schemas.search import SearchRequest, SearchResponse
from app.services.search_service import SearchService

router = APIRouter()


@router.post(
    "/hybrid",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Tìm kiếm lai kết hợp FAISS mmap và PostgreSQL FTS qua RRF"
)
async def search_hybrid(
    request: SearchRequest,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """
    Tìm kiếm tri thức đa phương thức:
    - Tìm kiếm ngữ nghĩa qua vector 1024 chiều trên FAISS mmap index.
    - Tìm kiếm từ khóa chính xác qua FTS.
    - Hợp nhất kết quả bằng Reciprocal Rank Fusion (k=60).
    - Trả về Parent Chunk giàu ngữ cảnh cho LLM.
    """
    scope = ensure_department_allowed(context, request.department_id)
    return await SearchService.hybrid_search(
        db,
        context.tenant_id,
        request,
        allowed_department_ids=scope,
    )
