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


@pytest.mark.asyncio
async def test_mcp_external_db_zero_knowledge_and_jwt_isolation(client: AsyncClient, tmp_path):
    """
    Kiểm định MCP cho DB riêng của doanh nghiệp theo chuẩn Zero-Knowledge + DB JWT:
    - DSN được mã hóa AES-256-GCM bằng tenant_secret (không lưu secret trên server).
    - Sai tenant_secret bị chặn 403 -> kể cả chủ nền tảng cũng không thể giải mã hay xin DB JWT.
    - Đúng db_jwt + tenant_secret cho phép chạy SELECT Read-Only trên bảng allowed_tables.
    - Chặn DROP/DELETE và chặn truy vấn bảng ngoài allowed_tables.
    """
    import json
    import sqlite3

    ext_db_file = tmp_path / "enterprise_erp.db"
    conn = sqlite3.connect(str(ext_db_file))
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, item TEXT, total INTEGER)")
    conn.execute("CREATE TABLE payroll (id INTEGER PRIMARY KEY, salary INTEGER)")
    conn.execute("INSERT INTO orders (id, item, total) VALUES (1, 'Laptop', 25000000)")
    conn.execute("INSERT INTO payroll (id, salary) VALUES (1, 99999999)")
    conn.commit()
    conn.close()

    token = await register_and_get_token(client, "extdb@corp.com", "ExtDB Corp", "extdb-corp")
    headers = {"Authorization": f"Bearer {token}"}
    tenant_secret = "Enterprise_Only_Secret_2026!"
    ext_dsn = f"sqlite+aiosqlite:///{ext_db_file.as_posix()}"

    # 1. Đăng ký kết nối DB riêng (chỉ cho phép đọc bảng 'orders')
    reg_res = await client.post(
        "/api/v1/mcp/external-db",
        json={
            "name": "ERP Orders DB",
            "db_type": "sqlite",
            "connection_uri": ext_dsn,
            "tenant_secret": tenant_secret,
            "allowed_tables": ["orders"],
        },
        headers=headers,
    )
    assert reg_res.status_code == 201
    reg_data = reg_res.json()
    connector_id = reg_data["id"]
    assert reg_data["host_masked"] == "sqlite:///***"
    assert ext_dsn not in reg_res.text
    assert tenant_secret not in reg_res.text

    # 2. Sai tenant_secret -> 403 Forbidden (Zero-Knowledge)
    bad_tok = await client.post(
        "/api/v1/mcp/external-db/token",
        json={"connector_id": connector_id, "tenant_secret": "WrongSecretKey123"},
        headers=headers,
    )
    assert bad_tok.status_code == 403

    # 3. Đúng tenant_secret -> cấp phát DB JWT Read-Only
    tok_res = await client.post(
        "/api/v1/mcp/external-db/token",
        json={"connector_id": connector_id, "tenant_secret": tenant_secret, "expires_minutes": 30},
        headers=headers,
    )
    assert tok_res.status_code == 200
    db_jwt = tok_res.json()["db_jwt"]

    # 4. Gọi MCP tool 'query_enterprise_db' với SELECT hợp lệ
    q_ok = await client.post(
        "/api/v1/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 201,
            "method": "tools/call",
            "params": {
                "name": "query_enterprise_db",
                "arguments": {
                    "connector_id": connector_id,
                    "db_jwt": db_jwt,
                    "tenant_secret": tenant_secret,
                    "sql_query": "SELECT id, item, total FROM orders",
                },
            },
        },
        headers=headers,
    )
    assert q_ok.status_code == 200
    res_ok = q_ok.json()["result"]
    assert res_ok["isError"] is False
    parsed = json.loads(res_ok["content"][0]["text"])
    assert parsed["row_count"] == 1
    assert parsed["rows"][0]["item"] == "Laptop"

    # 5. Chặn truy vấn bảng ngoài allowed_tables ('payroll') & chặn DROP TABLE
    for bad_sql in ["SELECT * FROM payroll", "DROP TABLE orders"]:
        q_bad = await client.post(
            "/api/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 202,
                "method": "tools/call",
                "params": {
                    "name": "query_enterprise_db",
                    "arguments": {
                        "connector_id": connector_id,
                        "db_jwt": db_jwt,
                        "tenant_secret": tenant_secret,
                        "sql_query": bad_sql,
                    },
                },
            },
            headers=headers,
        )
        assert q_bad.status_code == 200
        assert q_bad.json()["result"]["isError"] is True
