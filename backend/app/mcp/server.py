"""
MÔ TẢ KIẾN TRÚC:
MCP Server Core (Model Context Protocol Engine).
Tự tay triển khai bắt tay giao thức (Handshake), Liệt kê Tools (tools/list), và Thực thi Tools (tools/call).
Đảm bảo kiểm soát quyền truy cập chặt chẽ theo TenantContext (SEC-003, SEC-004).
"""

import uuid
import json
from typing import Dict, Any, Optional
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.mcp.protocol import JsonRpcErrorCodes, JsonRpcResponse
from app.core.context import TenantContext
from app.core.access import allowed_department_ids
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService
from app.services.document_service import DocumentService
from app.services.external_db_service import ExternalDBService


class MCPServer:
    """Máy chủ xử lý giao thức MCP 2024-11-05 cho AI Agents ngoài."""

    PROTOCOL_VERSION = "2024-11-05"
    SERVER_NAME = "enterprise-knowledge-mcp"
    SERVER_VERSION = "2.3.2"

    # Định nghĩa danh sách các Tools mà MCP Server cung cấp cho AI Agent
    TOOLS_REGISTRY = [
        {
            "name": "search_knowledge_base",
            "description": (
                "Tra cứu kho tri thức nội bộ doanh nghiệp bằng tìm kiếm lai (FAISS mmap + Full-Text Search). "
                "Trả về các đoạn văn bản trích dẫn liên quan nhất kèm tiêu đề và ngữ cảnh đầy đủ."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Câu hỏi tự nhiên hoặc từ khóa cần tìm kiếm trong tài liệu doanh nghiệp"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Số lượng đoạn văn bản tối đa cần lấy (mặc định 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "get_document_context",
            "description": (
                "Lấy toàn bộ nội dung văn bản chi tiết và danh sách phân đoạn phân cấp của một tài liệu "
                "dựa trên document_id."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document_id": {
                        "type": "string",
                        "description": "Mã định danh UUID của tài liệu cần đọc chi tiết"
                    }
                },
                "required": ["document_id"]
            }
        },
        {
            "name": "list_enterprise_db_connectors",
            "description": (
                "Liệt kê các kết nối cơ sở dữ liệu riêng của doanh nghiệp (chỉ hiển thị tên, loại DB và bảng được phép)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
            }
        },
        {
            "name": "query_enterprise_db",
            "description": (
                "Truy vấn Read-Only (SELECT) vào cơ sở dữ liệu riêng của doanh nghiệp. "
                "Bắt buộc có db_jwt và tenant_secret do doanh nghiệp cấp (Zero-Knowledge)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "connector_id": {"type": "string", "description": "UUID của kết nối DB riêng"},
                    "db_jwt": {"type": "string", "description": "JWT phạm vi Read-Only do Tenant cấp"},
                    "tenant_secret": {"type": "string", "description": "Khóa bí mật riêng của doanh nghiệp"},
                    "sql_query": {"type": "string", "description": "Câu lệnh SELECT chỉ đọc"},
                },
                "required": ["connector_id", "db_jwt", "tenant_secret", "sql_query"]
            }
        }
    ]

    @classmethod
    async def handle_request(
        cls,
        payload: Dict[str, Any],
        db: AsyncSession,
        context: Optional[TenantContext] = None
    ) -> Dict[str, Any]:
        """Điều phối và xử lý một JSON-RPC request từ Agent."""
        req_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}

        if not method:
            return JsonRpcResponse.error(
                req_id, JsonRpcErrorCodes.INVALID_REQUEST, "Thiếu trường 'method' trong JSON-RPC request"
            )

        # 1. BẮT TAY GIAO THỨC (Initialize Handshake)
        if method == "initialize":
            return JsonRpcResponse.success(req_id, {
                "protocolVersion": cls.PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": cls.SERVER_NAME,
                    "version": cls.SERVER_VERSION
                }
            })

        if method == "notifications/initialized":
            return JsonRpcResponse.success(req_id, {})

        if method == "ping":
            return JsonRpcResponse.success(req_id, {})

        # 2. KIỂM TRA QUYỀN TRUY CẬP (SEC-004) CHO CÁC PHƯƠNG THỨC NGHIỆP VỤ
        if method.startswith("tools/"):
            if not context:
                return JsonRpcResponse.error(
                    req_id,
                    JsonRpcErrorCodes.UNAUTHORIZED,
                    "Yêu cầu xác thực: Bạn phải truyền Bearer Token hoặc Tenant Context hợp lệ để gọi MCP Tools"
                )

        # 3. DANH SÁCH TOOLS (tools/list)
        if method == "tools/list":
            return JsonRpcResponse.success(req_id, {
                "tools": cls.TOOLS_REGISTRY
            })

        # 4. THỰC THI TOOL (tools/call)
        if method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments") or {}

            if not tool_name:
                return JsonRpcResponse.error(
                    req_id, JsonRpcErrorCodes.INVALID_PARAMS, "Thiếu tham số 'name' của Tool"
                )

            # Phạm vi phòng ban được phép của phiên MCP (Department ACL)
            department_scope = allowed_department_ids(context)

            # Thực thi Tool 1: search_knowledge_base
            if tool_name == "search_knowledge_base":
                query_str = tool_args.get("query")
                if not query_str:
                    return JsonRpcResponse.error(
                        req_id, JsonRpcErrorCodes.INVALID_PARAMS, "Tham số 'query' không được để trống"
                    )
                try:
                    requested_top_k = int(tool_args.get("top_k", 5))
                except (TypeError, ValueError):
                    return JsonRpcResponse.error(
                        req_id, JsonRpcErrorCodes.INVALID_PARAMS, "Tham số 'top_k' phải là số nguyên"
                    )
                # Chặn lạm dụng tài nguyên: tối đa 20 chunks mỗi lần gọi MCP
                top_k = max(1, min(requested_top_k, 20))

                search_req = SearchRequest(query=query_str, top_k=top_k)
                search_res = await SearchService.hybrid_search(
                    db,
                    context.tenant_id,
                    search_req,
                    allowed_department_ids=department_scope,
                )

                formatted_chunks = []
                for idx, r in enumerate(search_res.results, start=1):
                    formatted_chunks.append(
                        f"[{idx}] Tài liệu: '{r.document_title}' | Phân mục: '{r.heading or 'Tổng quát'}' "
                        f"(RRF Score: {r.rrf_score})\n{r.parent_content}\n"
                    )

                text_output = (
                    f"Tìm thấy {search_res.total_found} đoạn trích dẫn liên quan cho truy vấn '{query_str}':\n\n"
                    + "\n---\n".join(formatted_chunks)
                ) if formatted_chunks else f"Không tìm thấy thông tin nào phù hợp cho câu hỏi: '{query_str}'"

                return JsonRpcResponse.success(req_id, {
                    "content": [
                        {
                            "type": "text",
                            "text": text_output
                        }
                    ],
                    "isError": False
                })

            # Thực thi Tool 2: get_document_context
            elif tool_name == "get_document_context":
                doc_id_str = tool_args.get("document_id")
                if not doc_id_str:
                    return JsonRpcResponse.error(
                        req_id, JsonRpcErrorCodes.INVALID_PARAMS, "Thiếu tham số 'document_id'"
                    )
                try:
                    doc_uuid = uuid.UUID(doc_id_str)
                except ValueError:
                    return JsonRpcResponse.error(
                        req_id, JsonRpcErrorCodes.INVALID_PARAMS, "document_id không đúng định dạng UUID"
                    )

                try:
                    doc = await DocumentService.get_document(
                        db,
                        doc_uuid,
                        context.tenant_id,
                        allowed_department_ids=department_scope,
                    )
                except HTTPException as exc:
                    return JsonRpcResponse.success(req_id, {
                        "content": [
                            {
                                "type": "text",
                                "text": f"Không tìm thấy tài liệu có ID '{doc_id_str}' trong phạm vi được phép "
                                f"(Tenant + Phòng ban). Chi tiết: {exc.detail}"
                            }
                        ],
                        "isError": True
                    })

                sections = [f"### Phân mục: {p.heading or 'Đoạn ' + str(p.chunk_index)}\n{p.content}" for p in doc.parent_chunks]
                doc_text = f"# {doc.title} (File: {doc.file_name})\n\n" + "\n\n".join(sections)

                return JsonRpcResponse.success(req_id, {
                    "content": [
                        {
                            "type": "text",
                            "text": doc_text
                        }
                    ],
                    "isError": False
                })

            # Thực thi Tool 3: list_enterprise_db_connectors
            elif tool_name == "list_enterprise_db_connectors":
                connectors = await ExternalDBService.list_connectors(db, context.tenant_id)
                items = [
                    {
                        "connector_id": str(c.id),
                        "name": c.name,
                        "db_type": c.db_type,
                        "host_masked": c.host_masked,
                        "allowed_tables": c.allowed_tables,
                        "is_active": c.is_active,
                    }
                    for c in connectors
                ]
                return JsonRpcResponse.success(req_id, {
                    "content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False)}],
                    "isError": False,
                })

            # Thực thi Tool 4: query_enterprise_db (Zero-Knowledge + JWT + Read-Only SQL)
            elif tool_name == "query_enterprise_db":
                conn_id_str = tool_args.get("connector_id")
                db_jwt = tool_args.get("db_jwt")
                tenant_secret = tool_args.get("tenant_secret")
                sql_query = tool_args.get("sql_query")
                if not all([conn_id_str, db_jwt, tenant_secret, sql_query]):
                    return JsonRpcResponse.error(
                        req_id,
                        JsonRpcErrorCodes.INVALID_PARAMS,
                        "Thiếu tham số bắt buộc: connector_id, db_jwt, tenant_secret, sql_query",
                    )
                try:
                    conn_uuid = uuid.UUID(conn_id_str)
                    result = await ExternalDBService.execute_readonly_query(
                        db=db,
                        tenant_id=context.tenant_id,
                        connector_id=conn_uuid,
                        db_jwt=db_jwt,
                        tenant_secret=tenant_secret,
                        sql_query=sql_query,
                    )
                    return JsonRpcResponse.success(req_id, {
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}],
                        "isError": False,
                    })
                except Exception as exc:
                    return JsonRpcResponse.success(req_id, {
                        "content": [{"type": "text", "text": str(getattr(exc, "detail", exc))}],
                        "isError": True,
                    })

            else:
                return JsonRpcResponse.error(
                    req_id, JsonRpcErrorCodes.METHOD_NOT_FOUND, f"Tool '{tool_name}' không tồn tại trong Registry"
                )

        return JsonRpcResponse.error(
            req_id, JsonRpcErrorCodes.METHOD_NOT_FOUND, f"Phương thức '{method}' không được hỗ trợ"
        )
