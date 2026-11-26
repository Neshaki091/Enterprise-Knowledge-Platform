"""
MÔ TẢ KIẾN TRÚC:
Bộ kiểm thử tự động toàn diện cho Sprint 5 (Custom Hand-Coded MCP Server).
Kiểm định bắt tay giao thức JSON-RPC 2.0, cấp phát danh sách Tools, thực thi tra cứu tri thức
và kiểm soát phân quyền Tenant Context (SEC-004).
"""

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
async def test_mcp_initialize_and_ping(client: AsyncClient):
    """Kiểm thử bắt tay giao thức MCP (Initialize Handshake) và Ping."""
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "Claude-Desktop", "version": "1.0.0"}
        }
    }
    response = await client.post("/api/v1/mcp", json=init_payload)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["id"] == 1
    assert "result" in res_data
    assert res_data["result"]["protocolVersion"] == "2024-11-05"
    assert res_data["result"]["serverInfo"]["name"] == "enterprise-knowledge-mcp"

    # Ping
    ping_payload = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    ping_res = await client.post("/api/v1/mcp", json=ping_payload)
    assert ping_res.status_code == 200
    assert ping_res.json()["result"] == {}


@pytest.mark.asyncio
async def test_mcp_tools_list_authorized_and_unauthorized(client: AsyncClient):
    """Kiểm thử liệt kê Tools (tools/list) có kiểm tra xác thực quyền (SEC-004)."""
    # 1. Gọi tools/list không có token -> Bị từ chối -32001 (Unauthorized)
    list_payload = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
    unauth_res = await client.post("/api/v1/mcp", json=list_payload)
    assert unauth_res.status_code == 200
    assert "error" in unauth_res.json()
    assert unauth_res.json()["error"]["code"] == -32001

    # 2. Gọi tools/list kèm Bearer Token hợp lệ -> Trả về danh sách Tools
    token = await register_and_get_token(client, "mcp_admin@test.com", "MCP Corp", "mcp-corp")
    headers = {"Authorization": f"Bearer {token}"}

    auth_res = await client.post("/api/v1/mcp", json=list_payload, headers=headers)
    assert auth_res.status_code == 200
    result = auth_res.json()["result"]
    assert "tools" in result
    tool_names = [t["name"] for t in result["tools"]]
    assert "search_knowledge_base" in tool_names
    assert "get_document_context" in tool_names


@pytest.mark.asyncio
async def test_mcp_tools_call_search_and_get_context(client: AsyncClient):
    """Kiểm thử thực thi tool 'search_knowledge_base' và 'get_document_context'."""
    token = await register_and_get_token(client, "agent_user@ai.com", "Agent Corp", "agent-corp")
    headers = {"Authorization": f"Bearer {token}"}

    # Upload tài liệu làm dữ liệu kiểm thử
    content = (
        "# HƯỚNG DẪN CẤU HÌNH BẢO MẬT HỆ THỐNG\n\n"
        "Yêu cầu bật xác thực đa yếu tố 2FA cho toàn bộ tài khoản quản trị.\n"
        "Mật khẩu API phải được mã hóa chuẩn AES-256."
    )
    files = {"file": ("mcp_security.txt", content.encode("utf-8"), "text/plain")}
    up_res = await client.post("/api/v1/documents/upload", files=files, headers=headers)
    doc_id = up_res.json()["id"]

    # 1. Gọi tool 'search_knowledge_base'
    call_search = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "tools/call",
        "params": {
            "name": "search_knowledge_base",
            "arguments": {
                "query": "xác thực 2FA cho quản trị viên",
                "top_k": 3
            }
        }
    }
    call_res = await client.post("/api/v1/mcp", json=call_search, headers=headers)
    assert call_res.status_code == 200
    res_search = call_res.json()["result"]
    assert res_search["isError"] is False
    assert len(res_search["content"]) >= 1
    assert "2FA" in res_search["content"][0]["text"]

    # 2. Gọi tool 'get_document_context'
    call_context = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/call",
        "params": {
            "name": "get_document_context",
            "arguments": {
                "document_id": doc_id
            }
        }
    }
    context_res = await client.post("/api/v1/mcp", json=call_context, headers=headers)
    assert context_res.status_code == 200
    res_context = context_res.json()["result"]
    assert res_context["isError"] is False
    assert "AES-256" in res_context["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_invalid_method_error(client: AsyncClient):
    """Kiểm thử gọi phương thức không tồn tại trả về mã lỗi chuẩn -32601."""
    token = await register_and_get_token(client, "dummy@test.com", "Dummy Corp", "dummy-corp")
    headers = {"Authorization": f"Bearer {token}"}

    payload = {
        "jsonrpc": "2.0",
        "id": 999,
        "method": "unknown_future_method",
        "params": {}
    }
    res = await client.post("/api/v1/mcp", json=payload, headers=headers)
    assert res.status_code == 200
    err = res.json()["error"]
    assert err["code"] == -32601
    assert "không được hỗ trợ" in err["message"]
