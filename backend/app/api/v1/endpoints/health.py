"""
MÔ TẢ KIẾN TRÚC:
Endpoint Liveness & Readiness Probe (/api/v1/health).
Thực thi truy vấn kiểm tra kết nối CSDL thật ('SELECT 1'), kiểm tra thư mục FAISS storage
và trạng thái cấu hình dịch vụ Cloud AI (không lộ secret keys).
"""

import os
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db
from app.core.version import APP_VERSION
from app.services.faiss_service import FaissService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", tags=["System"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """Endpoint kiểm tra sức khỏe hệ thống thực tế (CSDL + FAISS storage + cấu hình AI)."""
    db_connected = False
    try:
        probe_res = await db.execute(text("SELECT 1"))
        db_connected = probe_res.scalar_one() == 1
    except (SQLAlchemyError, OSError) as exc:
        logger.warning("Health probe thất bại khi kiểm tra CSDL: %s", exc)
        try:
            await db.rollback()
        except SQLAlchemyError as rb_exc:
            logger.debug("Bỏ qua rollback sau lỗi health probe: %s", rb_exc)

    faiss_storage_ready = False
    try:
        os.makedirs(FaissService.STORAGE_DIR, exist_ok=True)
        faiss_storage_ready = os.path.isdir(FaissService.STORAGE_DIR)
    except OSError as exc:
        logger.warning("Không thể truy cập thư mục FAISS '%s': %s", FaissService.STORAGE_DIR, exc)

    payload = {
        "status": "healthy" if db_connected else "unhealthy",
        "database": "connected" if db_connected else "disconnected",
        "faiss_storage_ready": faiss_storage_ready,
        "llm_provider": settings.LLM_PROVIDER,
        "llm_real_configured": bool(settings.LLM_API_URL and settings.effective_llm_api_key),
        "embedding_real_configured": bool(settings.EMBEDDING_API_URL and settings.effective_embedding_api_key),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "service": "Enterprise AI Knowledge Platform API",
        "version": APP_VERSION,
    }

    if not db_connected:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
    return payload
