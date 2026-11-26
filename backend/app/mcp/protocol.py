"""
MÔ TẢ KIẾN TRÚC:
Bộ xử lý chuẩn giao thức JSON-RPC 2.0 cho Model Context Protocol (MCP - ADR-007).
Tuân thủ đặc tả kỹ thuật: https://www.jsonrpc.org/specification
"""

from typing import Any, Dict, Optional, Union
from pydantic import BaseModel, Field


class JsonRpcErrorCodes:
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    UNAUTHORIZED = -32001
    FORBIDDEN = -32003


class JsonRpcRequest(BaseModel):
    """Schema yêu cầu JSON-RPC 2.0."""
    jsonrpc: str = Field("2.0", pattern=r"^2\.0$")
    id: Optional[Union[str, int]] = None
    method: str
    params: Optional[Dict[str, Any]] = None


class JsonRpcResponse:
    """Helper định dạng phản hồi chuẩn JSON-RPC 2.0."""

    @staticmethod
    def success(req_id: Optional[Union[str, int]], result: Any) -> Dict[str, Any]:
        """Tạo phản hồi thành công."""
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result
        }

    @staticmethod
    def error(
        req_id: Optional[Union[str, int]],
        code: int,
        message: str,
        data: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Tạo phản hồi lỗi chuẩn."""
        err_obj: Dict[str, Any] = {
            "code": code,
            "message": message
        }
        if data is not None:
            err_obj["data"] = data
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": err_obj
        }
