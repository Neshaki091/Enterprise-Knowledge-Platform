"""
MÔ TẢ KIẾN TRÚC:
Router Endpoints cung cấp cổng giao tiếp HTTP và SSE cho Model Context Protocol (MCP).
Cho phép các Agent đám mây hoặc công cụ AI tương tác qua JSON-RPC 2.0 (ADR-007).
"""

from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, Request, status, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.context import get_current_tenant_context, TenantContext
from app.mcp.server import MCPServer
from app.mcp.protocol import JsonRpcResponse, JsonRpcErrorCodes

router = APIRouter()


@router.post(
    "",
    summary="Cổng xử lý giao thức MCP JSON-RPC 2.0 qua HTTP POST"
)
async def mcp_endpoint(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Điểm tiếp nhận các cuộc gọi MCP từ AI Agents:
    - Nếu là 'initialize', 'ping', 'notifications/initialized' -> Cho phép bắt tay tự do.
    - Nếu là 'tools/list' hoặc 'tools/call' -> Bắt buộc xác thực TenantContext.
    """
    try:
        payload = await request.json()
    except Exception:
        return JsonRpcResponse.error(None, JsonRpcErrorCodes.PARSE_ERROR, "Không thể phân tích cú pháp JSON")

    if not isinstance(payload, dict):
        return JsonRpcResponse.error(None, JsonRpcErrorCodes.INVALID_REQUEST, "JSON-RPC request phải là một object")

    req_id = payload.get("id")
    method = payload.get("method")
    public_methods = {"initialize", "ping", "notifications/initialized"}

    # Fail-Closed: mọi phương thức nghiệp vụ bắt buộc Bearer Token hợp lệ, không nuốt lỗi xác thực
    context = None
    if method not in public_methods:
        if not authorization or not authorization.startswith("Bearer "):
            return JsonRpcResponse.error(
                req_id,
                JsonRpcErrorCodes.UNAUTHORIZED,
                "Yêu cầu xác thực: Thiếu Bearer Token cho phương thức MCP nghiệp vụ",
            )
        try:
            from fastapi.security import HTTPAuthorizationCredentials
            creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=authorization.split(" ", 1)[1])
            context = await get_current_tenant_context(
                credentials=creds,
                x_tenant_id=request.headers.get("X-Tenant-ID"),
                db=db,
            )
        except HTTPException as exc:
            return JsonRpcResponse.error(req_id, JsonRpcErrorCodes.UNAUTHORIZED, str(exc.detail))
        except Exception:
            return JsonRpcResponse.error(
                req_id,
                JsonRpcErrorCodes.UNAUTHORIZED,
                "Không thể xác thực phiên MCP. Vui lòng đăng nhập lại.",
            )

    return await MCPServer.handle_request(payload, db=db, context=context)


@router.get(
    "/sse",
    summary="Server-Sent Events (SSE) Transport cho MCP"
)
async def mcp_sse_transport(request: Request):
    """Cung cấp SSE stream cho các Agent yêu cầu kết nối Server-Sent Events."""
    async def sse_stream():
        yield "event: endpoint\ndata: /api/v1/mcp\n\n"
    return StreamingResponse(sse_stream(), media_type="text/event-stream")
