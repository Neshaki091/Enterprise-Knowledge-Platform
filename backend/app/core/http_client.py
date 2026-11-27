"""
MÔ TẢ KIẾN TRÚC:
Quản lý HTTP client dùng chung cho các cuộc gọi AI Inference (Embedding, LLM).
Tái sử dụng kết nối TCP/TLS (keep-alive) thay vì bắt tay mới trên mỗi request,
giúp giảm 100-300ms độ trễ mỗi lần gọi và tránh lãng phí file descriptor trên e2-micro.
Client chỉ được tạo lười trong event loop đang chạy và đóng khi ứng dụng tắt.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional, Union

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0, write=10.0, pool=10.0)
_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)

_shared_client: Optional[httpx.AsyncClient] = None


def get_shared_http_client() -> httpx.AsyncClient:
    """Trả về AsyncClient dùng chung, khởi tạo lười trong event loop đang chạy."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, limits=_LIMITS)
        logger.info("Khởi tạo HTTP client dùng chung (keep-alive tới AI Inference APIs)")
    return _shared_client


@asynccontextmanager
async def acquire_http_client(
    *,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    timeout: Union[float, httpx.Timeout, None] = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """
    Cấp HTTP client cho một lần gọi.

    - Khi `transport` được truyền vào (kiểm thử với MockTransport): tạo client tạm
      để không làm bẩn pool dùng chung.
    - Ngược lại: dùng client dùng chung, giữ keep-alive giữa các request.
    """
    if transport is not None:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=timeout or _DEFAULT_TIMEOUT,
        ) as client:
            yield client
    else:
        yield get_shared_http_client()


async def close_shared_http_client() -> None:
    """Đóng client dùng chung khi ứng dụng tắt để giải phóng socket."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None