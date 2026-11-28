"""CLI Entrypoint chạy Phase 3 LLM UX Simulation & MCP Supabase Verification."""

import argparse
import asyncio
import json
import logging
import os
import random
import shutil
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx

from .agent_simulator import LlmQueryBudget, simulate_single_user_session
from .config import SimConfig
from .llm_persona import get_llm_pool
from .mcp_test import run_mcp_supabase_check
from .metrics import compute_ux_metrics, render_markdown_report
from .storage import SimStorage, UxSimEvent
from .user_seeder import ensure_users_seeded_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase3_ux_sim")


@asynccontextmanager
async def _build_target_client(target_url: str) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Tạo AsyncClient trỏ tới URL thật (VPS/HTTPS) hoặc ASGI cục bộ không cần bật server."""
    if target_url != "asgi://local":
        async with httpx.AsyncClient(base_url=target_url.rstrip("/"), timeout=60.0) as client:
            yield client
        return

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    backend_dir = os.path.join(root_dir, "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.core.config import settings
    from app.core.database import get_db
    from app.main import app as fastapi_app
    from app.models.base import Base
    from app.services.faiss_service import FaissService
    import app.models  # noqa: F401

    temp_db = os.path.join(root_dir, "phase3_ux_sim", "_asgi_backend.db")
    temp_faiss = os.path.join(root_dir, "phase3_ux_sim", "_asgi_faiss")
    os.makedirs(temp_faiss, exist_ok=True)

    prev_faiss_dir = FaissService.STORAGE_DIR
    FaissService.STORAGE_DIR = temp_faiss
    # Giữ chế độ offline cho ASGI test nếu SIM_OFFLINE_LLM=true
    if os.getenv("SIM_OFFLINE_LLM", "false").lower() == "true":
        settings.EMBEDDING_API_URL = None
        settings.LLM_API_URL = None

    engine = create_async_engine(f"sqlite+aiosqlite:///{temp_db}", echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fastapi_app),
            base_url="http://phase3-local",
            timeout=60.0,
        ) as client:
            yield client
    finally:
        fastapi_app.dependency_overrides.clear()
        FaissService.STORAGE_DIR = prev_faiss_dir
        await engine.dispose()


async def run_simulation(args: argparse.Namespace) -> int:
    cfg = SimConfig()
    if args.target_url:
        cfg.target_api_url = args.target_url
    if args.target_users is not None:
        cfg.target_user_count = args.target_users
    if args.active_users is not None:
        cfg.active_users_per_run = args.active_users
    if args.steps is not None:
        cfg.steps_per_user = args.steps
    if args.offline_llm:
        cfg.offline_llm = True
        os.environ["SIM_OFFLINE_LLM"] = "true"
    if args.max_llm_queries is not None:
        cfg.max_llm_queries_per_run = max(0, args.max_llm_queries)
    if args.llm_models:
        parsed_models = [m.strip() for m in args.llm_models.split(",") if m.strip()]
        if parsed_models:
            cfg.llm_models = parsed_models

    logger.info(
        "Telemetry storage: %s | Target API: %s | Max Backend LLM queries/run: %d",
        "Supabase Cloud PostgreSQL" if cfg.is_supabase_cloud else cfg.telemetry_dsn,
        cfg.target_api_url,
        cfg.max_llm_queries_per_run,
    )

    storage = SimStorage(cfg.telemetry_dsn)
    await storage.init_schema()

    try:
        async with _build_target_client(cfg.target_api_url) as client:
            # 1. Seed 100 User ID duy nhất (1 lần)
            seeded_users = await ensure_users_seeded_once(cfg, storage, client)
            if not seeded_users:
                raise RuntimeError("Không seed hoặc nạp được tài khoản người dùng mô phỏng nào.")

            sample_size = max(1, min(cfg.active_users_per_run, len(seeded_users)))
            active_pool = random.sample(seeded_users, sample_size)
            run_id = await storage.create_run(
                target_api_url=cfg.target_api_url,
                trigger_source=args.source,
                total_seeded_users=len(seeded_users),
                active_users_count=len(active_pool),
            )

            query_budget = LlmQueryBudget(cfg.max_llm_queries_per_run)
            all_events: list[UxSimEvent] = []
            if not args.seed_only:
                sem = asyncio.Semaphore(max(1, min(5, len(active_pool))))

                async def _guarded_user(u):
                    async with sem:
                        return await simulate_single_user_session(
                            cfg=cfg,
                            storage=storage,
                            client=client,
                            run_id=run_id,
                            user=u,
                            steps_per_user=cfg.steps_per_user,
                            jitter_max_sec=0.05 if cfg.target_api_url == "asgi://local" else 0.5,
                            query_budget=query_budget,
                        )

                results = await asyncio.gather(*[_guarded_user(u) for u in active_pool])
                for ev_list in results:
                    all_events.extend(ev_list)

            # 2. Kiểm thử kết nối MCP tới Supabase Cloud DB dự án cũ
            mcp_check = None
            if not args.skip_mcp:
                owner_candidate = next((u for u in seeded_users if u.role == "OWNER"), seeded_users[0])
                mcp_check = await run_mcp_supabase_check(
                    cfg=cfg,
                    storage=storage,
                    client=client,
                    run_id=run_id,
                    owner_user=owner_candidate,
                )

            # 3. Tổng hợp UX Scorecard + xuất báo cáo
            llm_pool_stats = get_llm_pool(cfg).get_stats() if not cfg.offline_llm else {
                "models": list(cfg.llm_models),
                "mode": "offline_deterministic_fallback",
                "rpm_limit_per_model": cfg.llm_rpm_limit,
                "rpd_limit_per_model": cfg.llm_rpd_limit,
                "calls_by_model": {},
                "model_failovers": 0,
                "throttle_waits": 0,
                "combined_rpd_capacity": len(cfg.llm_models) * cfg.llm_rpd_limit,
                "rpd_remaining_by_model": {m: cfg.llm_rpd_limit for m in cfg.llm_models},
            }
            metrics = compute_ux_metrics(
                events=all_events,
                mcp_check=mcp_check,
                total_seeded_users=len(seeded_users),
                active_users=len(active_pool),
                supabase_cloud=cfg.is_supabase_cloud,
                llm_pool_stats=llm_pool_stats,
                query_budget_stats=query_budget.get_stats(),
            )
            await storage.finalize_run(run_id, total_events=len(all_events), summary_metrics=metrics)

            report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
            os.makedirs(report_dir, exist_ok=True)
            md_report = render_markdown_report(run_id, cfg.target_api_url, metrics)
            with open(os.path.join(report_dir, "latest_ux_report.md"), "w", encoding="utf-8") as f_md:
                f_md.write(md_report)
            with open(os.path.join(report_dir, "latest_ux_report.json"), "w", encoding="utf-8") as f_js:
                json.dump({"run_id": run_id, "metrics": metrics}, f_js, ensure_ascii=False, indent=2)

            summary_path = os.getenv("GITHUB_STEP_SUMMARY")
            if summary_path:
                with open(summary_path, "a", encoding="utf-8") as f_gh:
                    f_gh.write(md_report + "\n")

            print(md_report)
            if mcp_check and not mcp_check.passed:
                logger.error("MCP Supabase Check thất bại: %s", mcp_check.details)
                return 2
            return 0
    finally:
        await storage.dispose()


def _enable_utf8_stdout() -> None:
    """Console Windows cp1258 không in được tiếng Việt; chuyển stdout/stderr sang UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    _enable_utf8_stdout()
    parser = argparse.ArgumentParser(description="Phase 3 UX Simulation & Supabase MCP Verification")
    parser.add_argument("--target-url", default=None, help="URL API Backend thật hoặc asgi://local")
    parser.add_argument("--target-users", type=int, default=None, help="Tổng số user seed (mặc định 100)")
    parser.add_argument("--active-users", type=int, default=None, help="Số user chạy phiên UX trong run này")
    parser.add_argument("--steps", type=int, default=None, help="Số hành động LLM mỗi user")
    parser.add_argument("--seed-only", action="store_true", help="Chỉ chạy bước seed user 1 lần")
    parser.add_argument("--skip-mcp", action="store_true", help="Bỏ qua kiểm thử MCP external DB")
    parser.add_argument("--offline-llm", action="store_true", help="Chạy fallback không gọi Google API ngoài")
    parser.add_argument("--max-llm-queries", type=int, default=None, help="Trần số lượt hỏi LLM RAG toàn run (mặc định 20)")
    parser.add_argument("--llm-models", default=None, help="Danh sách model Simulator xoay vòng, phân tách bằng dấu phẩy")
    parser.add_argument("--source", default="local", help="Nguồn chạy: local | github-actions")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_simulation(args)))


if __name__ == "__main__":
    main()
