"""
MÔ TẢ KIẾN TRÚC:
Service Layer điều phối quy trình Hỏi đáp Tri thức Hoàn chỉnh (End-to-End RAG Service).
Quy trình:
1. Rate Limiting: Kiểm tra Token Bucket qua QuotaManager (30 RPM / 16K TPM).
2. Hybrid Search: Gọi SearchService lấy ứng viên (FAISS mmap + FTS + RRF).
3. Context Filtering: Nén 3 tầng (Thresholding -> Deduplication -> Compression) giảm 70% token.
4. LLM Generation: Gemini 3.5 Flash Lite suy luận có grounding và trích dẫn chứng cứ.
"""

import asyncio
import time
import uuid
from typing import Dict, Any, List
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.context import TenantContext
from app.core.access import ensure_department_allowed
from app.models.tenant import Tenant
from app.schemas.search import SearchRequest
from app.schemas.chat import ChatQueryRequest, ChatQueryResponse, ChatCitation
from app.services.quota_service import quota_manager, resolve_tenant_limits
from app.services.search_service import SearchService
from app.services.context_filter import ContextFilterPipeline
from app.services.llm_service import LLMService
from app.services.telemetry_service import TelemetryService
from app.services.chat_history_service import ChatHistoryService
from app.services.chunking_service import estimate_tokens


class ChatService:
    """Điều phối toàn diện pipeline RAG tri thức doanh nghiệp."""

    @classmethod
    async def process_query(
        cls,
        db: AsyncSession,
        context: TenantContext,
        request: ChatQueryRequest
    ) -> ChatQueryResponse:
        """Thực thi trọn vẹn quy trình RAG."""
        started = time.perf_counter()
        tenant_str = str(context.tenant_id)

        # 0. Xác thực phạm vi phòng ban (Department ACL)
        allowed_departments = ensure_department_allowed(context, request.department_id)
        session_id = request.session_id or uuid.uuid4()

        # 0b. Nạp session memory ngắn (chỉ để hiểu câu hỏi nối tiếp, không phải chứng cứ).
        history_turns = await ChatHistoryService.list_recent_turns(
            db,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            session_id=session_id,
            limit=settings.CHAT_HISTORY_CONTEXT_TURNS,
        )
        history_text = ChatHistoryService.format_for_prompt(
            history_turns,
            settings.CHAT_HISTORY_CONTEXT_TOKENS,
        )
        history_tokens = estimate_tokens(history_text) if history_text else 0

        # 1. KIỂM SOÁT HẠN MỨC (áp dụng quota_limits.rpm_limit / tpm_limit của từng Tenant)
        tenant = await db.get(Tenant, context.tenant_id)
        limits = resolve_tenant_limits(tenant.quota_limits if tenant else None)
        rpm_limit = limits["rpm_limit"]
        tpm_limit = limits["tpm_limit"]

        # Đặt cọc token dự kiến dựa trên trần nén tối đa + session memory (sẽ hoàn lại phần dư).
        reserved_tokens = min(
            tpm_limit,
            request.max_context_tokens + 160 + max(30, len(request.query) // 3) + history_tokens,
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
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Hệ thống đã đạt giới hạn hạn ngạch (Rate Limit: 30 RPM / 16K TPM). Vui lòng thử lại sau {retry_after} giây.",
                headers={"Retry-After": str(retry_after)}
            )

        # 2. TÌM KIẾM LAI (Hybrid Search RRF)
        search_req = SearchRequest(
            query=request.query,
            top_k=request.top_k,
            department_id=request.department_id
        )
        search_res = await SearchService.hybrid_search(
            db,
            context.tenant_id,
            search_req,
            allowed_department_ids=allowed_departments,
        )

        # 3. LỌC VÀ NÉN NGỮ CẢNH 3 TẦNG (CPU-bound -> thread, không chặn event loop)
        filtered_context, comp_stats = await asyncio.to_thread(
            ContextFilterPipeline.filter_and_compress,
            query=request.query,
            candidates=search_res.results,
            max_chunks=min(request.top_k, 3),
            max_context_tokens=request.max_context_tokens,
        )
        # Hoàn trả phần token tiết kiệm được về lại TPM bucket (hoặc trừ thêm nếu vượt dự kiến)
        base_prompt_tokens = int(comp_stats.get("prompt_input_tokens", 0) or 0)
        prompt_input_tokens = base_prompt_tokens + history_tokens
        comp_stats["history_tokens"] = history_tokens
        comp_stats["history_turns"] = len(history_turns)
        await quota_manager.reconcile_tokens(
            tenant_str,
            reserved_tokens=reserved_tokens,
            actual_tokens=prompt_input_tokens,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
        )

        retrieved_chunks = search_res.results[:request.top_k]

        # Trích xuất danh sách chứng cứ trích dẫn (Citations)
        citations: List[ChatCitation] = [
            ChatCitation(
                document_id=r.document_id,
                document_title=r.document_title,
                heading=r.heading,
                child_chunk_id=r.child_chunk_id,
                score=r.rrf_score
            )
            for r in retrieved_chunks
        ]

        # 4. SUY LUẬN TỪ GEMMA 4 26B (Grounded LLM Generation)
        try:
            answer, tokens_used = await LLMService.generate_answer(
                query=request.query,
                context=filtered_context,
                mode=request.mode,
                history=history_text,
            )
        except HTTPException as exc:
            await TelemetryService.record_event(
                db,
                event_type="llm_upstream_error",
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                model_name=settings.LLM_MODEL,
                mode=request.mode,
                status_code=exc.status_code,
                latency_ms=(time.perf_counter() - started) * 1000.0,
                input_tokens=comp_stats.get("original_tokens", 0),
                compressed_tokens=comp_stats.get("compressed_tokens", 0),
                details={"error": str(exc.detail)[:160]},
                persist=False,
            )
            raise

        # Ghi nhận token đầu ra thực tế vào Token Bucket (tránh thất thoát hạn mức TPM)
        output_tokens = max(0, int(tokens_used) - prompt_input_tokens)
        await quota_manager.charge_tokens(
            tenant_str,
            output_tokens,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
        )

        await TelemetryService.record_event(
            db,
            event_type="chat_query",
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            model_name=settings.LLM_MODEL if request.mode in ("real", "hybrid") else request.mode,
            mode=request.mode,
            status_code=200,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            input_tokens=comp_stats.get("original_tokens", 0),
            compressed_tokens=comp_stats.get("compressed_tokens", 0),
            output_tokens=output_tokens,
            details={
                "savings_percent": comp_stats.get("savings_percent", 0.0),
                "retained_chunks": comp_stats.get("retained_chunks", 0),
                "retrieved_count": len(retrieved_chunks),
                "history_turns": len(history_turns),
            },
            persist=True,
        )

        await ChatHistoryService.record_turn(
            db,
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            session_id=session_id,
            query=request.query,
            answer=answer,
            contexts=[
                {
                    "child_chunk_id": str(r.child_chunk_id),
                    "parent_chunk_id": str(r.parent_chunk_id),
                    "document_id": str(r.document_id),
                    "document_title": r.document_title,
                    "heading": r.heading,
                    "text": r.parent_content,
                    "rrf_score": r.rrf_score,
                    "dense_score": r.dense_score,
                    "sparse_score": r.sparse_score,
                }
                for r in retrieved_chunks
            ],
            citations=[c.model_dump(mode="json") for c in citations],
            chunk_ids=[str(r.child_chunk_id) for r in retrieved_chunks],
            llm_context=filtered_context,
            model_name=settings.LLM_MODEL if request.mode in ("real", "hybrid") else request.mode,
            mode=request.mode,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            input_tokens=comp_stats.get("original_tokens", 0),
            compressed_tokens=comp_stats.get("compressed_tokens", 0),
            output_tokens=output_tokens,
            compression_savings_percent=float(comp_stats.get("savings_percent", 0.0)),
        )

        return ChatQueryResponse(
            answer=answer,
            query=request.query,
            session_id=session_id,
            retrieved_chunks=retrieved_chunks,
            citations=citations,
            tokens_used=tokens_used,
            compression_stats=comp_stats,
            mode=request.mode
        )
