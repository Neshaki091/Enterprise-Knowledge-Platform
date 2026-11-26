import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.main import app as fastapi_app
from app.core.config import settings
from app.core.database import get_db
from app.models.base import Base
from app.services.faiss_service import FaissService
# Import toàn bộ models để Base.metadata có đầy đủ các bảng
import app.models  # noqa: F401

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def db_session(tmp_path):
    """Tạo session CSDL in-memory, thư mục FAISS tạm và cô lập AI offline cho từng test case."""
    old_storage = FaissService.STORAGE_DIR
    old_emb_url, old_llm_url = settings.EMBEDDING_API_URL, settings.LLM_API_URL
    FaissService.STORAGE_DIR = str(tmp_path / "faiss_indexes")
    settings.EMBEDDING_API_URL = None
    settings.LLM_API_URL = None

    test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    test_session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        async with test_session_factory() as session:
            yield session
            await session.rollback()
    finally:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await test_engine.dispose()
        FaissService.STORAGE_DIR = old_storage
        settings.EMBEDDING_API_URL = old_emb_url
        settings.LLM_API_URL = old_llm_url


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession):
    """Cung cấp AsyncClient kiểm thử gọi toàn bộ vertical slice API."""
    async def override_get_db():
        yield db_session

    fastapi_app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as ac:
        yield ac
    fastapi_app.dependency_overrides.clear()
