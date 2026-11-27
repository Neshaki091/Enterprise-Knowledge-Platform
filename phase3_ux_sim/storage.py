"""Lưu trữ toàn bộ kết quả mô phỏng UX (Users, Runs, Events, MCP Checks) lên Supabase Cloud DB."""

import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import (
    String,
    Integer,
    Float,
    Text,
    Boolean,
    DateTime,
    JSON,
    select,
    func,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SimBase(DeclarativeBase):
    """Base ORM riêng cho công cụ Phase 3 UX Simulation."""


class UxSimUser(SimBase):
    """Danh sách ~100 tài khoản giả lập được sinh 1 lần bởi LLM (không tạo trùng)."""
    __tablename__ = "ux_sim_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    backend_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tenant_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    username: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    department_name: Mapped[str] = mapped_column(String(100), nullable=False)
    persona_goal: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )


class UxSimRun(SimBase):
    """Thông tin mỗi phiên chạy mô phỏng UX (từ local hoặc GitHub Actions)."""
    __tablename__ = "ux_sim_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    target_api_url: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_source: Mapped[str] = mapped_column(String(64), default="local", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_seeded_users: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_users_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary_metrics: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class UxSimEvent(SimBase):
    """Lưu chi tiết từng hành động, câu hỏi, câu trả lời LLM và context theo timestamp."""
    __tablename__ = "ux_sim_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    timestamp_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    context: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    http_status: Mapped[int] = mapped_column(Integer, default=200, nullable=False)
    ttfb_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_compressed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    feedback_rating: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    llm_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class UxSimMcpCheck(SimBase):
    """Lưu kết quả kiểm thử MCP Zero-Knowledge tới Supabase Cloud DB dự án cũ."""
    __tablename__ = "ux_sim_mcp_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sql_executed: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    readonly_guard_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    details: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class SimStorage:
    """Lớp quản lý kết nối Supabase Cloud PostgreSQL (hỗ trợ cả PgBouncer port 6543)."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        connect_args: Dict[str, Any] = {}
        if "asyncpg" in dsn:
            # Bắt buộc statement_cache_size=0 khi đi qua Supabase Transaction Pooler (port 6543)
            connect_args["statement_cache_size"] = 0
        self.engine: AsyncEngine = create_async_engine(
            dsn,
            echo=False,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def init_schema(self) -> None:
        """Tạo các bảng telemetry UX trên Supabase Cloud DB nếu chưa tồn tại."""
        async with self.engine.begin() as conn:
            await conn.run_sync(SimBase.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def count_users(self) -> int:
        async with self.session_factory() as db:
            return int((await db.execute(select(func.count(UxSimUser.id)))).scalar_one() or 0)

    async def list_users(self, limit: Optional[int] = None) -> List[UxSimUser]:
        async with self.session_factory() as db:
            stmt = select(UxSimUser).order_by(UxSimUser.created_at.asc())
            if limit is not None:
                stmt = stmt.limit(limit)
            return list((await db.execute(stmt)).scalars().all())

    async def add_users(self, users: List[UxSimUser]) -> int:
        if not users:
            return 0
        inserted = 0
        async with self.session_factory() as db:
            existing_emails = set(
                (await db.execute(select(UxSimUser.email))).scalars().all()
            )
            for u in users:
                if u.email not in existing_emails:
                    db.add(u)
                    existing_emails.add(u.email)
                    inserted += 1
            await db.commit()
        return inserted

    async def create_run(
        self,
        target_api_url: str,
        trigger_source: str,
        total_seeded_users: int,
        active_users_count: int,
    ) -> str:
        run_obj = UxSimRun(
            target_api_url=target_api_url,
            trigger_source=trigger_source,
            total_seeded_users=total_seeded_users,
            active_users_count=active_users_count,
        )
        async with self.session_factory() as db:
            db.add(run_obj)
            await db.commit()
            return run_obj.id

    async def record_event(self, event: UxSimEvent) -> None:
        async with self.session_factory() as db:
            db.add(event)
            await db.commit()

    async def record_mcp_check(self, check: UxSimMcpCheck) -> None:
        async with self.session_factory() as db:
            db.add(check)
            await db.commit()

    async def finalize_run(
        self,
        run_id: str,
        total_events: int,
        summary_metrics: Dict[str, Any],
    ) -> None:
        async with self.session_factory() as db:
            run_obj = (await db.execute(select(UxSimRun).where(UxSimRun.id == run_id))).scalar_one()
            run_obj.finished_at = datetime.now(timezone.utc)
            run_obj.total_events = total_events
            run_obj.summary_metrics = summary_metrics
            await db.commit()
