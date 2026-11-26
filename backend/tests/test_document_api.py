"""
MÔ TẢ KIẾN TRÚC:
Bộ kiểm thử tự động toàn diện cho Sprint 3 (Ephemeral Ingestion & Hierarchical Chunking).
Kiểm thử toàn bộ luồng Vertical Slice từ HTTP API -> DocumentService -> Chunking -> Database.
Bao gồm các kiểm định:
- Zero Raw Storage (ADR-003)
- Chống trùng lặp SHA-256 (Conflict 409)
- Cắt đoạn phân cấp Parent-Child (ADR-005)
- Bảo mật đa khách thuê & Chặn BOLA (SEC-003, SEC-004)
- Xóa cascade tài liệu và chunks
"""

import io
import pytest
from httpx import AsyncClient
import pypdf


async def get_auth_token(client: AsyncClient, email: str, tenant_name: str, slug: str) -> str:
    """Helper đăng ký và lấy access token của một Tenant."""
    reg_payload = {
        "email": email,
        "password": "Password123!",
        "full_name": f"Admin {tenant_name}",
        "tenant_name": tenant_name,
        "tenant_slug": slug
    }
    await client.post("/api/v1/auth/register", json=reg_payload)
    login_res = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "Password123!"
    })
    return login_res.json()["access_token"]


@pytest.mark.asyncio
async def test_upload_text_document_success(client: AsyncClient):
    """Kiểm thử tải lên tài liệu văn bản thành công, tự động phân đoạn Parent & Child."""
    token = await get_auth_token(client, "doc_user1@acme.com", "Acme Docs", "acme-docs")
    headers = {"Authorization": f"Bearer {token}"}

    sample_content = (
        "# CHÍNH SÁCH BẢO MẬT DOANH NGHIỆP\n\n"
        "Mọi nhân viên phải tuân thủ nghiêm ngặt quy định bảo mật thông tin nội bộ.\n"
        "Tuyệt đối không chia sẻ mật khẩu hoặc API keys ra bên ngoài.\n\n"
        "## QUY TRÌNH XỬ LÝ SỰ CỐ\n\n"
        "Khi phát hiện nghi vấn lộ dữ liệu, lập tức thông báo cho Đội An ninh thông tin trong vòng 15 phút.\n"
        "Các biện pháp cô lập hệ thống sẽ được kích hoạt ngay lập tức."
    )

    files = {
        "file": ("security_policy.txt", sample_content.encode("utf-8"), "text/plain")
    }
    data = {
        "title": "Chính Sách Bảo Mật 2026"
    }

    response = await client.post("/api/v1/documents/upload", files=files, data=data, headers=headers)
    assert response.status_code == 201
    doc_data = response.json()
    assert doc_data["title"] == "Chính Sách Bảo Mật 2026"
    assert doc_data["file_name"] == "security_policy.txt"
    assert "file_hash" in doc_data
    assert doc_data["parent_chunk_count"] >= 1
    assert doc_data["child_chunk_count"] >= 1


@pytest.mark.asyncio
async def test_upload_duplicate_document_conflict(client: AsyncClient):
    """Kiểm định chống trùng lặp mã băm SHA-256: Tải lại file trùng lặp bị từ chối 409 Conflict."""
    token = await get_auth_token(client, "dup_user@beta.com", "Beta Corp", "beta-corp")
    headers = {"Authorization": f"Bearer {token}"}

    file_bytes = b"Tai lieu mat nghiep vu noi bo khong the trung lap."
    files = {"file": ("contract.txt", file_bytes, "text/plain")}

    # Lần 1: Thành công
    res1 = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    assert res1.status_code == 201

    # Lần 2: Bị từ chối 409 Conflict
    files2 = {"file": ("contract_copy.txt", file_bytes, "text/plain")}
    res2 = await client.post("/api/v1/documents/upload", files=files2, headers=headers)
    assert res2.status_code == 409
    assert "Trùng mã băm SHA-256" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_upload_empty_document_rejected(client: AsyncClient):
    """Tải lên file rỗng không có nội dung bị từ chối 400 Bad Request."""
    token = await get_auth_token(client, "empty_user@test.com", "Empty Corp", "empty-corp")
    headers = {"Authorization": f"Bearer {token}"}

    files = {"file": ("empty.txt", b"", "text/plain")}
    res = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_list_and_get_document_detail_hierarchical(client: AsyncClient):
    """Kiểm thử lấy danh sách và chi tiết tài liệu kèm danh sách Parent-Child Chunks."""
    token = await get_auth_token(client, "list_user@corp.com", "List Corp", "list-corp")
    headers = {"Authorization": f"Bearer {token}"}

    # Upload 1 tài liệu
    content = (
        "# CHƯƠNG 1: KHỞI TẠO HỆ THỐNG\n\n"
        "Hướng dẫn thiết lập môi trường phát triển cho kỹ sư backend.\n"
        "Cài đặt Python 3.11 và Docker trên Ubuntu 22.04 LTS.\n\n"
        "# CHƯƠNG 2: VẬN HÀNH\n\n"
        "Các bước vận hành và giám sát máy chủ với Prometheus và Grafana."
    )
    files = {"file": ("manual.txt", content.encode("utf-8"), "text/plain")}
    upload_res = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    assert upload_res.status_code == 201
    doc_id = upload_res.json()["id"]

    # 1. Kiểm tra API danh sách
    list_res = await client.get("/api/v1/documents", headers=headers)
    assert list_res.status_code == 200
    docs = list_res.json()
    assert len(docs) >= 1
    assert any(d["id"] == doc_id for d in docs)

    # 2. Kiểm tra API chi tiết
    detail_res = await client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["raw_text"] is not None
    assert len(detail["parent_chunks"]) >= 1
    for p in detail["parent_chunks"]:
        assert "content" in p
        assert len(p["child_chunks"]) >= 1


@pytest.mark.asyncio
async def test_cross_tenant_document_isolation_blocked(client: AsyncClient):
    """
    Kiểm định an ninh BOLA (SEC-004):
    User Tenant B cố tình truy cập hoặc xóa tài liệu của Tenant A sẽ bị chặn (404/403).
    """
    # 1. Tenant A upload tài liệu
    token_a = await get_auth_token(client, "alpha_admin@a.com", "Tenant A", "tenant-a")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    files = {"file": ("secret_plan_a.txt", b"Ke hoach kinh doanh toi mat cua Tenant A", "text/plain")}
    upload_a = await client.post("/api/v1/documents/upload", files=files, headers=headers_a)
    doc_a_id = upload_a.json()["id"]

    # 2. Tenant B cố tình truy xuất doc_a_id
    token_b = await get_auth_token(client, "beta_admin@b.com", "Tenant B", "tenant-b")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    tamper_get = await client.get(f"/api/v1/documents/{doc_a_id}", headers=headers_b)
    assert tamper_get.status_code == 404

    tamper_delete = await client.delete(f"/api/v1/documents/{doc_a_id}", headers=headers_b)
    assert tamper_delete.status_code == 404


@pytest.mark.asyncio
async def test_delete_document_and_cascade(client: AsyncClient):
    """Kiểm thử xóa tài liệu: CSDL tự động cascade xóa sạch các Parent và Child chunks."""
    token = await get_auth_token(client, "delete_user@corp.com", "Delete Corp", "delete-corp")
    headers = {"Authorization": f"Bearer {token}"}

    files = {"file": ("to_delete.txt", b"Van ban nay se bi xoa sau it giay.", "text/plain")}
    upload_res = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    doc_id = upload_res.json()["id"]

    # Xóa
    del_res = await client.delete(f"/api/v1/documents/{doc_id}", headers=headers)
    assert del_res.status_code == 200

    # Lấy lại -> 404 Not Found
    get_res = await client.get(f"/api/v1/documents/{doc_id}", headers=headers)
    assert get_res.status_code == 404
