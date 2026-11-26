"""
MÔ TẢ KIẾN TRÚC:
Bộ kiểm thử tự động toàn diện cho Sprint 2 (Authentication & Multi-Tenant Context).
Kiểm thử toàn bộ luồng Vertical Slice từ HTTP API -> Service Layer -> Database (SQLite in-memory async).
Bao gồm các kịch bản kiểm tra an ninh nâng cao:
- BOLA/IDOR Prevention (SEC-004)
- Token Rotation & Token Reuse Detection (SEC-005)
- Password Hashing (SEC-001)
- Token Revocation on Logout
"""

import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_register_user_and_initial_tenant_success(client: AsyncClient):
    """Kiểm thử đăng ký người dùng mới tạo đồng thời Tenant và cấp quyền OWNER."""
    payload = {
        "email": "ceo@acme-corp.com",
        "password": "StrongPassword123!",
        "full_name": "Nguyen Van A",
        "tenant_name": "Acme Corporation",
        "tenant_slug": "acme-corp"
    }
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "ceo@acme-corp.com"
    assert data["full_name"] == "Nguyen Van A"
    assert "id" in data
    assert "hashed_password" not in data


@pytest.mark.asyncio
async def test_register_duplicate_email_conflict(client: AsyncClient):
    """Kiểm thử đăng ký trùng email bị từ chối với lỗi HTTP 409 Conflict."""
    payload = {
        "email": "duplicate@test.com",
        "password": "Password123!",
        "full_name": "Test User 1",
        "tenant_name": "Company 1"
    }
    res1 = await client.post("/api/v1/auth/register", json=payload)
    assert res1.status_code == 201

    res2 = await client.post("/api/v1/auth/register", json=payload)
    assert res2.status_code == 409
    assert "đã được đăng ký" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_login_success_and_invalid_credentials(client: AsyncClient):
    """Kiểm thử đăng nhập thành công nhận cặp Tokens và thất bại khi sai mật khẩu."""
    # 1. Đăng ký tài khoản
    reg_payload = {
        "email": "login_test@example.com",
        "password": "CorrectPassword123!",
        "full_name": "Login Tester",
        "tenant_name": "Login Corp"
    }
    await client.post("/api/v1/auth/register", json=reg_payload)

    # 2. Đăng nhập sai mật khẩu -> 401
    bad_login = await client.post("/api/v1/auth/login", json={
        "email": "login_test@example.com",
        "password": "WrongPassword!"
    })
    assert bad_login.status_code == 401

    # 3. Đăng nhập đúng mật khẩu -> 200 kèm Token
    good_login = await client.post("/api/v1/auth/login", json={
        "email": "login_test@example.com",
        "password": "CorrectPassword123!"
    })
    assert good_login.status_code == 200
    token_data = good_login.json()
    assert "access_token" in token_data
    assert "refresh_token" in token_data
    assert token_data["token_type"] == "bearer"
    assert token_data["role"] == "OWNER"


@pytest.mark.asyncio
async def test_get_me_with_tenant_context(client: AsyncClient):
    """Kiểm thử endpoint /me trích xuất chính xác TenantContext từ JWT."""
    # 1. Đăng ký & Đăng nhập
    reg_payload = {
        "email": "me_test@example.com",
        "password": "Password123!",
        "full_name": "Me Tester",
        "tenant_name": "Me Corp"
    }
    await client.post("/api/v1/auth/register", json=reg_payload)
    login_res = await client.post("/api/v1/auth/login", json={
        "email": "me_test@example.com",
        "password": "Password123!"
    })
    token = login_res.json()["access_token"]
    tenant_id = login_res.json()["tenant_id"]

    # 2. Gọi endpoint /me với Bearer Token
    headers = {"Authorization": f"Bearer {token}"}
    me_res = await client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    context = me_res.json()
    assert context["email"] == "me_test@example.com"
    assert context["tenant_id"] == tenant_id
    assert context["role"] == "OWNER"
    assert "ADMIN" in context["permissions"]


@pytest.mark.asyncio
async def test_refresh_token_rotation_and_reuse_detection(client: AsyncClient):
    """
    Kiểm định an ninh SEC-005:
    1. Xoay vòng Refresh Token (Token Rotation) cấp token mới và thu hồi token cũ.
    2. Phát hiện tái sử dụng Token (Reuse Detection) khi gửi lại token cũ đã bị xoay vòng.
    """
    # 1. Đăng ký & Đăng nhập
    reg_payload = {
        "email": "rotation@test.com",
        "password": "Password123!",
        "full_name": "Rotation Tester",
        "tenant_name": "Rotation Corp"
    }
    await client.post("/api/v1/auth/register", json=reg_payload)
    login_res = await client.post("/api/v1/auth/login", json={
        "email": "rotation@test.com",
        "password": "Password123!"
    })
    original_refresh_token = login_res.json()["refresh_token"]

    # 2. Xoay vòng token lần 1 (Hợp lệ)
    refresh_res_1 = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": original_refresh_token
    })
    assert refresh_res_1.status_code == 200
    new_refresh_token = refresh_res_1.json()["refresh_token"]
    new_access_token = refresh_res_1.json()["access_token"]
    assert new_refresh_token != original_refresh_token

    # 3. Tái sử dụng token cũ lần 2 (Kẻ gian dùng lại original_refresh_token)
    reuse_res = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": original_refresh_token
    })
    assert reuse_res.status_code == 401
    assert "Reuse Detected" in reuse_res.json()["detail"]

    # 4. Toàn bộ token family bị thu hồi: refresh token mới nhất cũng không dùng được
    refresh_res_2 = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": new_refresh_token
    })
    assert refresh_res_2.status_code == 401

    # 5. Access token mới nhất cũng bị vô hiệu hóa ngay lập tức
    me_after_reuse = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {new_access_token}"},
    )
    assert me_after_reuse.status_code == 401


@pytest.mark.asyncio
async def test_logout_revocation(client: AsyncClient):
    """Kiểm thử đăng xuất đưa token vào blacklist; các cuộc gọi sau đó bị từ chối 401."""
    # 1. Đăng ký & Đăng nhập
    reg_payload = {
        "email": "logout@test.com",
        "password": "Password123!",
        "full_name": "Logout Tester",
        "tenant_name": "Logout Corp"
    }
    await client.post("/api/v1/auth/register", json=reg_payload)
    login_res = await client.post("/api/v1/auth/login", json={
        "email": "logout@test.com",
        "password": "Password123!"
    })
    access_token = login_res.json()["access_token"]
    refresh_token = login_res.json()["refresh_token"]

    headers = {"Authorization": f"Bearer {access_token}"}

    # 2. Xác nhận token đang hoạt động
    me_before = await client.get("/api/v1/auth/me", headers=headers)
    assert me_before.status_code == 200

    # 3. Thực hiện Đăng xuất
    logout_res = await client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token}, headers=headers)
    assert logout_res.status_code == 200

    # 4. Gọi lại /me với token cũ -> Bị từ chối 401 vì token đã bị thu hồi
    me_after = await client.get("/api/v1/auth/me", headers=headers)
    assert me_after.status_code == 401
    assert "thu hồi" in me_after.json()["detail"]


@pytest.mark.asyncio
async def test_bola_cross_tenant_access_blocked(client: AsyncClient):
    """
    Kiểm định an ninh SEC-004: Tấn công BOLA/IDOR đa khách thuê (Multi-tenant BOLA).
    User A thuộc Tenant A cố tình gửi header X-Tenant-ID trỏ sang Tenant B.
    Hệ thống bắt buộc phải phát hiện và chặn đứng với mã lỗi 403 Forbidden.
    """
    # 1. User A tạo Tenant A
    user_a_payload = {
        "email": "user_a@tenant-a.com",
        "password": "Password123!",
        "full_name": "User Alpha",
        "tenant_name": "Tenant Alpha Corp"
    }
    await client.post("/api/v1/auth/register", json=user_a_payload)
    login_a = await client.post("/api/v1/auth/login", json={
        "email": "user_a@tenant-a.com",
        "password": "Password123!"
    })
    token_a = login_a.json()["access_token"]

    # 2. User B tạo Tenant B
    user_b_payload = {
        "email": "user_b@tenant-b.com",
        "password": "Password123!",
        "full_name": "User Beta",
        "tenant_name": "Tenant Beta Corp"
    }
    await client.post("/api/v1/auth/register", json=user_b_payload)
    login_b = await client.post("/api/v1/auth/login", json={
        "email": "user_b@tenant-b.com",
        "password": "Password123!"
    })
    tenant_b_id = login_b.json()["tenant_id"]

    # 3. User A gửi Token A nhưng kèm header giả mạo X-Tenant-ID trỏ sang Tenant B
    headers_tampered = {
        "Authorization": f"Bearer {token_a}",
        "X-Tenant-ID": str(tenant_b_id)
    }
    tampered_res = await client.get("/api/v1/auth/me", headers=headers_tampered)

    # 4. Kỳ vọng: Bị từ chối thẳng thừng 403 Forbidden (BOLA Blocked)
    assert tampered_res.status_code == 403
    assert "BOLA Blocked" in tampered_res.json()["detail"]
