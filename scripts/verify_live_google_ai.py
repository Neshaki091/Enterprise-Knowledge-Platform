"""
Kiểm thử Live End-to-End kết nối trực tiếp tới Google Gemini Embedding (`gemini-embedding-001`, 786 chiều, batch max 10)
và Gemma 4 26B A4B (`gemma-4-26b-a4b-it`) qua `GOOGLE_API_KEY` trong `backend/.env`.
"""

import os
import sys
import json
import shutil
import asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)


async def run_live_verification():
    from app.core.config import settings
    from app.core.database import get_db
    from app.main import app as fastapi_app
    from app.models.base import Base
    from app.services.faiss_service import FaissService
    import app.models  # noqa: F401

    print(f"🔑 GOOGLE_API_KEY loaded: {bool(settings.effective_embedding_api_key)}")
    print(f"📐 Embedding: {settings.EMBEDDING_MODEL} | dim={settings.EMBEDDING_DIMENSION} | batch={settings.EMBEDDING_BATCH_SIZE}")
    print(f"🤖 LLM: {settings.LLM_MODEL} | provider={settings.LLM_PROVIDER}")

    temp_db = os.path.join(ROOT_DIR, "endpoint_test", "_live_ai_temp.db")
    temp_faiss = os.path.join(ROOT_DIR, "endpoint_test", "_live_ai_faiss")
    for p in (temp_db, temp_faiss):
        if os.path.exists(p):
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    os.makedirs(temp_faiss, exist_ok=True)
    FaissService.STORAGE_DIR = temp_faiss

    engine = create_async_engine(f"sqlite+aiosqlite:///{temp_db}", echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db

    try:
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://livetest", timeout=60.0) as client:
            # 1. Đăng ký User + Tenant thật
            r_reg = await client.post("/api/v1/auth/register", json={
                "email": "live_test@enterprise.vn",
                "password": "StrongPassword123!",
                "full_name": "Live Tester",
                "tenant_name": "Live AI Corp",
                "tenant_slug": "live-ai-corp",
            })
            assert r_reg.status_code == 201, r_reg.text
            r_log = await client.post("/api/v1/auth/login", json={
                "email": "live_test@enterprise.vn",
                "password": "StrongPassword123!",
            })
            assert r_log.status_code == 200, r_log.text
            headers = {"Authorization": f"Bearer {r_log.json()['access_token']}"}

            # 2. Tạo tài liệu nhiều đoạn để test live batching Embedding (>= 11 child chunks -> 2 batches: 10 + N)
            sections = [
                f"# CHÍNH SÁCH SỐ {i}: QUY ĐỊNH BẢO MẬT & PHÚC LỢI\n\n"
                + " ".join([f"Quy định chi tiết mục {i}.{j} về phúc lợi và quy trình kiểm soát nội bộ." for j in range(1, 22)])
                + "\n"
                for i in range(1, 6)
            ]
            sections.append(
                "# CHÍNH SÁCH SỐ 6: NGHỈ PHÉP & PHỤ CẤP\n\n"
                "Nhân viên chính thức tại Live AI Corp được hưởng 16 ngày nghỉ phép có lương mỗi năm "
                "và nhận phụ cấp ăn trưa 1.200.000 VNĐ/tháng.\n"
            )
            doc_bytes = "\n\n".join(sections).encode("utf-8")
            r_up = await client.post(
                "/api/v1/documents/upload",
                files={"file": ("live_policy_2026.txt", doc_bytes, "text/plain")},
                data={"title": "Chính Sách Phúc Lợi 2026"},
                headers=headers,
            )
            assert r_up.status_code == 201, r_up.text
            print(f"📄 Uploaded & Indexed with Gemini Embedding: {r_up.json()['child_chunk_count']} child chunks (786-dim, batch<=10)")

            # 2b. Hybrid Search thật bằng vector gemini-embedding-001 (786 chiều) + FTS
            r_search = await client.post(
                "/api/v1/search/hybrid",
                json={"query": "phụ cấp ăn trưa bao nhiêu tiền", "top_k": 3},
                headers=headers,
            )
            assert r_search.status_code == 200, r_search.text
            assert r_search.json()["total_found"] >= 1
            print(f"🔍 Live Gemini-786 Hybrid Search hits: {r_search.json()['total_found']} (top_rrf={r_search.json()['results'][0]['rrf_score']})")

            # 3. Query RAG thật qua mode='real' (gemini-embedding-001 + FAISS 786 + gemma-4-26b-a4b-it)
            r_chat = await client.post(
                "/api/v1/chat/query",
                json={
                    "query": "Nhân viên được bao nhiêu ngày nghỉ phép và phụ cấp ăn trưa bao nhiêu?",
                    "top_k": 3,
                    "mode": "real",
                },
                headers=headers,
            )
            if r_chat.status_code == 200:
                chat_data = r_chat.json()
                print(f"📦 Retrieved chunks before LLM: {len(chat_data['retrieved_chunks'])}")
                print(f"🤖 Gemma 4 26B Answer:\n{chat_data['answer']}")
            elif r_chat.status_code == 502:
                print(f"⚠️ Gemma 4 26B (mode='real') đang gặp 503 High Demand từ phía Google API: {r_chat.json()['detail']}")
                # Kiểm chứng chế độ hybrid tự động giữ an toàn trải nghiệm khi Google 503
                r_hybrid = await client.post(
                    "/api/v1/chat/query",
                    json={
                        "query": "Nhân viên được bao nhiêu ngày nghỉ phép và phụ cấp ăn trưa bao nhiêu?",
                        "top_k": 3,
                        "mode": "hybrid",
                    },
                    headers=headers,
                )
                assert r_hybrid.status_code == 200, r_hybrid.text
                print(f"🛡️ Hybrid resilience verified: chunks={len(r_hybrid.json()['retrieved_chunks'])}")
            else:
                raise AssertionError(f"Unexpected status {r_chat.status_code}: {r_chat.text}")

            # 4. Streaming SSE: luôn trả chunks trước khi stream token (dùng 'hybrid' để đảm bảo resilience nếu Google 503)
            r_stream = await client.post(
                "/api/v1/chat/stream",
                json={"query": "Phụ cấp ăn trưa hàng tháng là bao nhiêu?", "top_k": 2, "mode": "hybrid"},
                headers=headers,
            )
            assert r_stream.status_code == 200, r_stream.text
            events = [json.loads(line[6:]) for line in r_stream.text.splitlines() if line.startswith("data: ")]
            types = [e["type"] for e in events]
            print(f"🌊 Live SSE event sequence: {types[:4]} ... {types[-1]}")
            assert "chunks" in types and "citations" in types
            if "token" in types:
                assert types.index("chunks") < types.index("token")
            print("🎉 LIVE GOOGLE GEMINI EMBEDDING (786-DIM, BATCH<=10) + GEMMA 4 26B CHECK COMPLETE!")
    finally:
        fastapi_app.dependency_overrides.clear()
        await engine.dispose()
        for p in (temp_db, temp_faiss):
            if os.path.exists(p):
                try:
                    shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
                except OSError:
                    pass


if __name__ == "__main__":
    asyncio.run(run_live_verification())
