"""
MÔ TẢ KIẾN TRÚC:
Service truyền phát dòng phản hồi thời gian thực qua Server-Sent Events (SSE).
Thứ tự bắt buộc:
1. Trả về các Chunks đã truy xuất (event: chunks) & Trích dẫn (event: citations) TRƯỚC TIÊN.
2. Truyền luồng từng token câu trả lời từ Cloud LLM API (mode real/hybrid) hoặc Mock grounded stream.
3. Gửi tín hiệu hoàn tất (event: done) kèm thống kê nén token.
"""

import asyncio
import json
import time
import logging
from typing import AsyncGenerator, List, Optional
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core import rls
from app.core.context import TenantContext
from app.core.access import ensure_department_allowed
from app.models.tenant import Tenant
from app.schemas.chat import ChatQueryRequest
from app.schemas.search import SearchRequest
from app.services.quota_service import quota_manager, resolve_tenant_limits
from app.services.search_service import SearchService
from app.services.context_filter import ContextFilterPipeline
from app.services.llm_service import LLMService
from app.services.telemetry_service import TelemetryService

logger = logging.getLogger(__name__)

# Gom token SSE để giảm overhead khung JSON (~40-60 bytes/token) và số lần render frontend.
_SSE_TOKEN_FLUSH_CHARS = 24
_SSE_TOKEN_FLUSH_INTERVAL_SECONDS = 0.05


class StreamingChatService:
    """Điều phối truyền luồng câu trả lời thời gian thực qua SSE."""

    @classmethod
    async def reserve_quota(
        cls,
        db: AsyncSession,
        context: TenantContext,
        request: ChatQueryRequest,
    ) -> int:
        """
        Kiểm tra ACL phòng ban và cấp phát hạn mức TRƯỚC khi mở SSE.
        Nhờ đó client nhận đúng HTTP 429 + Retry-After thay vì một stream 200 chứa lỗi.
        """
        ensure_department_allowed(context, request.department_id)
        tenant = await db.get(Tenant, context.tenant_id)
        limits = resolve_tenant_limits(tenant.quota_limits if tenant else None)
        reserved_tokens = min(
            limits["tpm_limit"],
            request.max_context_tokens + 160 + max(30, len(request.query) // 3),
        )
        is_allowed, retry_after = await quota_manager.acquire(
            str(context.tenant_id),
            estimated_tokens=reserved_tokens,
            rpm_limit=limits["rpm_limit"],
            tpm_limit=limits["tpm_limit"],
        )
        if not is_allowed:
            await TelemetryService.record_event(
                db,
                event_type="rate_limit_blocked",
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                model_name=settings.LLM_MODEL,
                mode=request.mode,
                status_code=429,
                details={"retry_after": retry_after, "transport": "sse_precheck"},
                persist=False,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Hệ thống đã đạt hạn ngạch {limits['rpm_limit']} RPM / {limits['tpm_limit']} TPM. "
                f"Vui lòng thử lại sau {retry_after} giây.",
                headers={"Retry-After": str(retry_after)},
            )
        return reserved_tokens

    @classmethod
    async def stream_response(
        cls,
        db: AsyncSession,
        context: TenantContext,
        request: ChatQueryRequest,
        reserved_tokens: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Async generator sinh các sự kiện SSE chuẩn:
        - Event 'chunks': Trả về ngay các đoạn văn bản liên quan tìm được từ FAISS mmap + FTS.
        - Event 'citations': Trả về nguồn trích dẫn tài liệu.
        - Event 'token': Từng token từ LLM thật hoặc Mock grounded stream.
        - Event 'done': Báo hiệu hoàn tất kèm thống kê nén token.
        """
        started = time.perf_counter()
        tenant_str = str(context.tenant_id)
        session_bind = db.bind

        # 1. Xác thực phạm vi phòng ban + hạn mức hiệu lực của Tenant
        allowed_departments = ensure_department_allowed(context, request.department_id)
        tenant = await db.get(Tenant, context.tenant_id)
        limits = resolve_tenant_limits(tenant.quota_limits if tenant else None)
        rpm_limit = limits["rpm_limit"]
        tpm_limit = limits["tpm_limit"]

        if reserved_tokens is None:
            # Fallback khi generator được gọi trực tiếp (không qua endpoint pre-check)
            reserved_tokens = min(
                tpm_limit,
                request.max_context_tokens + 160 + max(30, len(request.query) // 3),
            )
            is_allowed, retry_after = await quota_manager.acquire(
                tenant_str,
                estimated_tokens=reserved_tokens,
                rpm_limit=rpm_limit,
                tpm_limit=tpm_limit,
            )
            if not is_allowed:
                await TelemetryService.record_event(
                    db,
                    event_type="rate_limit_blocked",
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                    model_name=settings.LLM_MODEL,
                    mode=request.mode,
                    status_code=429,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    details={"retry_after": retry_after},
                    persist=False,
                )
                err_data = json.dumps({
                    "type": "error",
                    "message": f"Vượt quá hạn mức {rpm_limit} RPM / {tpm_limit} TPM. Thử lại sau {retry_after}s",
                    "retry_after": retry_after,
                }, ensure_ascii=False)
                yield f"data: {err_data}\n\n"
                return

        # 2. Tìm kiếm lai thật (FAISS mmap + PostgreSQL FTS + RRF) trong phạm vi ACL
        search_req = SearchRequest(query=request.query, top_k=request.top_k, department_id=request.department_id)
        search_res = await SearchService.hybrid_search(
            db,
            context.tenant_id,
            search_req,
            allowed_department_ids=allowed_departments,
        )

        # 3. Lọc ngữ cảnh 3 tầng (CPU-bound -> thread, không chặn event loop)
        filtered_context, stats = await asyncio.to_thread(
            ContextFilterPipeline.filter_and_compress,
            query=request.query,
            candidates=search_res.results,
            max_chunks=min(request.top_k, 3),
            max_context_tokens=request.max_context_tokens,
        )
        prompt_input_tokens = int(stats.get("prompt_input_tokens", reserved_tokens))
        await quota_manager.reconcile_tokens(
            tenant_str,
            reserved_tokens=reserved_tokens,
            actual_tokens=prompt_input_tokens,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
        )

        # 4. Giải phóng connection DB trước khi stream LLM 20-60 giây.
        # Telemetry cuối stream dùng một session ngắn riêng, nhờ đó 30 SSE đồng thời
        # không còn chiếm 30 connection trong pool (tránh health check timeout).
        session_factory = (
            async_sessionmaker(bind=session_bind, expire_on_commit=False, autoflush=False)
            if session_bind is not None
            else AsyncSessionLocal
        )
        try:
            await db.close()
        except Exception as exc:
            logger.warning("Không thể đóng DB session trước khi stream LLM: %s", exc)

        # 5. BẮT BUỘC TRẢ VỀ CHUNKS TRƯỚC TIÊN (ngay cả khi LLM dùng mock hay real)
        chunks_payload = [
            {
                "child_chunk_id": str(r.child_chunk_id),
                "parent_chunk_id": str(r.parent_chunk_id),
                "document_id": str(r.document_id),
                "document_title": r.document_title,
                "heading": r.heading,
                "child_content": r.child_content,
                "parent_content": r.parent_content,
                "rrf_score": r.rrf_score,
                "dense_score": r.dense_score,
                "sparse_score": r.sparse_score,
            }
            for r in search_res.results[:request.top_k]
        ]
        yield f"data: {json.dumps({'type': 'chunks', 'data': chunks_payload, 'compression_stats': stats}, ensure_ascii=False)}\n\n"

        # 6. Phát sự kiện Citations
        citations_data = [
            {
                "document_id": str(r.document_id),
                "document_title": r.document_title,
                "heading": r.heading,
                "child_chunk_id": str(r.child_chunk_id),
                "score": r.rrf_score
            }
            for r in search_res.results[:request.top_k]
        ]
        yield f"data: {json.dumps({'type': 'citations', 'data': citations_data}, ensure_ascii=False)}\n\n"

        # 7. Phát luồng câu trả lời từ LLMService (hỗ trợ cả 'real' lẫn 'mock' / 'retrieval_only')
        streamed_chars = 0
        token_buffer: List[str] = []
        buffered_chars = 0
        last_flush = time.monotonic()
        try:
            async for token_piece in LLMService.stream_answer(
                query=request.query,
                context=filtered_context,
                mode=request.mode,
            ):
                streamed_chars += len(token_piece)
                token_buffer.append(token_piece)
                buffered_chars += len(token_piece)
                now = time.monotonic()
                if buffered_chars >= _SSE_TOKEN_FLUSH_CHARS or (now - last_flush) >= _SSE_TOKEN_FLUSH_INTERVAL_SECONDS:
                    chunk_text = "".join(token_buffer)
                    token_buffer.clear()
                    buffered_chars = 0
                    last_flush = now
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk_text}, ensure_ascii=False)}\n\n"

            if token_buffer:
                yield f"data: {json.dumps({'type': 'token', 'content': ''.join(token_buffer)}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.error("Lỗi trong lúc stream LLM: %s", exc)
            await TelemetryService.record_event(
                db,
                event_type="llm_upstream_error",
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                model_name=settings.LLM_MODEL,
                mode=request.mode,
                status_code=int(getattr(exc, "status_code", 500)),
                latency_ms=(time.perf_counter() - started) * 1000.0,
                input_tokens=stats.get("original_tokens", 0),
                compressed_tokens=stats.get("compressed_tokens", 0),
                details={"error": str(getattr(exc, "detail", exc))[:160]},
                persist=False,
            )
            err_payload = {"type": "error", "message": str(getattr(exc, "detail", exc))}
            yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\n"
            return

        # Ghi nhận token đầu ra thực tế vào Token Bucket TPM
        output_tokens = max(0, streamed_chars // 4)
        await quota_manager.charge_tokens(
            tenant_str,
            output_tokens,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
        )

        # Gắn lại ngữ cảnh RLS cho session telemetry ngắn: body SSE được tiêu thụ
        # sau khi middleware request có thể đã reset contextvar.
        tenant_token = rls.current_tenant_id.set(str(context.tenant_id))
        user_token = rls.current_user_id.set(str(context.user_id))
        bypass_token = rls.rls_bypass.set(False)
        try:
            async with session_factory() as telemetry_db:
                await TelemetryService.record_event(
                    telemetry_db,
                    event_type="chat_stream",
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                    model_name=settings.LLM_MODEL if request.mode in ("real", "hybrid") else request.mode,
                    mode=request.mode,
                    status_code=200,
                    latency_ms=(time.perf_counter() - started) * 1000.0,
                    input_tokens=stats.get("original_tokens", 0),
                    compressed_tokens=stats.get("compressed_tokens", 0),
                    output_tokens=output_tokens,
                    details={
                        "savings_percent": stats.get("savings_percent", 0.0),
                        "retained_chunks": stats.get("retained_chunks", 0),
                    },
                    persist=True,
                )
                await telemetry_db.commit()
        finally:
            rls.rls_bypass.reset(bypass_token)
            rls.current_user_id.reset(user_token)
            rls.current_tenant_id.reset(tenant_token)

        # 8. Phát sự kiện Hoàn tất (Done)
        done_payload = {
            "type": "done",
            "mode": request.mode,
            "tokens_saved_percent": stats.get("savings_percent", 0.0),
            "retained_chunks": stats.get("retained_chunks", 0),
            "output_tokens": output_tokens,
        }
        yield f"data: {json.dumps(done_payload, ensure_ascii=False)}\n\n"
