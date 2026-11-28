"""
MÔ TẢ KIẾN TRÚC:
Bộ kiểm thử End-to-End thực tế toàn luồng Doanh nghiệp (Enterprise E2E Workflow):
1. Đăng ký người dùng thật & Doanh nghiệp thật (Register, Tenant, Departments, Members).
2. Thêm dữ liệu mẫu đa định dạng (TXT, PDF thật, DOCX thật) đi qua pipeline Ephemeral Ingestion + FAISS mmap.
3. Query thật: Hybrid Search có lọc phòng ban, RAG Query trả về `retrieved_chunks` trước khi LLM trả lời,
   và Streaming SSE kiểm chứng sự kiện `chunks` luôn xuất hiện TRƯỚC các `token`.
4. Kiểm chứng giao tiếp HTTP thật của Cloud LLM Client (`OpenAI-compatible` & `Google Gemma/Gemini`) kèm retry Exponential Backoff.
"""

import io
import json
import uuid
import docx
import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import update
from app.core.config import settings
from app.models.user import User
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMService


def _build_minimal_real_pdf_bytes(text_line: str) -> bytes:
    """Sinh file PDF 1.4 hợp lệ chuẩn bảng xref trong RAM để kiểm thử luồng bóc tách pypdf thật."""
    safe_text = text_line.replace("(", "[").replace(")", "]")
    stream_content = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("latin-1", errors="replace")
    objs = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        b"5 0 obj << /Length " + str(len(stream_content)).encode() + b" >> stream\n" + stream_content + b"\nendstream\nendobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())
    return bytes(out)


def _build_real_docx_bytes(paragraphs: list[str]) -> bytes:
    """Sinh file DOCX hợp lệ trong RAM bằng python-docx."""
    doc = docx.Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_real_register_user_enterprise_and_members(client: AsyncClient, db_session):
    """Test thật: Đăng ký Owner + Doanh nghiệp -> Tạo Phòng ban -> Thêm Nhân viên -> Nhân viên đăng nhập thật."""
    # 1. Đăng ký Chủ doanh nghiệp & Tenant A
    reg_res = await client.post("/api/v1/auth/register", json={
        "email": "founder@viettech.vn",
        "password": "FounderPassword123!",
        "full_name": "Tran Van Sang",
        "tenant_name": "VietTech Solutions",
        "tenant_slug": "viettech-solutions"
    })
    assert reg_res.status_code == 201

    login_owner = await client.post("/api/v1/auth/login", json={
        "email": "founder@viettech.vn",
        "password": "FounderPassword123!"
    })
    assert login_owner.status_code == 200
    owner_token = login_owner.json()["access_token"]
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    # 2. Tạo Phòng ban thật (Kỹ thuật & Nhân sự)
    dept_res = await client.post(
        "/api/v1/departments",
        json={"name": "Phòng Kỹ Thuật AI", "code": "ENG_AI"},
        headers=owner_headers,
    )
    assert dept_res.status_code == 201
    dept_id = dept_res.json()["id"]

    # 3. Thêm Nhân viên thật thuộc Phòng Kỹ Thuật AI
    member_res = await client.post(
        "/api/v1/tenants/members",
        json={
            "email": "dev1@viettech.vn",
            "password": "DevPassword123!",
            "full_name": "Nguyen Thi Mai",
            "role": "EMPLOYEE",
            "department_id": dept_id,
        },
        headers=owner_headers,
    )
    assert member_res.status_code == 201
    assert member_res.json()["department_id"] == dept_id

    # 4. Nhân viên vừa tạo tự đăng nhập lấy token và xác minh context phòng ban
    emp_login = await client.post("/api/v1/auth/login", json={
        "email": "dev1@viettech.vn",
        "password": "DevPassword123!"
    })
    assert emp_login.status_code == 200
    emp_headers = {"Authorization": f"Bearer {emp_login.json()['access_token']}"}

    me_res = await client.get("/api/v1/auth/me", headers=emp_headers)
    assert me_res.status_code == 200
    assert me_res.json()["role"] == "EMPLOYEE"
    assert me_res.json()["department_id"] == dept_id

    # Nhân viên thường không được tạo thêm phòng ban
    forbidden_dept = await client.post(
        "/api/v1/departments",
        json={"name": "Phòng Bị Chặn", "code": "FORBIDDEN"},
        headers=emp_headers,
    )
    assert forbidden_dept.status_code == 403

    # 5. Super-Admin tạo thêm một doanh nghiệp B độc lập
    await db_session.execute(
        update(User).where(User.email == "founder@viettech.vn").values(is_superuser=True)
    )
    await db_session.commit()
    tenant_b_res = await client.post(
        "/api/v1/tenants",
        json={"name": "FinBank Vietnam", "slug": "finbank-vn", "quota_limits": {"max_users": 200}},
        headers=owner_headers,
    )
    assert tenant_b_res.status_code == 201


@pytest.mark.asyncio
async def test_real_ingest_mock_data_and_query_chunks_first_before_llm(client: AsyncClient):
    """
    Test thật luồng Nạp dữ liệu mẫu đa định dạng (TXT + PDF + DOCX) -> Query thật:
    - Chế độ backend có thể mock LLM nhưng BẮT BUỘC trả về `retrieved_chunks` và SSE event `chunks` TRƯỚC khi LLM sinh text.
    """
    # 1. Đăng ký doanh nghiệp & phòng ban thật
    await client.post("/api/v1/auth/register", json={
        "email": "rag_owner@enterprise.vn",
        "password": "StrongPassword123!",
        "full_name": "Le Hoang Nam",
        "tenant_name": "Enterprise AI Lab",
        "tenant_slug": "enterprise-ai-lab"
    })
    login_res = await client.post("/api/v1/auth/login", json={
        "email": "rag_owner@enterprise.vn",
        "password": "StrongPassword123!"
    })
    headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}

    dept_res = await client.post(
        "/api/v1/departments",
        json={"name": "Phòng Pháp Chế", "code": "LEGAL"},
        headers=headers,
    )
    dept_id = dept_res.json()["id"]

    # 2. Thêm dữ liệu mẫu (mock content đóng gói thành TXT, PDF thật, DOCX thật)
    txt_bytes = (
        "# QUY ĐỊNH THƯỞNG HIỆU SUẤT QUÝ 4/2026\n\n"
        "Nhân viên đạt KPI loại A nhận thưởng 2.5 tháng lương cơ bản.\n"
        "Thời gian giải ngân dự kiến vào ngày 15 tháng 01 năm 2027."
    ).encode("utf-8")
    up_txt = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("kpi_bonus_2026.txt", txt_bytes, "text/plain")},
        data={"title": "Quy Định Thưởng KPI 2026", "department_id": dept_id},
        headers=headers,
    )
    assert up_txt.status_code == 201

    pdf_bytes = _build_minimal_real_pdf_bytes("Legal Contract Retention Policy 5 Years Code LEG-2026")
    up_pdf = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("retention_policy.pdf", pdf_bytes, "application/pdf")},
        data={"title": "Chính Sách Lưu Trữ PDF", "department_id": dept_id},
        headers=headers,
    )
    assert up_pdf.status_code == 201

    docx_bytes = _build_real_docx_bytes([
        "# QUY TRÌNH PHÊ DUYỆT HỢP ĐỒNG",
        "Mọi hợp đồng trên 500 triệu đồng phải được Giám đốc Pháp chế ký duyệt trong 48 giờ."
    ])
    up_docx = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("contract_sop.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        data={"title": "Quy Trình Hợp Đồng DOCX", "department_id": dept_id},
        headers=headers,
    )
    assert up_docx.status_code == 201

    # 3. Query Hybrid Search thật có lọc department_id
    search_res = await client.post(
        "/api/v1/search/hybrid",
        json={"query": "LEG-2026 Retention Policy", "top_k": 5, "department_id": dept_id},
        headers=headers,
    )
    assert search_res.status_code == 200
    assert search_res.json()["total_found"] >= 1
    assert any("LEG-2026" in r["parent_content"] for r in search_res.json()["results"])

    # 4. Query RAG thường (`mode="mock"`): Bắt buộc trả về `retrieved_chunks` thật trước khi hoàn thiện LLM
    rag_res = await client.post(
        "/api/v1/chat/query",
        json={"query": "Hợp đồng trên 500 triệu đồng ai phê duyệt?", "top_k": 3, "department_id": dept_id, "mode": "mock"},
        headers=headers,
    )
    assert rag_res.status_code == 200
    rag_data = rag_res.json()
    assert len(rag_data["retrieved_chunks"]) >= 1
    assert any("500 triệu" in c["parent_content"] for c in rag_data["retrieved_chunks"])
    assert len(rag_data["citations"]) >= 1
    assert "answer" in rag_data

    # 5. Query Streaming SSE (`mode="mock"`): Chứng minh thứ tự sự kiện trả về `chunks` -> `citations` -> `token` -> `done`
    stream_res = await client.post(
        "/api/v1/chat/stream",
        json={"query": "Nhân viên KPI loại A được thưởng bao nhiêu?", "top_k": 3, "department_id": dept_id, "mode": "mock"},
        headers=headers,
    )
    assert stream_res.status_code == 200
    events = []
    for line in stream_res.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    event_types = [e["type"] for e in events]
    assert "chunks" in event_types
    assert "citations" in event_types
    assert "token" in event_types
    assert event_types[-1] == "done"
    # Ràng buộc cốt lõi: CHUNKS phải về trước TOKEN!
    assert event_types.index("chunks") < event_types.index("token")
    assert len(events[event_types.index("chunks")]["data"]) >= 1


@pytest.mark.asyncio
async def test_real_llm_http_transport_openai_and_google_retry():
    """Kiểm chứng giao tiếp HTTP thật của LLMService (lọc thought part của Gemini 3.5 Flash Lite + retry 429/503) và batching 10 chunks của EmbeddingService."""
    attempts = {"count": 0}

    def openai_mock_handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "gemini-3.5-flash-lite"
        if attempts["count"] == 1:
            return httpx.Response(429, json={"error": "Rate limit"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Trả lời thật từ Gemma API [Nguồn: Policy]."}}]},
        )

    old_vals = (
        settings.LLM_API_URL,
        settings.LLM_API_KEY,
        settings.LLM_PROVIDER,
        settings.LLM_MODEL,
        settings.GOOGLE_API_KEY,
        settings.EMBEDDING_API_URL,
        settings.EMBEDDING_PROVIDER,
        settings.EMBEDDING_MODEL,
        settings.EMBEDDING_DIMENSION,
        settings.EMBEDDING_BATCH_SIZE,
    )
    try:
        settings.LLM_API_URL = "https://mock-llm.local/v1"
        settings.LLM_API_KEY = "test-secret-key"
        settings.LLM_PROVIDER = "openai_compatible"
        settings.LLM_MODEL = "gemini-3.5-flash-lite"

        answer, tokens = await LLMService.generate_answer(
            query="Quy định là gì?",
            context="[Tài liệu: Policy]\nQuy định bảo mật.",
            mode="real",
            transport=httpx.MockTransport(openai_mock_handler),
        )
        assert attempts["count"] == 2
        assert "Trả lời thật từ Gemma API" in answer
        assert tokens > 0

        # Kiểm chứng Google Gemini 3.5 Flash Lite dùng chung GOOGLE_API_KEY, lọc bỏ part thought=True và retry khi 503
        gemma_attempts = {"count": 0}

        def google_mock_handler(request: httpx.Request) -> httpx.Response:
            gemma_attempts["count"] += 1
            assert ":generateContent" in str(request.url)
            assert request.headers.get("x-goog-api-key") == "shared-google-key"
            if gemma_attempts["count"] == 1:
                return httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE"}})
            return httpx.Response(
                200,
                json={
                    "candidates": [{
                        "content": {
                            "parts": [
                                {"text": "* Internal reasoning step...", "thought": True},
                                {"text": "Nhân viên được nghỉ 14 ngày phép [Nguồn: Policy]."},
                            ]
                        }
                    }]
                },
            )

        settings.LLM_API_URL = "https://generativelanguage.googleapis.com"
        settings.LLM_API_KEY = None
        settings.GOOGLE_API_KEY = "shared-google-key"
        settings.LLM_PROVIDER = "google"
        settings.LLM_MODEL = "gemini-3.5-flash-lite"

        g_answer, _ = await LLMService.generate_answer(
            query="Test Google",
            context="[Tài liệu: Policy]\nNội dung.",
            mode="real",
            transport=httpx.MockTransport(google_mock_handler),
        )
        assert gemma_attempts["count"] == 2
        assert g_answer == "Nhân viên được nghỉ 14 ngày phép [Nguồn: Policy]."
        assert "Internal reasoning" not in g_answer

        # Kiểm chứng Embedding batching tối đa 10 chunks/call với gemini-embedding-001 (786 chiều) dùng chung GOOGLE_API_KEY
        batch_sizes = []

        def embed_mock_handler(request: httpx.Request) -> httpx.Response:
            assert "gemini-embedding-001:batchEmbedContents" in str(request.url)
            assert request.headers.get("x-goog-api-key") == "shared-google-key"
            reqs = json.loads(request.content.decode("utf-8"))["requests"]
            assert len(reqs) <= 10
            assert all(r["outputDimensionality"] == 786 and r["taskType"] == "RETRIEVAL_DOCUMENT" for r in reqs)
            batch_sizes.append(len(reqs))
            return httpx.Response(
                200,
                json={"embeddings": [{"values": [0.1] * 786} for _ in reqs]},
            )

        settings.EMBEDDING_API_URL = "https://generativelanguage.googleapis.com"
        settings.EMBEDDING_PROVIDER = "google"
        settings.EMBEDDING_MODEL = "gemini-embedding-001"
        settings.EMBEDDING_DIMENSION = 786
        settings.EMBEDDING_BATCH_SIZE = 10

        vecs = await EmbeddingService.get_embeddings(
            [f"Chunk {i}" for i in range(25)],
            task_type="RETRIEVAL_DOCUMENT",
            transport=httpx.MockTransport(embed_mock_handler),
        )
        assert batch_sizes == [10, 10, 5]
        assert vecs.shape == (25, 786)
    finally:
        (
            settings.LLM_API_URL,
            settings.LLM_API_KEY,
            settings.LLM_PROVIDER,
            settings.LLM_MODEL,
            settings.GOOGLE_API_KEY,
            settings.EMBEDDING_API_URL,
            settings.EMBEDDING_PROVIDER,
            settings.EMBEDDING_MODEL,
            settings.EMBEDDING_DIMENSION,
            settings.EMBEDDING_BATCH_SIZE,
        ) = old_vals
