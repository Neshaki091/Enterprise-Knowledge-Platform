"""
MÔ TẢ KIẾN TRÚC:
Bộ kiểm thử hồi quy cho giai đoạn Hardening bảo mật đa khách thuê:
- Department ACL: nhân viên chỉ đọc tài liệu phòng ban mình + tài liệu toàn doanh nghiệp.
- Chặn ghi tài liệu vào phòng ban khác.
- Giới hạn kích thước upload (HTTP 413).
- Hạn ngạch max_documents / max_users của Tenant.
- SSE precheck trả 429 + Retry-After trước khi mở stream.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings, Settings
from app.core.login_guard import LoginAttemptGuard
from app.models.tenant import Tenant


async def _register_and_login(
    client: AsyncClient,
    email: str,
    tenant_name: str,
    tenant_slug: str,
) -> dict:
    """Đăng ký Owner và trả về payload token + tenant_id."""
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": f"Owner {tenant_name}",
        "tenant_name": tenant_name,
        "tenant_slug": tenant_slug,
    })
    login = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "Password123!",
    })
    assert login.status_code == 200, login.text
    return login.json()


async def _set_quota(db: AsyncSession, tenant_id: str, quota_limits: dict) -> None:
    """Ghi đè quota_limits của Tenant trực tiếp trong CSDL kiểm thử."""
    await db.execute(
        update(Tenant)
        .where(Tenant.id == uuid.UUID(tenant_id))
        .values(quota_limits=quota_limits)
    )
    await db.commit()


def test_production_config_rejects_weak_defaults():
    """Production phải fail-closed với DEBUG bật, SECRET_KEY yếu, CORS '*' và RLS tắt."""
    weak = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        DEBUG=True,
        SECRET_KEY="change_me",
        CORS_ALLOW_ORIGINS="*",
        DB_RLS_ENABLED=False,
    )
    with pytest.raises(RuntimeError) as exc_info:
        weak.validate_runtime_security()
    message = str(exc_info.value)
    assert "DEBUG" in message
    assert "SECRET_KEY" in message
    assert "CORS" in message
    assert "RLS" in message


def test_production_config_accepts_hardened_settings():
    """Cấu hình production đã siết đúng chuẩn phải khởi động được."""
    strong = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        DEBUG=False,
        SECRET_KEY="a" * 64,
        CORS_ALLOW_ORIGINS="https://app.example.com",
        DB_RLS_ENABLED=True,
    )
    strong.validate_runtime_security()


def test_login_guard_blocks_distributed_ip_rotation():
    """5 lần/IP và 10 lần/email: xoay IP vẫn không thể brute-force một tài khoản."""
    guard = LoginAttemptGuard()
    for index in range(5):
        guard.record_failure("victim@test.com", f"10.0.0.{index}")
    assert guard.check("victim@test.com", "10.0.0.250") is None  # chưa chạm ngưỡng email

    for index in range(5, 10):
        guard.record_failure("victim@test.com", f"10.0.0.{index}")
    assert guard.check("victim@test.com", "10.0.0.250") is not None  # chặn theo email dù IP mới

    guard.reset("victim@test.com", "10.0.0.250")
    assert guard.check("victim@test.com", "10.0.0.250") is None


def test_login_guard_blocks_repeated_same_ip():
    """Cùng email + cùng IP chỉ được sai tối đa 5 lần trong 5 phút."""
    guard = LoginAttemptGuard()
    for _ in range(5):
        guard.record_failure("same-ip@test.com", "192.0.2.10")
    assert guard.check("same-ip@test.com", "192.0.2.10") is not None


@pytest.mark.asyncio
async def test_upload_size_limit_returns_413(client: AsyncClient, monkeypatch):
    """Upload vượt MAX_UPLOAD_SIZE_BYTES phải bị chặn 413 trước khi parse."""
    tokens = await _register_and_login(client, "size_limit@test.com", "Size Limit Corp", "size-limit")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_BYTES", 64)

    payload = b"A" * 65
    res = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("big.txt", payload, "text/plain")},
        headers=headers,
    )
    assert res.status_code == 413
    assert "vượt quá" in res.json()["detail"].lower() or "dung lượng" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_department_acl_read_write_and_search(client: AsyncClient):
    """Nhân viên phòng A không đọc, không ghi, không thấy trong search tài liệu phòng B."""
    owner = await _register_and_login(client, "acl_owner@test.com", "ACL Corp", "acl-corp")
    owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}

    dept_a = await client.post(
        "/api/v1/departments",
        json={"name": "Phòng A", "code": "ACL_A"},
        headers=owner_headers,
    )
    dept_b = await client.post(
        "/api/v1/departments",
        json={"name": "Phòng B", "code": "ACL_B"},
        headers=owner_headers,
    )
    assert dept_a.status_code == 201 and dept_b.status_code == 201
    dept_a_id, dept_b_id = dept_a.json()["id"], dept_b.json()["id"]

    secret = "# TÀI LIỆU MẬT PHÒNG B\n\nChỉ phòng B được xem mã ACL-SECRET-B."
    up = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("dept_b_secret.txt", secret.encode("utf-8"), "text/plain")},
        data={"department_id": dept_b_id},
        headers=owner_headers,
    )
    assert up.status_code == 201, up.text
    doc_b_id = up.json()["id"]

    add_member = await client.post(
        "/api/v1/tenants/members",
        json={
            "email": "acl_employee@test.com",
            "password": "Employee123!",
            "full_name": "Employee A",
            "role": "EMPLOYEE",
            "department_id": dept_a_id,
        },
        headers=owner_headers,
    )
    assert add_member.status_code == 201, add_member.text
    emp_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "acl_employee@test.com", "password": "Employee123!"},
    )
    assert emp_login.status_code == 200, emp_login.text
    emp_headers = {"Authorization": f"Bearer {emp_login.json()['access_token']}"}

    # 1. Đọc trực tiếp tài liệu phòng B -> 404 (fail-closed)
    get_res = await client.get(f"/api/v1/documents/{doc_b_id}", headers=emp_headers)
    assert get_res.status_code == 404

    # 2. Danh sách tài liệu của nhân viên không chứa tài liệu phòng B
    list_res = await client.get("/api/v1/documents", headers=emp_headers)
    assert list_res.status_code == 200
    assert doc_b_id not in [d["id"] for d in list_res.json()]

    # 3. Ghi vào phòng B -> 403
    write_res = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("attack.txt", b"employee writes to foreign dept", "text/plain")},
        data={"department_id": dept_b_id},
        headers=emp_headers,
    )
    assert write_res.status_code == 403

    # 4. Search không trả tài liệu phòng B dù query khớp mã bí mật
    search_res = await client.post(
        "/api/v1/search/hybrid",
        json={"query": "ACL-SECRET-B", "top_k": 10},
        headers=emp_headers,
    )
    assert search_res.status_code == 200
    result_docs = [r["document_id"] for r in search_res.json()["results"]]
    assert doc_b_id not in result_docs

    # 5. Owner vẫn đọc được bình thường
    owner_get = await client.get(f"/api/v1/documents/{doc_b_id}", headers=owner_headers)
    assert owner_get.status_code == 200


@pytest.mark.asyncio
async def test_max_documents_quota_enforced(client: AsyncClient, db_session: AsyncSession):
    """Tenant vượt max_documents phải bị 403 khi upload tài liệu mới."""
    tokens = await _register_and_login(client, "doc_quota@test.com", "Doc Quota Corp", "doc-quota")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    await _set_quota(db_session, tokens["tenant_id"], {"max_documents": 1})

    first = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("first.txt", b"Tai lieu thu nhat cua quota.", "text/plain")},
        headers=headers,
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("second.txt", b"Tai lieu thu hai vuot han ngach.", "text/plain")},
        headers=headers,
    )
    assert second.status_code == 403
    assert "hạn ngạch" in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_max_users_quota_enforced(client: AsyncClient, db_session: AsyncSession):
    """Tenant vượt max_users phải bị 403 khi thêm thành viên mới."""
    tokens = await _register_and_login(client, "user_quota@test.com", "User Quota Corp", "user-quota")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    await _set_quota(db_session, tokens["tenant_id"], {"max_users": 1})

    res = await client.post(
        "/api/v1/tenants/members",
        json={
            "email": "quota_member@test.com",
            "password": "Member123!",
            "full_name": "Quota Member",
            "role": "EMPLOYEE",
        },
        headers=headers,
    )
    assert res.status_code == 403
    assert "hạn ngạch" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_stream_precheck_returns_429_with_retry_after(client: AsyncClient, db_session: AsyncSession):
    """SSE precheck phải trả 429 + Retry-After trước khi mở stream khi hết quota RPM."""
    tokens = await _register_and_login(client, "stream_quota@test.com", "Stream Quota Corp", "stream-quota")
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    up = await client.post(
        "/api/v1/documents/upload",
        files={"file": ("stream_policy.txt", b"Chinh sach lam viec tu xa hai ngay moi tuan.", "text/plain")},
        headers=headers,
    )
    assert up.status_code == 201, up.text
    await _set_quota(db_session, tokens["tenant_id"], {"rpm_limit": 1, "tpm_limit": 16000})

    first = await client.post(
        "/api/v1/chat/stream",
        json={"query": "Chính sách làm việc từ xa?", "top_k": 2, "mode": "mock"},
        headers=headers,
    )
    assert first.status_code == 200
    assert "text/event-stream" in first.headers.get("content-type", "")

    second = await client.post(
        "/api/v1/chat/stream",
        json={"query": "Chính sách làm việc từ xa?", "top_k": 2, "mode": "mock"},
        headers=headers,
    )
    assert second.status_code == 429
    assert second.headers.get("retry-after") is not None
