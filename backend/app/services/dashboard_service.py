"""
MÔ TẢ KIẾN TRÚC:
Service Layer điều phối dữ liệu cho Dashboards 3 cấp, Analytics thời gian thực,
Giám sát lưu trữ DB vs FAISS mmap theo từng Tenant, và Limit API Key / Model (SEC-026).
"""

import json
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, FrozenSet, Optional, List, AsyncGenerator
from sqlalchemy import select, func, text, cast, LargeBinary
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.access import apply_document_department_scope
from app.models.tenant import Tenant
from app.models.user import User
from app.models.membership import Membership
from app.models.department import Department
from app.models.document import Document
from app.models.chunk import DocumentParentChunk, DocumentChildChunk
from app.models.feedback import ChatFeedback
from app.models.telemetry import TelemetryLog
from app.schemas.dashboard import (
    PlatformAdminDashboardResponse,
    BusinessAdminDashboardResponse,
    EmployeeWorkspaceDashboardResponse,
    TenantStorageBreakdown,
    LiveQuotaStatus,
    TenantAnalyticsItem,
    PlatformAnalyticsResponse,
    TenantAnalyticsResponse,
    TelemetryLogsResponse,
)
from app.services.faiss_service import FaissService
from app.services.quota_service import quota_manager, resolve_tenant_limits
from app.services.telemetry_service import TelemetryService


def _byte_length_expr(db: AsyncSession, column):
    """Chọn hàm tính kích thước byte UTF-8 chính xác cho PostgreSQL (octet_length) và SQLite (CAST AS BLOB)."""
    dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
    return func.octet_length(column) if dialect == "postgresql" else func.length(cast(column, LargeBinary))


class DashboardService:
    """Truy vấn dữ liệu tổng hợp phân tầng và analytics thời gian thực cho 3 cấp người dùng."""

    @classmethod
    async def get_platform_admin_dashboard(cls, db: AsyncSession) -> PlatformAdminDashboardResponse:
        """Lấy dữ liệu tổng quan toàn bộ nền tảng SaaS."""
        db_alive = (await db.execute(text("SELECT 1"))).scalar_one() == 1
        total_tenants = (await db.execute(select(func.count(Tenant.id)))).scalar_one() or 0
        active_tenants = (await db.execute(select(func.count(Tenant.id)).where(Tenant.is_active.is_(True)))).scalar_one() or 0
        total_users = (await db.execute(select(func.count(User.id)))).scalar_one() or 0
        total_documents = (await db.execute(select(func.count(Document.id)))).scalar_one() or 0
        total_bytes = (await db.execute(select(func.sum(Document.file_size_bytes)))).scalar_one() or 0

        return PlatformAdminDashboardResponse(
            total_tenants=total_tenants,
            active_tenants=active_tenants,
            total_users=total_users,
            total_documents=total_documents,
            total_storage_bytes=int(total_bytes),
            system_health="OPERATIONAL" if db_alive else "DEGRADED",
        )

    @classmethod
    async def get_business_admin_dashboard(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID
    ) -> BusinessAdminDashboardResponse:
        """Lấy dữ liệu thống kê nội bộ của một Tenant."""
        tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()

        total_members = (await db.execute(
            select(func.count(Membership.id)).where(Membership.tenant_id == tenant_id, Membership.is_active.is_(True))
        )).scalar_one() or 0

        dept_count = (await db.execute(
            select(func.count(Department.id)).where(Department.tenant_id == tenant_id)
        )).scalar_one() or 0

        doc_count = (await db.execute(
            select(func.count(Document.id)).where(Document.tenant_id == tenant_id)
        )).scalar_one() or 0

        doc_bytes = (await db.execute(
            select(func.sum(Document.file_size_bytes)).where(Document.tenant_id == tenant_id)
        )).scalar_one() or 0

        up_count = (await db.execute(
            select(func.count(ChatFeedback.id)).where(
                ChatFeedback.tenant_id == tenant_id, ChatFeedback.rating == "THUMBS_UP"
            )
        )).scalar_one() or 0

        down_count = (await db.execute(
            select(func.count(ChatFeedback.id)).where(
                ChatFeedback.tenant_id == tenant_id, ChatFeedback.rating == "THUMBS_DOWN"
            )
        )).scalar_one() or 0

        return BusinessAdminDashboardResponse(
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            total_members=total_members,
            departments_count=dept_count,
            total_documents=doc_count,
            total_storage_bytes=int(doc_bytes),
            quota_limits=tenant.quota_limits,
            feedback_summary={
                "thumbs_up": up_count,
                "thumbs_down": down_count
            }
        )

    @classmethod
    async def get_employee_workspace_dashboard(
        cls,
        db: AsyncSession,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        allowed_department_ids: Optional[FrozenSet[uuid.UUID]] = None,
    ) -> EmployeeWorkspaceDashboardResponse:
        """Lấy thông tin không gian làm việc của nhân viên (số tài liệu theo Department ACL)."""
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
        tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()

        mem_res = await db.execute(
            select(Membership).where(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
        )
        membership = mem_res.scalar_one_or_none()

        dept_name = None
        if membership and membership.department_id:
            dept_res = await db.execute(select(Department).where(Department.id == membership.department_id))
            dept = dept_res.scalar_one_or_none()
            dept_name = dept.name if dept else None

        doc_query = select(func.count(Document.id)).where(Document.tenant_id == tenant_id)
        doc_query = apply_document_department_scope(doc_query, Document.department_id, allowed_department_ids)
        doc_count = (await db.execute(doc_query)).scalar_one() or 0

        return EmployeeWorkspaceDashboardResponse(
            user_name=user.full_name,
            email=user.email,
            tenant_name=tenant.name,
            role=membership.role if membership else "EMPLOYEE",
            department_name=dept_name,
            accessible_documents_count=doc_count
        )

    @classmethod
    async def _build_tenant_storage_breakdown(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
    ) -> TenantStorageBreakdown:
        """Đo dung lượng thật theo từng Tenant ở cả Database lẫn FAISS .index trên đĩa."""
        doc_row = (await db.execute(
            select(
                func.count(Document.id),
                func.coalesce(func.sum(Document.file_size_bytes), 0),
                func.coalesce(func.sum(_byte_length_expr(db, Document.raw_text)), 0),
            ).where(Document.tenant_id == tenant_id)
        )).one()

        p_row = (await db.execute(
            select(
                func.count(DocumentParentChunk.id),
                func.coalesce(func.sum(_byte_length_expr(db, DocumentParentChunk.content)), 0),
            ).where(DocumentParentChunk.tenant_id == tenant_id)
        )).one()

        c_row = (await db.execute(
            select(
                func.count(DocumentChildChunk.id),
                func.coalesce(func.sum(_byte_length_expr(db, DocumentChildChunk.content)), 0),
            ).where(DocumentChildChunk.tenant_id == tenant_id)
        )).one()

        faiss_stats = await FaissService.get_index_stats(tenant_id)
        raw_bytes = int(doc_row[2] or 0)
        chunks_bytes = int(p_row[1] or 0) + int(c_row[1] or 0)

        return TenantStorageBreakdown(
            documents_count=int(doc_row[0] or 0),
            parent_chunks_count=int(p_row[0] or 0),
            child_chunks_count=int(c_row[0] or 0),
            uploaded_files_bytes=int(doc_row[1] or 0),
            db_raw_text_bytes=raw_bytes,
            db_chunks_text_bytes=chunks_bytes,
            total_db_bytes=raw_bytes + chunks_bytes,
            faiss_index_exists=bool(faiss_stats["index_exists"]),
            faiss_index_bytes=int(faiss_stats["file_size_bytes"]),
            faiss_vectors_count=int(faiss_stats["vector_count"]),
            faiss_dimension=int(faiss_stats["dimension"]),
        )

    @classmethod
    async def get_platform_analytics(cls, db: AsyncSession) -> PlatformAnalyticsResponse:
        """Tổng hợp Analytics thời gian thực, dung lượng DB/FAISS từng Tenant và Limit Model/API Key."""
        db_alive = (await db.execute(text("SELECT 1"))).scalar_one() == 1
        tenants = (await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))).scalars().all()

        event_counts_rows = (await db.execute(
            select(TelemetryLog.tenant_id, func.count(TelemetryLog.id)).group_by(TelemetryLog.tenant_id)
        )).all()
        event_count_map = {str(tid): int(cnt) for tid, cnt in event_counts_rows if tid is not None}

        breakdown: List[TenantAnalyticsItem] = []
        total_docs = 0
        total_parents = 0
        total_children = 0
        total_upload_bytes = 0
        total_db_bytes = 0
        total_faiss_bytes = 0
        total_faiss_vectors = 0

        for t in tenants:
            storage = await cls._build_tenant_storage_breakdown(db, t.id)
            limits = resolve_tenant_limits(t.quota_limits)
            q_status = await quota_manager.get_tenant_status(
                str(t.id),
                rpm_limit=limits["rpm_limit"],
                tpm_limit=limits["tpm_limit"],
            )
            breakdown.append(
                TenantAnalyticsItem(
                    tenant_id=str(t.id),
                    tenant_name=t.name,
                    tenant_slug=t.slug,
                    is_active=t.is_active,
                    tier=str((t.quota_limits or {}).get("tier", "FREE")),
                    storage=storage,
                    live_quota=LiveQuotaStatus(**q_status),
                    total_logged_events=event_count_map.get(str(t.id), 0),
                )
            )
            total_docs += storage.documents_count
            total_parents += storage.parent_chunks_count
            total_children += storage.child_chunks_count
            total_upload_bytes += storage.uploaded_files_bytes
            total_db_bytes += storage.total_db_bytes
            total_faiss_bytes += storage.faiss_index_bytes
            total_faiss_vectors += storage.faiss_vectors_count

        total_logs_count = (await db.execute(select(func.count(TelemetryLog.id)))).scalar_one() or 0

        return PlatformAnalyticsResponse(
            measured_at_utc=datetime.now(timezone.utc).isoformat(),
            system_health="OPERATIONAL" if db_alive else "DEGRADED",
            storage_summary={
                "total_tenants": len(tenants),
                "total_documents": total_docs,
                "total_parent_chunks": total_parents,
                "total_child_chunks": total_children,
                "total_upload_bytes": total_upload_bytes,
                "total_db_text_bytes": total_db_bytes,
                "total_faiss_bytes": total_faiss_bytes,
                "total_faiss_vectors": total_faiss_vectors,
                "total_telemetry_logs": int(total_logs_count),
            },
            realtime_metrics=TelemetryService.get_realtime_metrics(tenant_id=None),
            model_api_limits=quota_manager.get_model_api_limits_snapshot(),
            tenants_breakdown=breakdown,
        )

    @classmethod
    async def get_tenant_analytics(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
    ) -> TenantAnalyticsResponse:
        """Lấy Analytics chi tiết (DB + FAISS + Quota + Telemetry) cho Quản trị viên Doanh nghiệp."""
        tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
        storage = await cls._build_tenant_storage_breakdown(db, tenant_id)
        limits = resolve_tenant_limits(tenant.quota_limits)
        q_status = await quota_manager.get_tenant_status(
            str(tenant_id),
            rpm_limit=limits["rpm_limit"],
            tpm_limit=limits["tpm_limit"],
        )

        return TenantAnalyticsResponse(
            measured_at_utc=datetime.now(timezone.utc).isoformat(),
            tenant_id=str(tenant.id),
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            is_active=tenant.is_active,
            quota_limits=tenant.quota_limits,
            storage=storage,
            live_quota=LiveQuotaStatus(**q_status),
            realtime_metrics=TelemetryService.get_realtime_metrics(tenant_id=tenant_id),
            model_api_limits=quota_manager.get_model_api_limits_snapshot(),
        )

    @classmethod
    async def get_audit_logs(
        cls,
        db: AsyncSession,
        tenant_id: Optional[uuid.UUID] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> TelemetryLogsResponse:
        """Trả về nhật ký vận hành & lưu trữ có phân lập Tenant."""
        items = await TelemetryService.list_logs(
            db,
            tenant_id=tenant_id,
            event_type=event_type,
            limit=limit,
        )
        return TelemetryLogsResponse(total_returned=len(items), logs=items)

    @classmethod
    async def stream_realtime_analytics(
        cls,
        db: AsyncSession,
        tenant_id: Optional[uuid.UUID] = None,
        interval_sec: float = 2.0,
        max_events: int = 0,
    ) -> AsyncGenerator[str, None]:
        """Phát luồng SSE ảnh chụp Analytics thời gian thực (Platform hoặc Tenant)."""
        delay = max(0.1, min(float(interval_sec), 30.0))
        emitted = 0

        while True:
            if tenant_id is None:
                snapshot = (await cls.get_platform_analytics(db)).model_dump()
            else:
                snapshot = (await cls.get_tenant_analytics(db, tenant_id)).model_dump()

            yield f"event: analytics\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
            emitted += 1
            if max_events > 0 and emitted >= max_events:
                break
            await asyncio.sleep(delay)
