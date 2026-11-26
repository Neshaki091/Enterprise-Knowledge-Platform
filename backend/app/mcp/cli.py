"""
MÔ TẢ KIẾN TRÚC:
CLI Runner cho MCP Stdio Transport.
Cho phép cắm trực tiếp MCP Server vào Claude Desktop hoặc Cursor qua giao thức stdin/stdout:
Lệnh chạy: python -m app.mcp.cli --tenant-id <UUID>
"""

import sys
import json
import asyncio
import argparse
import uuid
from app.core.database import AsyncSessionLocal
from app.core.context import TenantContext
from app.mcp.server import MCPServer


async def main():
    parser = argparse.ArgumentParser(description="Enterprise Knowledge MCP Stdio Transport Runner")
    parser.add_argument("--tenant-id", required=True, help="UUID của Tenant phục vụ tra cứu")
    parser.add_argument("--user-id", default=None, help="UUID của User (tùy chọn)")
    args = parser.parse_args()

    tenant_uuid = uuid.UUID(args.tenant_id)
    user_uuid = uuid.UUID(args.user_id) if args.user_id else uuid.uuid4()

    # Tạo context cố định cho phiên Stdio Desktop
    context = TenantContext(
        user_id=user_uuid,
        tenant_id=tenant_uuid,
        role="ADMIN",
        department_id=None,
        email="mcp_desktop_agent@enterprise.local",
        is_superuser=False
    )

    # Dùng asyncio.to_thread để tương thích đa nền tảng (cả Windows lẫn Linux) thay cho connect_read_pipe
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break
        text = line.strip()
        if not text:
            continue

        try:
            payload = json.loads(text)
            async with AsyncSessionLocal() as db:
                response = await MCPServer.handle_request(payload, db=db, context=context)
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}
            sys.stdout.write(json.dumps(err_resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
