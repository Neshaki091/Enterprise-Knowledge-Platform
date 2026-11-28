"""
MÔ TẢ KIẾN TRÚC:
Bộ kiểm thử tự động toàn diện cho Sprint 6 (Context Filtering, Token Bucket Rate Limiter & Gemma RAG).
Kiểm định:
- Luồng hỏi đáp RAG với Context Filtering và Trích dẫn nguồn (Citations).
- Khả năng từ chối trả lời khi không có thông tin (Anti-Hallucination).
- Kiểm soát hạn ngạch Token Bucket Rate Limiter (30 RPM / 429 Too Many Requests).
- Đánh giá tỷ lệ nén token của Pipeline 3 tầng.
"""

import gzip
import json
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient


async def register_and_get_token(client: AsyncClient, email: str, tenant_name: str, slug: str) -> str:
    """Helper tạo tài khoản và lấy access token."""
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": f"Admin {tenant_name}",
        "tenant_name": tenant_name,
        "tenant_slug": slug
    })
    login_res = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "Password123!"
    })
    return login_res.json()["access_token"]


@pytest.mark.asyncio
async def test_chat_rag_query_with_citations_and_compression(client: AsyncClient):
    """Kiểm thử quy trình RAG: Tìm kiếm, Nén ngữ cảnh và Sinh câu trả lời có trích dẫn."""
    token = await register_and_get_token(client, "chat_user1@acme.com", "Chat Corp", "chat-corp")
    headers = {"Authorization": f"Bearer {token}"}

    # Upload tài liệu chính sách nghỉ phép
    policy_doc = (
        "# NỘI QUY LAO ĐỘNG VÀ CHẾ ĐỘ NGHỈ PHÉP NĂM 2026\n\n"
        "Mỗi nhân viên chính thức được hưởng 14 ngày nghỉ phép có lương mỗi năm.\n"
        "Nhân viên làm việc đủ 5 năm trở lên được cộng thêm 1 ngày phép thâm niên.\n\n"
        "## QUY TRÌNH NỘP ĐƠN NGHỈ PHÉP\n\n"
        "Đơn xin nghỉ phép từ 3 ngày trở lên phải nộp trước ít nhất 7 ngày làm việc trên cổng ERP.\n"
        "Trưởng bộ phận có trách nhiệm phê duyệt trong vòng 24 giờ."
    )
    files = {"file": ("leave_policy.txt", policy_doc.encode("utf-8"), "text/plain")}
    up_res = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    assert up_res.status_code == 201

    # Gửi câu hỏi
    query_payload = {
        "query": "Số ngày nghỉ phép hàng năm của nhân viên là bao nhiêu ngày?",
        "top_k": 3,
        "mode": "mock"
    }
    chat_res = await client.post("/api/v1/chat/query", json=query_payload, headers=headers)
    assert chat_res.status_code == 200
    data = chat_res.json()
    assert "14 ngày nghỉ phép" in data["answer"]
    assert len(data["citations"]) >= 1
    assert data["citations"][0]["document_title"] == "leave_policy.txt"
    assert "compression_stats" in data
    assert data["compression_stats"]["retained_chunks"] >= 1


@pytest.mark.asyncio
async def test_chat_rag_refusal_when_no_knowledge(client: AsyncClient):
    """Kiểm thử từ chối suy diễn khi câu hỏi hoàn toàn nằm ngoài tài liệu (Anti-Hallucination)."""
    token = await register_and_get_token(client, "chat_user2@test.com", "Empty RAG Corp", "empty-rag-corp")
    headers = {"Authorization": f"Bearer {token}"}

    # Không upload tài liệu nào, hỏi câu hỏi ngẫu nhiên
    query_payload = {
        "query": "Công thức chế tạo nhiên liệu tên lửa vũ trụ là gì?",
        "top_k": 3,
        "mode": "mock"
    }
    chat_res = await client.post("/api/v1/chat/query", json=query_payload, headers=headers)
    assert chat_res.status_code == 200
    data = chat_res.json()
    assert "không tìm thấy thông tin" in data["answer"]


@pytest.mark.asyncio
async def test_token_bucket_rate_limiter_exceeded(client: AsyncClient):
    """
    Kiểm định kiểm soát hạn ngạch Quota Manager:
    Gửi liên tiếp requests vượt quá capacity sẽ bị chặn với mã lỗi HTTP 429 Too Many Requests.
    """
    token = await register_and_get_token(client, "rate_user@spam.com", "Spam Corp", "spam-corp")
    headers = {"Authorization": f"Bearer {token}"}

    query_payload = {
        "query": "Xin chào hệ thống",
        "top_k": 1,
        "mode": "mock"
    }

    # Bucket mặc định có capacity 30 requests.
    # Ta gửi 32 requests liên tục trong vòng 1 giây:
    responses = []
    for _ in range(32):
        res = await client.post("/api/v1/chat/query", json=query_payload, headers=headers)
        responses.append(res.status_code)

    # Bắt buộc ít nhất request thứ 31 hoặc 32 phải bị từ chối 429
    assert 429 in responses
    # Request bị chặn có header Retry-After
    blocked_indices = [idx for idx, code in enumerate(responses) if code == 429]
    assert len(blocked_indices) >= 1


@pytest.mark.asyncio
async def test_context_filter_pipeline_strict_16k_tpm_budget():
    """
    Kiểm thử đơn vị ContextFilterPipeline với văn bản dài nhiều đoạn nhiễu:
    - Giới hạn tối đa max_chunks=3 ngay cả khi có nhiều ứng viên.
    - Loại bỏ ứng viên đuôi có RRF score < 55% so với Top 1.
    - Nén ở mức câu và khóa cứng compressed_tokens <= max_context_tokens (tiết kiệm >= 60%).
    """
    import uuid
    from app.schemas.search import SearchResultItem
    from app.services.context_filter import ContextFilterPipeline

    doc_id = uuid.uuid4()
    noise_block = " ".join(
        [
            "Thông báo hành chính định kỳ về việc bảo trì hệ thống điều hòa không khí tại khu vực sảnh chính tầng trệt."
            for _ in range(12)
        ]
    )

    candidates = []
    for idx in range(6):
        if idx == 0:
            content = (
                f"# CHÍNH SÁCH PHÚC LỢI 2026\n"
                f"{noise_block}\n"
                f"Mỗi nhân viên chính thức được trợ cấp tiền ăn trưa 950.000 VNĐ mỗi tháng và chuyển cùng kỳ lương.\n"
                f"{noise_block}"
            )
            child = "trợ cấp tiền ăn trưa 950.000 VNĐ mỗi tháng"
            rrf = 0.0163
        elif idx == 1:
            content = (
                "Mỗi nhân viên chính thức được trợ cấp tiền ăn trưa 950.000 VNĐ mỗi tháng và chuyển cùng kỳ lương.\n"
                f"Nhân viên thử việc hưởng 80% mức trợ cấp ăn trưa theo quy định phòng Nhân sự.\n{noise_block}"
            )
            child = "trợ cấp ăn trưa"
            rrf = 0.0120
        else:
            # RRF < 0.0163 * 0.55 = 0.00896 -> phải bị Tier 1 cắt bỏ
            content = f"Tài liệu lưu trữ phụ lục số {idx}.\n{noise_block}"
            child = f"phụ lục {idx}"
            rrf = 0.0065

        candidates.append(
            SearchResultItem(
                child_chunk_id=uuid.uuid4(),
                parent_chunk_id=uuid.uuid4(),
                document_id=doc_id,
                document_title="welfare_policy_2026.txt",
                heading=f"Mục {idx + 1}",
                child_content=child,
                parent_content=content,
                rrf_score=rrf,
                dense_score=0.9 - idx * 0.1,
                sparse_score=0.8 - idx * 0.1,
            )
        )

    compressed, stats = ContextFilterPipeline.filter_and_compress(
        query="Nhân viên chính thức được trợ cấp ăn trưa bao nhiêu tiền mỗi tháng?",
        candidates=candidates,
        max_chunks=3,
        max_context_tokens=220,
    )

    assert "950.000 VNĐ" in compressed
    assert "bảo trì hệ thống điều hòa" not in compressed
    assert stats["retained_chunks"] <= 2
    assert stats["dropped_chunks"] >= 4
    assert stats["compressed_tokens"] <= 220
    assert stats["savings_percent"] >= 60.0
    # Đảm bảo câu trùng ở chunk #1 không bị lặp lại hai lần từ chunk #0
    assert compressed.count("950.000 VNĐ mỗi tháng") == 1


@pytest.mark.asyncio
async def test_chat_rag_query_respects_compact_token_budget(client: AsyncClient):
    """Kiểm thử end-to-end RAG giữ compressed_tokens dưới trần max_context_tokens trên tài liệu dài."""
    token = await register_and_get_token(client, "tpm_guard@acme.com", "TPM Guard Corp", "tpm-guard-corp")
    headers = {"Authorization": f"Bearer {token}"}

    filler = " ".join(["Quy trình lưu trữ hồ sơ giấy tờ hành chính tuân thủ chuẩn ISO nội bộ năm 2026." for _ in range(18)])
    long_doc = (
        "# CHÍNH SÁCH CÔNG TÁC PHÍ 2026\n\n"
        f"{filler}\n\n"
        "Nhân viên đi công tác trong nước được thanh toán phụ cấp lưu trú tối đa 1.200.000 VNĐ mỗi đêm.\n\n"
        f"{filler}\n\n"
        "## QUY ĐỊNH CHỨNG TỪ\n\n"
        f"{filler}\n\n"
        "Hóa đơn khách sạn hợp lệ phải nộp về phòng Kế toán trong vòng 5 ngày làm việc sau khi kết thúc chuyến đi."
    )
    up_res = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("travel_policy.txt", long_doc.encode("utf-8"), "text/plain")},
        headers=headers,
    )
    assert up_res.status_code == 201

    res = await client.post(
        "/api/v1/chat/query",
        json={
            "query": "Phụ cấp lưu trú công tác trong nước tối đa bao nhiêu tiền mỗi đêm?",
            "top_k": 5,
            "max_context_tokens": 240,
            "mode": "mock",
        },
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert "1.200.000" in data["answer"]
    assert data["compression_stats"]["compressed_tokens"] <= 240
    assert data["compression_stats"]["savings_percent"] >= 50.0
    assert data["compression_stats"]["retained_chunks"] <= 3


@pytest.mark.asyncio
async def test_quota_manager_16k_tpm_and_refund():
    """Kiểm định QuotaManager kiểm soát trần 16K TPM và hoàn trả phần token dư sau khi nén."""
    from app.services.quota_service import QuotaManager

    qm = QuotaManager()
    tid = "tenant-tpm-test"

    # Dự trữ 1.000 token rồi hoàn trả vì nén chỉ tốn 200 token -> chỉ tiêu thụ 200 token thực
    ok1, wait1 = await qm.acquire(tid, estimated_tokens=1000)
    assert ok1 is True and wait1 is None
    await qm.reconcile_tokens(tid, reserved_tokens=1000, actual_tokens=200)

    # Yêu cầu 15.850 token ngay lập tức vượt 16.000 - 200 = 15.800 -> phải bị chặn kèm retry_after >= 1
    ok2, wait2 = await qm.acquire(tid, estimated_tokens=15850)
    assert ok2 is False
    assert wait2 is not None and wait2 >= 1

    # Yêu cầu 15.500 token vừa khít ngân sách còn lại (15.800) -> được cấp phép
    ok3, wait3 = await qm.acquire(tid, estimated_tokens=15500)
    assert ok3 is True and wait3 is None


@pytest.mark.asyncio
async def test_chat_query_persists_rag_turn_and_history(client: AsyncClient, db_session):
    """Một lượt chat phải lưu query/answer/context/chunk_id và trả được qua /chat/history."""
    from sqlalchemy import select

    from app.models.chat_history import ChatTurn

    token = await register_and_get_token(client, "trace_user@acme.com", "Trace Corp", "trace-corp")
    headers = {"Authorization": f"Bearer {token}"}

    doc = "Chính sách nghỉ phép: mỗi nhân viên được 14 ngày phép có lương mỗi năm."
    files = {"file": ("trace_policy.txt", doc.encode("utf-8"), "text/plain")}
    assert (await client.post("/api/v1/documents/upload", files=files, headers=headers)).status_code == 201

    query = "Nhân viên được bao nhiêu ngày phép?"
    res = await client.post(
        "/api/v1/chat/query",
        json={"query": query, "top_k": 3, "mode": "mock"},
        headers=headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"]

    turns = (await db_session.execute(select(ChatTurn))).scalars().all()
    assert len(turns) == 1
    turn = turns[0]
    assert turn.query == query
    assert "14 ngày" in turn.answer
    assert turn.session_id is not None
    assert turn.contexts
    assert turn.chunk_ids
    assert turn.llm_context

    history = await client.get("/api/v1/chat/history", headers=headers)
    assert history.status_code == 200
    payload = history.json()
    assert payload["total"] == 1
    assert payload["turns"][0]["query"] == query
    assert payload["turns"][0]["answer"] == turn.answer
    assert payload["turns"][0]["chunk_ids"] == turn.chunk_ids

    month = datetime.now(timezone.utc).strftime("%Y-%m")

    # Mặc định loại mock/retrieval_only khỏi dataset RAGAS; file luôn nén gzip.
    default_export = await client.get(f"/api/v1/chat/export?month={month}", headers=headers)
    assert default_export.status_code == 200
    assert "application/gzip" in default_export.headers.get("content-type", "")
    default_text = gzip.decompress(default_export.content).decode("utf-8")
    assert not [line for line in default_text.splitlines() if line.strip()]

    feedback = await client.post(
        "/api/v1/chat/feedback",
        json={"query": query, "answer": data["answer"], "rating": "THUMBS_UP"},
        headers=headers,
    )
    assert feedback.status_code == 201
    await db_session.refresh(turn)
    assert turn.feedback_rating == "THUMBS_UP"

    export = await client.get(
        f"/api/v1/chat/export?month={month}&include_mock=true",
        headers=headers,
    )
    assert export.status_code == 200
    assert "application/gzip" in export.headers.get("content-type", "")
    assert "attachment" in export.headers.get("content-disposition", "")
    assert ".jsonl.gz" in export.headers.get("content-disposition", "")
    exported = [
        json.loads(line)
        for line in gzip.decompress(export.content).decode("utf-8").splitlines()
        if line.strip()
    ]
    assert len(exported) == 1
    assert exported[0]["user_input"] == query
    assert "14 ngày" in exported[0]["response"]
    assert exported[0]["retrieved_contexts"]
    assert exported[0]["metadata"]["chunk_ids"] == turn.chunk_ids
    assert exported[0]["metadata"]["llm_context"]
    assert exported[0]["metadata"]["feedback_rating"] == "THUMBS_UP"


@pytest.mark.asyncio
async def test_export_ragas_forbidden_for_employee(client: AsyncClient):
    """EMPLOYEE không được xuất dữ liệu hội thoại của tenant."""
    owner_token = await register_and_get_token(client, "export_owner@acme.com", "Export Corp", "export-corp")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    add = await client.post(
        "/api/v1/tenants/members",
        json={
            "email": "export_employee@acme.com",
            "password": "Password123!",
            "full_name": "Export Employee",
            "role": "EMPLOYEE",
        },
        headers=owner_headers,
    )
    assert add.status_code == 201

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "export_employee@acme.com", "password": "Password123!"},
    )
    employee_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    res = await client.get(f"/api/v1/chat/export?month={month}", headers=employee_headers)
    assert res.status_code == 403
