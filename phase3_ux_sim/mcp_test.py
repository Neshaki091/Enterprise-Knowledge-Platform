"""Kiểm thử kết nối MCP Zero-Knowledge với DB Supabase Cloud dự án cũ của doanh nghiệp."""

import json
import time
from typing import Optional
import httpx

from .config import SimConfig, normalize_async_pg_url
from .storage import SimStorage, UxSimMcpCheck, UxSimUser


async def run_mcp_supabase_check(
    cfg: SimConfig,
    storage: SimStorage,
    client: httpx.AsyncClient,
    run_id: str,
    owner_user: UxSimUser,
) -> Optional[UxSimMcpCheck]:
    """
    Đăng ký Supabase Cloud DB (hoặc DB telemetry đang chạy nếu test offline) vào
    POST /api/v1/mcp/external-db, xin db_jwt, chạy query_enterprise_db và kiểm tra
    lớp chặn Read-Only / whitelist bảng.
    """
    t0 = time.perf_counter()
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": owner_user.email, "password": owner_user.password},
    )
    if login_res.status_code != 200:
        check = UxSimMcpCheck(
            run_id=run_id,
            tenant_id=owner_user.tenant_id,
            sql_executed=cfg.mcp_test_sql,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            passed=False,
            details={"error": f"Owner login failed: {login_res.status_code}"},
        )
        await storage.record_mcp_check(check)
        return check

    headers = {"Authorization": f"Bearer {login_res.json()['access_token']}"}
    dsn = cfg.mcp_target_dsn
    db_type = "postgresql" if "postgresql" in dsn else "sqlite"

    connector_id: Optional[str] = None
    try:
        reg_res = await client.post(
            "/api/v1/mcp/external-db",
            json={
                "name": f"Supabase_Phase3_{run_id[:6]}",
                "db_type": db_type,
                "connection_uri": dsn,
                "tenant_secret": cfg.mcp_tenant_secret,
                "allowed_tables": cfg.mcp_allowed_tables,
            },
            headers=headers,
        )
        if reg_res.status_code != 201:
            check = UxSimMcpCheck(
                run_id=run_id,
                tenant_id=owner_user.tenant_id,
                sql_executed=cfg.mcp_test_sql,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                passed=False,
                details={"step": "register", "status": reg_res.status_code, "body": reg_res.text[:250]},
            )
            await storage.record_mcp_check(check)
            return check

        connector_id = reg_res.json()["id"]

        tok_res = await client.post(
            "/api/v1/mcp/external-db/token",
            json={
                "connector_id": connector_id,
                "tenant_secret": cfg.mcp_tenant_secret,
                "expires_minutes": 15,
            },
            headers=headers,
        )
        if tok_res.status_code != 200:
            check = UxSimMcpCheck(
                run_id=run_id,
                tenant_id=owner_user.tenant_id,
                connector_id=connector_id,
                sql_executed=cfg.mcp_test_sql,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                passed=False,
                details={"step": "issue_db_jwt", "status": tok_res.status_code, "body": tok_res.text[:250]},
            )
            await storage.record_mcp_check(check)
            return check

        db_jwt = tok_res.json()["db_jwt"]

        # 1. Chạy SELECT Read-Only thật qua MCP tool `query_enterprise_db`
        q_res = await client.post(
            "/api/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 9001,
                "method": "tools/call",
                "params": {
                    "name": "query_enterprise_db",
                    "arguments": {
                        "connector_id": connector_id,
                        "db_jwt": db_jwt,
                        "tenant_secret": cfg.mcp_tenant_secret,
                        "sql_query": cfg.mcp_test_sql,
                    },
                },
            },
            headers=headers,
        )
        q_json = q_res.json().get("result", {})
        ok_select = q_res.status_code == 200 and q_json.get("isError") is False
        row_count = 0
        if ok_select:
            parsed = json.loads(q_json["content"][0]["text"])
            row_count = int(parsed.get("row_count", 0))

        # 2. Xác minh lớp bảo vệ chặn DDL / bảng ngoài whitelist
        target_tbl = cfg.mcp_allowed_tables[0] if cfg.mcp_allowed_tables else "ux_sim_events"
        bad_res = await client.post(
            "/api/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 9002,
                "method": "tools/call",
                "params": {
                    "name": "query_enterprise_db",
                    "arguments": {
                        "connector_id": connector_id,
                        "db_jwt": db_jwt,
                        "tenant_secret": cfg.mcp_tenant_secret,
                        "sql_query": f"DROP TABLE {target_tbl}",
                    },
                },
            },
            headers=headers,
        )
        blocked_ddl = bad_res.status_code == 200 and bad_res.json().get("result", {}).get("isError") is True

        check = UxSimMcpCheck(
            run_id=run_id,
            tenant_id=owner_user.tenant_id,
            connector_id=connector_id,
            sql_executed=cfg.mcp_test_sql,
            row_count=row_count,
            latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            readonly_guard_blocked=blocked_ddl,
            passed=bool(ok_select and blocked_ddl),
            details={
                "db_type": db_type,
                "supabase_cloud": cfg.is_supabase_cloud,
                "separate_mcp_db": cfg.has_separate_mcp_db,
                "allowed_tables": cfg.mcp_allowed_tables,
                "select_ok": ok_select,
                "ddl_blocked": blocked_ddl,
            },
        )
        await storage.record_mcp_check(check)
        return check
    finally:
        if connector_id:
            await client.delete(f"/api/v1/mcp/external-db/{connector_id}", headers=headers)
