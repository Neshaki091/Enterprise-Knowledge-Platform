"""
MÔ TẢ KIẾN TRÚC:
Bộ kiểm thử tự động toàn diện cho Sprint 7 (Three-Tier Dashboards & Streaming SSE API).
Kiểm định:
- Streaming câu trả lời thời gian thực qua Server-Sent Events (media-type text/event-stream).
- Phân quyền Dashboards 3 cấp: Platform Admin (Superuser only), Business Admin (Admin only), Employee Workspace.
- Thu thập đánh giá câu trả lời AI (Thumbs Up / Down feedback).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from app.models.user import User


async def register_and_get_token(client: AsyncClient, email: str, tenant_name: str, slug: str) -> str:
    """Helper đăng ký tài khoản và nhận access token."""
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
async def test_streaming_chat_sse_endpoint(client: AsyncClient):
    """Kiểm thử stream câu trả lời qua SSE nhận được các events: citations, token, done."""
    token = await register_and_get_token(client, "stream_user@corp.com", "Stream Corp", "stream-corp")
    headers = {"Authorization": f"Bearer {token}"}

    # Upload tài liệu
    content = "# QUY CHẾ BẢO MẬT\n\nNghiêm cấm sao chép dữ liệu khách hàng ra thiết bị ngoài."
    files = {"file": ("rule.txt", content.encode("utf-8"), "text/plain")}
    await client.post("/api/v1/documents/upload", files=files, headers=headers)

    query_payload = {
        "query": "Quy định sao chép dữ liệu là gì?",
        "top_k": 2
    }
    response = await client.post("/api/v1/chat/stream", json=query_payload, headers=headers)
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    body = response.text
    assert "data: " in body
    assert "citations" in body
    assert "token" in body
    assert "done" in body


@pytest.mark.asyncio
async def test_feedback_submission(client: AsyncClient):
    """Kiểm thử gửi đánh giá câu trả lời Thumbs Up / Down."""
    token = await register_and_get_token(client, "fb_user@test.com", "Feedback Corp", "fb-corp")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "query": "Hạn mức nghỉ phép?",
        "answer": "Bạn có 14 ngày nghỉ phép.",
        "rating": "THUMBS_UP",
        "comment": "Câu trả lời rất nhanh và chính xác!"
    }
    res = await client.post("/api/v1/chat/feedback", json=payload, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["rating"] == "THUMBS_UP"
    assert "id" in data


@pytest.mark.asyncio
async def test_three_tier_dashboards_rbac(client: AsyncClient, db_session):
    """
    Kiểm định phân quyền Dashboards 3 cấp:
    1. Employee Workspace: Mọi thành viên đều xem được.
    2. Business Admin: Chỉ Admin/Owner xem được.
    3. Platform Admin: Chỉ Super-Admin xem được, thành viên thường bị 403 Forbidden.
    """
    token = await register_and_get_token(client, "dash_owner@acme.com", "Acme Dash Corp", "acme-dash")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Employee Workspace Dashboard -> 200 OK
    ws_res = await client.get("/api/v1/dashboards/workspace", headers=headers)
    assert ws_res.status_code == 200
    ws_data = ws_res.json()
    assert ws_data["tenant_name"] == "Acme Dash Corp"
    assert ws_data["role"] == "OWNER"

    # 2. Business Admin Dashboard -> 200 OK (vì là OWNER)
    tenant_res = await client.get("/api/v1/dashboards/tenant", headers=headers)
    assert tenant_res.status_code == 200
    tenant_data = tenant_res.json()
    assert tenant_data["total_members"] >= 1
    assert "quota_limits" in tenant_data

    # 3. Platform Admin Dashboard -> Ban đầu bị từ chối 403 vì không phải is_superuser
    plat_res = await client.get("/api/v1/dashboards/platform", headers=headers)
    assert plat_res.status_code == 403

    # Nâng cấp user thành superuser trong DB để kiểm tra
    await db_session.execute(
        update(User).where(User.email == "dash_owner@acme.com").values(is_superuser=True)
    )
    await db_session.commit()

    # Thử lại Platform Admin -> 200 OK
    plat_ok_res = await client.get("/api/v1/dashboards/platform", headers=headers)
    assert plat_ok_res.status_code == 200
    plat_data = plat_ok_res.json()
    assert plat_data["total_tenants"] >= 1
    assert plat_data["system_health"] == "OPERATIONAL"


@pytest.mark.asyncio
async def test_admin_analytics_storage_logs_quota_and_realtime_sse(client: AsyncClient, db_session):
    """
    Kiểm định toàn diện Analytics Dashboard Realtime, Lưu trữ DB vs FAISS, Limit API Key/Model và Audit Logs:
    1. Upload tài liệu + Query RAG cho Tenant A -> sinh dữ liệu lưu trữ DB, file .index FAISS và TelemetryLog.
    2. Tenant Admin xem được DB/FAISS storage breakdown, live 30 RPM / 16K TPM quota, logs & realtime SSE.
    3. Employee bị chặn 403 trên các endpoint admin; Tenant A bị chặn 403 trên /platform/*.
    4. Platform Super-Admin xem được toàn bộ per-tenant breakdown (DB + FAISS + quota), model API limits & SSE.
    """
    import json

    token_a = await register_and_get_token(client, "analytics_a@corp.com", "Analytics A Corp", "analytics-a")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # Tạo 1 Employee thuộc Tenant A để test RBAC 403
    emp_res = await client.post(
        "/api/v1/tenants/members",
        json={"email": "emp_a@corp.com", "password": "Password123!", "full_name": "Emp A", "role": "EMPLOYEE"},
        headers=headers_a,
    )
    assert emp_res.status_code == 201
    emp_login = await client.post("/api/v1/auth/login", json={"email": "emp_a@corp.com", "password": "Password123!"})
    headers_emp = {"Authorization": f"Bearer {emp_login.json()['access_token']}"}

    # Upload tài liệu + Query RAG thật cho Tenant A
    secret_phrase = "NOI_DUNG_BI_MAT_KHONG_DUOC_LO_RA_PLATFORM_LOG_XYZ123"
    doc_body = f"# CHÍNH SÁCH ANALYTICS\n\n{secret_phrase}\nNhân viên được hỗ trợ thiết bị làm việc từ xa."
    up_res = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("analytics_policy.txt", doc_body.encode("utf-8"), "text/plain")},
        headers=headers_a,
    )
    assert up_res.status_code == 201

    chat_res = await client.post(
        "/api/v1/chat/query",
        json={"query": "Nhân viên được hỗ trợ gì khi làm việc từ xa?", "top_k": 3},
        headers=headers_a,
    )
    assert chat_res.status_code == 200

    # 1. Employee không được truy cập /tenant/analytics, /tenant/logs, /tenant/realtime
    assert (await client.get("/api/v1/dashboards/tenant/analytics", headers=headers_emp)).status_code == 403
    assert (await client.get("/api/v1/dashboards/tenant/logs", headers=headers_emp)).status_code == 403
    assert (await client.get("/api/v1/dashboards/tenant/realtime?max_events=1", headers=headers_emp)).status_code == 403

    # 2. Tenant Admin xem /tenant/analytics
    t_ana = await client.get("/api/v1/dashboards/tenant/analytics", headers=headers_a)
    assert t_ana.status_code == 200
    t_data = t_ana.json()
    assert t_data["storage"]["documents_count"] == 1
    assert t_data["storage"]["db_raw_text_bytes"] >= len(doc_body.encode("utf-8"))
    assert t_data["storage"]["faiss_index_exists"] is True
    assert t_data["storage"]["faiss_index_bytes"] > 0
    assert t_data["storage"]["faiss_vectors_count"] >= 1
    assert t_data["live_quota"]["rpm_capacity"] == 30
    assert t_data["live_quota"]["tpm_capacity"] == 16000
    assert t_data["live_quota"]["total_requests"] >= 1
    assert t_data["model_api_limits"]["llm"]["model"] == "gemma-4-26b-a4b-it"
    assert t_data["model_api_limits"]["embedding"]["limits"]["dimension"] == 786

    # 3. Tenant Admin xem logs & SSE realtime
    t_logs = await client.get("/api/v1/dashboards/tenant/logs?limit=20", headers=headers_a)
    assert t_logs.status_code == 200
    event_types = {item["event_type"] for item in t_logs.json()["logs"]}
    assert "document_upload" in event_types
    assert "chat_query" in event_types

    t_sse = await client.get("/api/v1/dashboards/tenant/realtime?interval_sec=0.1&max_events=2", headers=headers_a)
    assert t_sse.status_code == 200
    assert "text/event-stream" in t_sse.headers.get("content-type", "")
    sse_payloads = [
        json.loads(line[6:]) for line in t_sse.text.splitlines() if line.startswith("data: ")
    ]
    assert len(sse_payloads) == 2
    assert sse_payloads[0]["tenant_slug"] == "analytics-a"

    # 4. Tenant Admin thường bị chặn 403 trên /platform/analytics, /platform/logs, /platform/realtime
    assert (await client.get("/api/v1/dashboards/platform/analytics", headers=headers_a)).status_code == 403
    assert (await client.get("/api/v1/dashboards/platform/logs", headers=headers_a)).status_code == 403
    assert (await client.get("/api/v1/dashboards/platform/realtime?max_events=1", headers=headers_a)).status_code == 403

    # 5. Nâng cấp thành Super-Admin -> kiểm tra /platform/analytics, /platform/logs, /platform/realtime + SEC-026
    await db_session.execute(
        update(User).where(User.email == "analytics_a@corp.com").values(is_superuser=True)
    )
    await db_session.commit()

    p_ana = await client.get("/api/v1/dashboards/platform/analytics", headers=headers_a)
    assert p_ana.status_code == 200
    p_data = p_ana.json()
    assert p_data["storage_summary"]["total_faiss_bytes"] > 0
    assert p_data["storage_summary"]["total_db_text_bytes"] > 0
    assert p_data["model_api_limits"]["llm"]["rate_limits"]["tpm_limit"] == 16000
    assert p_data["model_api_limits"]["embedding"]["limits"]["batch_size_max"] == 10
    assert any(item["tenant_slug"] == "analytics-a" for item in p_data["tenants_breakdown"])

    p_logs = await client.get("/api/v1/dashboards/platform/logs?limit=20", headers=headers_a)
    assert p_logs.status_code == 200
    # Bảo đảm SEC-026: văn bản bí mật của tenant không bao giờ xuất hiện trong platform logs hay analytics
    assert secret_phrase not in p_logs.text
    assert secret_phrase not in p_ana.text

    p_sse = await client.get("/api/v1/dashboards/platform/realtime?interval_sec=0.1&max_events=2", headers=headers_a)
    assert p_sse.status_code == 200
    p_sse_payloads = [
        json.loads(line[6:]) for line in p_sse.text.splitlines() if line.startswith("data: ")
    ]
    assert len(p_sse_payloads) == 2
    assert "storage_summary" in p_sse_payloads[0]
