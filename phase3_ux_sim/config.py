"""Cấu hình cho Phase 3 UX Simulation Tool (độc lập với backend runtime)."""

import hashlib
import os
from dataclasses import dataclass, field
from typing import List
from dotenv import dotenv_values


def _load_env_files() -> None:
    """Nạp env ưu tiên phase3_ux_sim/.env_vu > phase3_ux_sim/.env > .env > backend/.env (bỏ qua chuỗi rỗng)."""
    for path in ("phase3_ux_sim/.env_vu", "phase3_ux_sim/.env", ".env", "backend/.env"):
        if not os.path.exists(path):
            continue
        for key, val in dotenv_values(path).items():
            if key and val is not None and val != "" and not os.environ.get(key):
                os.environ[key] = val


_load_env_files()


def normalize_async_pg_url(raw_url: str) -> str:
    """Chuẩn hóa URI PostgreSQL/Supabase sang driver postgresql+asyncpg."""
    url = raw_url.strip()
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def _default_llm_models() -> List[str]:
    raw_multi = os.getenv("SIM_LLM_MODELS", "").strip()
    if raw_multi:
        models = [m.strip() for m in raw_multi.split(",") if m.strip()]
        if models:
            return models
    raw_single = os.getenv("SIM_LLM_MODEL", "").strip()
    if raw_single:
        return [raw_single]
    # Tách riêng Simulator khỏi Gemma Backend (gemma-4-26b-a4b-it) bằng 2 model Flash-Lite độc lập RPD/RPM
    return ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]


@dataclass
class SimConfig:
    """Tham số chạy mô phỏng UX và kết nối Supabase Cloud."""

    target_api_url: str = field(
        default_factory=lambda: (
            os.getenv("SIM_TARGET_API_URL")
            or os.getenv("target_api_url")
            or "asgi://local"
        ).strip()
    )
    supabase_db_url: str = field(default_factory=lambda: os.getenv("SUPABASE_DB_URL", "").strip())
    local_fallback_db: str = field(
        default_factory=lambda: os.getenv(
            "SIM_LOCAL_FALLBACK_DB",
            "sqlite+aiosqlite:///phase3_ux_sim/sim_local.db",
        )
    )
    google_api_key: str = field(
        default_factory=lambda: (os.getenv("SIM_LLM_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    )
    llm_api_url: str = field(
        default_factory=lambda: (
            os.getenv("SIM_LLM_API_URL")
            or os.getenv("LLM_API_URL")
            or "https://generativelanguage.googleapis.com"
        ).strip()
    )
    llm_models: List[str] = field(default_factory=_default_llm_models)
    llm_rpm_limit: int = field(default_factory=lambda: max(1, int(os.getenv("SIM_LLM_RPM_LIMIT", "25"))))
    llm_rpd_limit: int = field(default_factory=lambda: max(1, int(os.getenv("SIM_LLM_RPD_LIMIT", "480"))))

    target_user_count: int = field(default_factory=lambda: int(os.getenv("SIM_USER_COUNT", "100")))
    users_per_tenant: int = field(default_factory=lambda: int(os.getenv("SIM_USERS_PER_TENANT", "10")))
    active_users_per_run: int = field(default_factory=lambda: int(os.getenv("SIM_ACTIVE_USERS", "20")))
    steps_per_user: int = field(default_factory=lambda: int(os.getenv("SIM_STEPS_PER_USER", "4")))
    max_llm_queries_per_run: int = field(
        default_factory=lambda: max(0, int(os.getenv("SIM_MAX_LLM_QUERIES", "20")))
    )

    mcp_db_url: str = field(default_factory=lambda: os.getenv("SIM_MCP_DB_URL", "").strip())
    mcp_tenant_secret: str = field(
        default_factory=lambda: os.getenv("SIM_TENANT_SECRET", "").strip()
    )
    mcp_allowed_tables: List[str] = field(
        default_factory=lambda: [
            t.strip()
            for t in os.getenv(
                "SIM_MCP_ALLOWED_TABLES",
                "ux_sim_events,ux_sim_users,ux_sim_runs",
            ).split(",")
            if t.strip()
        ]
    )
    mcp_test_sql: str = field(
        default_factory=lambda: os.getenv(
            "SIM_MCP_TEST_SQL",
            "SELECT id, user_id, action_type, http_status, total_latency_ms FROM ux_sim_events ORDER BY timestamp_utc DESC LIMIT 5",
        )
    )
    offline_llm: bool = field(
        default_factory=lambda: os.getenv("SIM_OFFLINE_LLM", "false").lower() == "true"
    )

    @property
    def llm_model(self) -> str:
        """Giữ tương thích ngược trả về model ưu tiên đầu tiên trong pool."""
        return self.llm_models[0] if self.llm_models else "gemini-3.1-flash-lite"

    @property
    def telemetry_dsn(self) -> str:
        """Trả về DSN Supabase Cloud nếu có, ngược lại dùng SQLite cục bộ.

        Với target remote, dùng file SQLite riêng theo hash URL để tránh dùng lại
        danh sách user cũ khi đổi target hoặc backend bị reset DB.
        """
        if self.supabase_db_url:
            return normalize_async_pg_url(self.supabase_db_url)
        explicit = os.getenv("SIM_LOCAL_FALLBACK_DB", "").strip()
        if explicit:
            return explicit
        if self.target_api_url != "asgi://local":
            slug = hashlib.sha1(self.target_api_url.rstrip("/").encode("utf-8")).hexdigest()[:8]
            return f"sqlite+aiosqlite:///phase3_ux_sim/sim_target_{slug}.db"
        return self.local_fallback_db

    @property
    def mcp_target_dsn(self) -> str:
        """Trả về DSN Supabase Cloud dùng cho test MCP (ưu tiên SIM_MCP_DB_URL, fallback telemetry_dsn)."""
        if self.mcp_db_url:
            return normalize_async_pg_url(self.mcp_db_url)
        return self.telemetry_dsn

    @property
    def is_supabase_cloud(self) -> bool:
        return bool(self.supabase_db_url)

    @property
    def has_separate_mcp_db(self) -> bool:
        return bool(self.mcp_db_url)
