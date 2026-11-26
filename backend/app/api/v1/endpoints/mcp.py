"""
MÔ TẢ KIẾN TRÚC:
Router Endpoints cung cấp cổng giao tiếp HTTP và SSE cho Model Context Protocol (MCP).
Cho phép các Agent đám mây hoặc công cụ AI tương tác qua JSON-RPC 2.0 (ADR-007).
"""

import uuid
from typing import Dict, Any, Optional, List
from fastapi import APIRouter, Depends, Request, status, Header, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.context import get_current_tenant_context, TenantContext
from app.mcp.server import MCPServer
from app.mcp.protocol import JsonRpcResponse, JsonRpcErrorCodes
from app.schemas.external_db import (
    ExternalDBConnectorCreate,
    ExternalDBConnectorResponse,
    ExternalDBTokenRequest,
    ExternalDBTokenResponse,
)
from app.services.external_db_service import ExternalDBService

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


def _require_tenant_admin_only(context: TenantContext) -> None:
    """Chỉ cho phép Admin/Owner nội bộ của Tenant; chặn cả Platform Super-Admin xem/cấu hình DB riêng."""
    if not context.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ Quản trị viên Doanh nghiệp (Tenant Admin/Owner) mới được quản lý kết nối DB riêng.",
        )


@router.post(
    "/external-db",
    response_model=ExternalDBConnectorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Đăng ký kết nối DB riêng của doanh nghiệp (mã hóa AES-256-GCM bằng tenant_secret)"
)
async def register_external_db(
    data: ExternalDBConnectorCreate,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    _require_tenant_admin_only(context)
    return await ExternalDBService.create_connector(db, context.tenant_id, context.user_id, data)


@router.get(
    "/external-db",
    response_model=List[ExternalDBConnectorResponse],
    summary="Liệt kê các kết nối DB riêng của doanh nghiệp (ẩn DSN)"
)
async def list_external_dbs(
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    _require_tenant_admin_only(context)
    return await ExternalDBService.list_connectors(db, context.tenant_id)


@router.delete(
    "/external-db/{connector_id}",
    status_code=status.HTTP_200_OK,
    summary="Xóa kết nối DB riêng của doanh nghiệp"
)
async def delete_external_db(
    connector_id: uuid.UUID,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    _require_tenant_admin_only(context)
    await ExternalDBService.delete_connector(db, context.tenant_id, connector_id)
    return {"deleted": True, "connector_id": str(connector_id)}


@router.post(
    "/external-db/token",
    response_model=ExternalDBTokenResponse,
    summary="Cấp JWT giới hạn quyền Read-Only để AI Agent truy vấn DB riêng qua MCP"
)
async def issue_external_db_token(
    data: ExternalDBTokenRequest,
    context: TenantContext = Depends(get_current_tenant_context),
    db: AsyncSession = Depends(get_db),
):
    _require_tenant_admin_only(context)
    token, connector = await ExternalDBService.issue_db_jwt(
        db=db,
        tenant_id=context.tenant_id,
        connector_id=data.connector_id,
        tenant_secret=data.tenant_secret,
        expires_minutes=data.expires_minutes,
    )
    return ExternalDBTokenResponse(
        db_jwt=token,
        connector_id=connector.id,
        allowed_tables=connector.allowed_tables,
        expires_in_seconds=data.expires_minutes * 60,
    )
