"""
MÔ TẢ KIẾN TRÚC:
Bộ kiểm thử tự động toàn diện cho Sprint 4 (FAISS mmap & Hybrid Search RRF).
Kiểm thử toàn bộ luồng Vertical Slice từ HTTP API -> FAISS mmap -> FTS -> RRF Fusion.
Bao gồm:
- Semantic Search (FAISS mmap)
- Exact Keyword / Code Search (FTS)
- RRF Fusion kết hợp thứ hạng
- Phân lập bảo mật Tenant Isolation (Cross-tenant search bị chặn)
- Hierarchical Retrieval (Tìm bằng Child, trả về Parent context)
"""

import pytest
from httpx import AsyncClient


async def register_and_get_token(client: AsyncClient, email: str, tenant_name: str, slug: str) -> str:
    """Helper đăng ký tài khoản và lấy access token."""
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
async def test_hybrid_search_semantic_and_keyword(client: AsyncClient):
    """
    Kiểm thử tìm kiếm lai:
    1. Upload tài liệu chứa quy định và mã hiệu văn bản.
    2. Tìm kiếm ngữ nghĩa (FAISS mmap).
    3. Tìm kiếm chính xác mã văn bản 'QD-8888/QD-AI'.
    """
    token = await register_and_get_token(client, "search_user1@acme.com", "Acme Search Corp", "acme-search")
    headers = {"Authorization": f"Bearer {token}"}

    # Upload tài liệu chính sách
    policy_doc = (
        "# QUYẾT ĐỊNH BAN HÀNH QUY CHẾ LÀM VIỆC TỪ XA\n\n"
        "Căn cứ Nghị định số 45/2024/NĐ-CP của Chính phủ.\n"
        "Ban hành Quyết định mã số QD-8888/QD-AI về chế độ làm việc linh hoạt (Work From Home).\n\n"
        "## ĐIỀU 1: ĐỐI TƯỢNG ÁP DỤNG\n\n"
        "Toàn thể kỹ sư phần mềm và chuyên viên thiết kế sản phẩm được quyền làm việc từ xa tối đa 2 ngày mỗi tuần.\n"
        "Nhân viên phải đảm bảo tham gia đầy đủ các buổi họp trực tuyến Daily Scrum."
    )
    files = {"file": ("wfh_policy.txt", policy_doc.encode("utf-8"), "text/plain")}
    up_res = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    assert up_res.status_code == 201

    # 1. Tìm kiếm ngữ nghĩa (Semantic query không trùng khớp từng chữ)
    semantic_query = {
        "query": "chính sách làm việc tại nhà cho lập trình viên",
        "top_k": 3,
        "dense_weight": 0.7,
        "sparse_weight": 0.3
    }
    search_res_1 = await client.post("/api/v1/search/hybrid", json=semantic_query, headers=headers)
    assert search_res_1.status_code == 200
    res_data_1 = search_res_1.json()
    assert res_data_1["total_found"] >= 1
    top_hit_1 = res_data_1["results"][0]
    assert "làm việc từ xa" in top_hit_1["parent_content"]
    assert top_hit_1["rrf_score"] > 0

    # 2. Tìm kiếm từ khóa chính xác mã hiệu văn bản
    keyword_query = {
        "query": "QD-8888/QD-AI",
        "top_k": 3,
        "dense_weight": 0.3,
        "sparse_weight": 0.7
    }
    search_res_2 = await client.post("/api/v1/search/hybrid", json=keyword_query, headers=headers)
    assert search_res_2.status_code == 200
    res_data_2 = search_res_2.json()
    assert res_data_2["total_found"] >= 1
    top_hit_2 = res_data_2["results"][0]
    assert "QD-8888/QD-AI" in top_hit_2["child_content"]


@pytest.mark.asyncio
async def test_cross_tenant_search_isolation(client: AsyncClient):
    """
    Kiểm định bảo mật đa khách thuê (SEC-003, SEC-004):
    Tenant B không thể tìm thấy bất kỳ vector hoặc văn bản nào của Tenant A.
    """
    # 1. Tenant A upload tài liệu bảo mật
    token_a = await register_and_get_token(client, "corp_a@test.com", "Corp A", "corp-a-search")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    files = {"file": ("secret_project_x.txt", b"Du an bi mat Project-X mang lai 10 trieu USD.", "text/plain")}
    await client.post("/api/v1/documents/upload", files=files, headers=headers_a)

    # 2. Tenant B tìm kiếm từ khóa 'Project-X'
    token_b = await register_and_get_token(client, "corp_b@test.com", "Corp B", "corp-b-search")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    search_query = {
        "query": "Project-X mang lai 10 trieu USD",
        "top_k": 5
    }
    search_res_b = await client.post("/api/v1/search/hybrid", json=search_query, headers=headers_b)
    assert search_res_b.status_code == 200
    # Bắt buộc không tìm thấy gì vì Tenant B có index và CSDL cách ly hoàn toàn
    assert search_res_b.json()["total_found"] == 0
