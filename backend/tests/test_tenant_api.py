import pytest
import pytest_asyncio
import uuid
from sqlalchemy import update
from app.models.user import User


@pytest_asyncio.fixture
async def superuser_headers(client, db_session):
    """Tạo tài khoản Platform Super-Admin và trả về header Bearer Token."""
    await client.post("/api/v1/auth/register", json={
        "email": "superadmin@platform.com",
        "password": "SuperPassword123!",
        "full_name": "Platform Super Admin",
        "tenant_name": "Platform Root",
        "tenant_slug": "platform-root"
    })
    await db_session.execute(
        update(User).where(User.email == "superadmin@platform.com").values(is_superuser=True)
    )
    await db_session.commit()

    login_res = await client.post("/api/v1/auth/login", json={
        "email": "superadmin@platform.com",
        "password": "SuperPassword123!"
    })
    token = login_res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_tenants_endpoints_require_authentication(client):
    """Kiểm thử an ninh: Không có token bị từ chối 401/403 trên mọi endpoint /tenants."""
    res_list = await client.get("/api/v1/tenants")
    assert res_list.status_code in (401, 403)

    res_create = await client.post("/api/v1/tenants", json={"name": "Anon", "slug": "anon"})
    assert res_create.status_code in (401, 403)

    res_suspend = await client.post(f"/api/v1/tenants/{uuid.uuid4()}/suspend")
    assert res_suspend.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_tenant_success(client, superuser_headers):
    """Kiểm thử luồng tạo mới Tenant thành công (POST /api/v1/tenants)."""
    payload = {
        "name": "Tập đoàn Vingroup",
        "slug": "vingroup",
        "quota_limits": {"max_users": 500, "max_documents": 10000}
    }
    response = await client.post("/api/v1/tenants", json=payload, headers=superuser_headers)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Tập đoàn Vingroup"
    assert data["slug"] == "vingroup"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_create_tenant_duplicate_slug_conflict(client, superuser_headers):
    """Kiểm thử phát hiện trùng lặp slug và trả về mã lỗi 409 Conflict."""
    payload = {
        "name": "FPT Software",
        "slug": "fpt-software"
    }
    # Lần 1: Tạo thành công
    res1 = await client.post("/api/v1/tenants", json=payload, headers=superuser_headers)
    assert res1.status_code == 201

    # Lần 2: Trùng slug phải bị từ chối
    res2 = await client.post("/api/v1/tenants", json=payload, headers=superuser_headers)
    assert res2.status_code == 409
    assert "đã tồn tại" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_get_tenant_by_id_and_not_found(client, superuser_headers):
    """Kiểm thử lấy chi tiết Tenant thành công và trường hợp 404 Not Found."""
    # 1. Tạo tenant mẫu
    res = await client.post(
        "/api/v1/tenants",
        json={"name": "Viettel Group", "slug": "viettel"},
        headers=superuser_headers
    )
    tenant_id = res.json()["id"]

    # 2. Lấy thành công
    get_res = await client.get(f"/api/v1/tenants/{tenant_id}", headers=superuser_headers)
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Viettel Group"

    # 3. Lấy ID không tồn tại
    fake_id = str(uuid.uuid4())
    not_found_res = await client.get(f"/api/v1/tenants/{fake_id}", headers=superuser_headers)
    assert not_found_res.status_code == 404


@pytest.mark.asyncio
async def test_list_tenants_pagination(client, superuser_headers):
    """Kiểm thử lấy danh sách phân trang Tenants."""
    # Tạo 3 tenants
    for i in range(3):
        await client.post(
            "/api/v1/tenants",
            json={"name": f"Company {i}", "slug": f"company-{i}"},
            headers=superuser_headers
        )

    response = await client.get("/api/v1/tenants?skip=0&limit=2", headers=superuser_headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_update_tenant(client, superuser_headers):
    """Kiểm thử cập nhật thông tin Tenant (PATCH /api/v1/tenants/{id})."""
    res = await client.post(
        "/api/v1/tenants",
        json={"name": "BKAV Corp", "slug": "bkav"},
        headers=superuser_headers
    )
    tenant_id = res.json()["id"]

    patch_res = await client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"name": "BKAV Security Corporation"},
        headers=superuser_headers
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "BKAV Security Corporation"
    assert patch_res.json()["slug"] == "bkav"


@pytest.mark.asyncio
async def test_suspend_and_activate_tenant(client, superuser_headers):
    """Kiểm thử quy trình tạm khóa (Suspend) và kích hoạt lại Tenant (SR-001)."""
    res = await client.post(
        "/api/v1/tenants",
        json={"name": "CMC Telecom", "slug": "cmc"},
        headers=superuser_headers
    )
    tenant_id = res.json()["id"]
    assert res.json()["is_active"] is True

    # 1. Tạm khóa
    suspend_res = await client.post(f"/api/v1/tenants/{tenant_id}/suspend", headers=superuser_headers)
    assert suspend_res.status_code == 200
    assert suspend_res.json()["is_active"] is False

    # 2. Kích hoạt lại
    activate_res = await client.post(f"/api/v1/tenants/{tenant_id}/activate", headers=superuser_headers)
    assert activate_res.status_code == 200
    assert activate_res.json()["is_active"] is True
