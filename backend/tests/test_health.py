"""
Kiểm thử Health Endpoint (/api/v1/health) cho cả kịch bản CSDL hoạt động và CSDL ngắt kết nối (503).
"""

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.exc import SQLAlchemyError
from app.main import app
from app.core.database import get_db
from app.core.version import APP_VERSION


@pytest.mark.asyncio
async def test_health_check_endpoint(client: AsyncClient):
    """Kiểm thử readiness probe endpoint kiểm tra CSDL thật và trả về trạng thái healthy."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert isinstance(data["faiss_storage_ready"], bool)
    assert isinstance(data["llm_real_configured"], bool)
    assert isinstance(data["embedding_real_configured"], bool)
    assert "timestamp_utc" in data
    assert data["version"] == APP_VERSION


@pytest.mark.asyncio
async def test_health_check_returns_503_when_db_down():
    """Kiểm thử /api/v1/health trả về HTTP 503 unhealthy khi truy vấn CSDL thất bại."""
    class _BrokenSession:
        async def execute(self, *_args, **_kwargs):
            raise SQLAlchemyError("Simulated DB connection loss")

        async def rollback(self):
            return None

    async def _broken_get_db():
        yield _BrokenSession()

    prev_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _broken_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as raw_client:
            response = await raw_client.get("/api/v1/health")
            assert response.status_code == 503
            data = response.json()
            assert data["status"] == "unhealthy"
            assert data["database"] == "disconnected"
    finally:
        if prev_override is not None:
            app.dependency_overrides[get_db] = prev_override
        else:
            app.dependency_overrides.pop(get_db, None)
