"""
Script kiểm thử toàn diện (Comprehensive End-to-End API Test Runner) cho toàn bộ hệ thống.
Chạy độc lập với pytest, kiểm tra tất cả Endpoints (Sprint 1 -> Sprint 8), Security (BOLA/IDOR, RBAC, Auth),
Edge cases (file lỗi, tenant bị khóa, xóa document, concurrency/rate limit) và ghi nhận toàn bộ lỗi
vào `endpoint_test/endpoint_test_report_<YYYY-MM-DD>.md` (ngày local của máy chạy) và
`endpoint_test/latest_results.json`.

Cách chạy:
  .venv\\Scripts\\python endpoint_test/run_all_endpoint_tests.py
  .venv\\Scripts\\python endpoint_test/run_all_endpoint_tests.py --url http://localhost:8000
"""

import os
import re
import sys
import json
import time
import uuid
import shutil
import asyncio
import argparse
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable, Awaitable

# Console Windows mặc định (cp1258) không mã hóa được emoji -> ép UTF-8 để runner không crash.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Thêm thư mục backend vào sys.path để import app khi chạy chế độ local ASGI
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(PROJECT_ROOT, "backend")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import update


class TestResult:
    def __init__(
        self,
        test_id: str,
        group: str,
        name: str,
        method: str,
        endpoint: str,
        expected: str,
        actual: str,
        status: str,  # PASS | FAIL | ERROR | SECURITY_FLAW
        latency_ms: float,
        details: str = "",
        severity: str = "INFO",  # INFO | LOW | MEDIUM | HIGH | CRITICAL
    ):
        self.test_id = test_id
        self.group = group
        self.name = name
        self.method = method
        self.endpoint = endpoint
        self.expected = expected
        self.actual = actual
        self.status = status
        self.latency_ms = round(latency_ms, 2)
        self.details = details
        self.severity = severity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_id": self.test_id,
            "group": self.group,
            "name": self.name,
            "method": self.method,
            "endpoint": self.endpoint,
            "expected": self.expected,
            "actual": self.actual,
            "status": self.status,
            "severity": self.severity,
            "latency_ms": self.latency_ms,
            "details": self.details,
        }


class ComprehensiveTester:
    def __init__(
        self,
        base_url: Optional[str] = None,
        report_name: Optional[str] = None,
        json_name: str = "latest_results.json",
    ):
        if report_name and not re.fullmatch(r"endpoint_test_report_\d{4}-\d{2}-\d{2}\.md", report_name):
            raise ValueError(
                "Tên báo cáo không hợp lệ. Bắt buộc dùng endpoint_test_report_<YYYY-MM-DD>.md"
            )
        self.base_url = base_url
        self.report_name = report_name
        self.json_name = json_name
        self.results: List[TestResult] = []
        self.state: Dict[str, Any] = {}
        self.temp_faiss_dir = os.path.join(PROJECT_ROOT, "endpoint_test", "_temp_faiss")
        self.temp_db_path = os.path.join(PROJECT_ROOT, "endpoint_test", "_temp_test.db")
        self.engine = None
        self.session_factory = None
        self.fastapi_app = None

    async def setup_local_asgi(self):
        from app.main import app as fastapi_app
        from app.core.config import settings
        from app.core.database import get_db
        from app.models.base import Base
        from app.services.faiss_service import FaissService
        import app.models  # noqa: F401

        # Dọn dẹp DB/FAISS tạm trước khi chạy
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)
        if os.path.exists(self.temp_faiss_dir):
            shutil.rmtree(self.temp_faiss_dir, ignore_errors=True)
        os.makedirs(self.temp_faiss_dir, exist_ok=True)

        # Trỏ FAISS storage tạm vào endpoint_test/_temp_faiss và cô lập offline cho test suite $0
        FaissService.STORAGE_DIR = self.temp_faiss_dir
        settings.EMBEDDING_API_URL = None
        settings.LLM_API_URL = None

        db_url = f"sqlite+aiosqlite:///{self.temp_db_path}"
        self.engine = create_async_engine(db_url, echo=False)
        self.session_factory = async_sessionmaker(
            bind=self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def override_get_db():
            async with self.session_factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
                finally:
                    await session.close()

        fastapi_app.dependency_overrides[get_db] = override_get_db
        self.fastapi_app = fastapi_app

        # Khởi tạo sẵn 1 Platform Super-Admin để kiểm thử nhóm endpoints quản trị nền tảng (/tenants)
        from app.services.auth_service import AuthService
        from app.schemas.auth import UserRegisterRequest
        from app.models.user import User
        async with self.session_factory() as db:
            su, st, _ = await AuthService.register(db, UserRegisterRequest(
                email="platform_su@e2e.com",
                password="SuperPassword123!",
                full_name="Platform Super Admin",
                tenant_name="Platform Root Org",
                tenant_slug="platform-root-org"
            ))
            await db.execute(update(User).where(User.id == su.id).values(is_superuser=True))
            await db.commit()
            su_tokens = AuthService.create_tokens(su.id, st.id, "SUPER_ADMIN")
            self.state["superuser_token"] = su_tokens.access_token

    async def teardown_local_asgi(self):
        if self.fastapi_app:
            self.fastapi_app.dependency_overrides.clear()
        if self.engine:
            await self.engine.dispose()
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except OSError:
                pass
        if os.path.exists(self.temp_faiss_dir):
            shutil.rmtree(self.temp_faiss_dir, ignore_errors=True)

    async def run_case(
        self,
        test_id: str,
        group: str,
        name: str,
        method: str,
        endpoint: str,
        expected: str,
        fn: Callable[[AsyncClient], Awaitable[Dict[str, Any]]],
        client: AsyncClient,
        severity_on_fail: str = "HIGH",
    ):
        t0 = time.perf_counter()
        try:
            outcome = await fn(client)
            latency = (time.perf_counter() - t0) * 1000.0
            ok = outcome.get("ok", False)
            status = "PASS" if ok else outcome.get("fail_type", "FAIL")
            res = TestResult(
                test_id=test_id,
                group=group,
                name=name,
                method=method,
                endpoint=endpoint,
                expected=expected,
                actual=outcome.get("actual", ""),
                status=status,
                latency_ms=latency,
                details=outcome.get("details", ""),
                severity="INFO" if ok else outcome.get("severity", severity_on_fail),
            )
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000.0
            tb = traceback.format_exc()
            res = TestResult(
                test_id=test_id,
                group=group,
                name=name,
                method=method,
                endpoint=endpoint,
                expected=expected,
                actual=f"Unhandled Exception: {type(exc).__name__}: {exc}",
                status="ERROR",
                latency_ms=latency,
                details=tb,
                severity="CRITICAL",
            )
        self.results.append(res)
        icon = "✅" if res.status == "PASS" else ("🚨" if res.severity == "CRITICAL" else "❌")
        print(f"{icon} [{res.test_id}] {res.method} {res.endpoint} -> {res.status} ({res.latency_ms}ms) | {res.actual}")

    async def execute_all(self):
        if self.base_url:
            client_ctx = AsyncClient(base_url=self.base_url, timeout=30.0)
        else:
            await self.setup_local_asgi()
            client_ctx = AsyncClient(
                transport=ASGITransport(app=self.fastapi_app, raise_app_exceptions=False),
                base_url="http://testserver",
                timeout=30.0,
            )

        async with client_ctx as client:
            await self._run_group_a_system(client)
            await self._run_group_b_tenants(client)
            await self._run_group_c_auth(client)
            await self._run_group_d_documents(client)
            if not self.base_url and (not self.state.get("token_a") or not self.state.get("doc_a_id")):
                await self._ensure_fallback_seed_data()
            await self._run_group_e_search(client)
            await self._run_group_f_mcp(client)
            await self._run_group_g_chat_rag(client)
            await self._run_group_h_dashboards(client)
            await self._run_group_i_static_checks(client)

        if not self.base_url:
            await self.teardown_local_asgi()

        self.write_reports()

    # =========================================================================
    # GROUP A: SYSTEM & PROBES
    # =========================================================================
    async def _run_group_a_system(self, client: AsyncClient):
        async def t_root(c: AsyncClient):
            r = await c.get("/")
            return {"ok": r.status_code == 200, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_health(c: AsyncClient):
            r = await c.get("/api/v1/health")
            ok = r.status_code == 200 and r.json().get("status") == "healthy"
            return {"ok": ok, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_openapi(c: AsyncClient):
            r = await c.get("/openapi.json")
            if r.status_code != 200:
                return {"ok": False, "actual": f"HTTP {r.status_code}", "details": r.text}
            paths = list(r.json().get("paths", {}).keys())
            missing_expected = [
                p for p in [
                    "/api/v1/auth/register",
                    "/api/v1/auth/login",
                    "/api/v1/documents/upload",
                    "/api/v1/departments",
                    "/api/v1/tenants/members",
                ]
                if p not in paths
            ]
            ok = len(missing_expected) == 0
            return {
                "ok": ok,
                "actual": f"HTTP 200, {len(paths)} routes exposed" + (f" (MISSING: {missing_expected})" if missing_expected else ""),
                "details": f"Registered routes: {paths}. Missing critical routes: {missing_expected}",
                "severity": "CRITICAL" if missing_expected else "INFO",
            }

        await self.run_case("A01", "System", "Root endpoint", "GET", "/", "HTTP 200", t_root, client)
        await self.run_case("A02", "System", "Health check probe", "GET", "/api/v1/health", "HTTP 200 healthy", t_health, client)
        await self.run_case("A03", "System", "OpenAPI routes completeness", "GET", "/openapi.json", "All module routers registered", t_openapi, client, "CRITICAL")

    # =========================================================================
    # GROUP B: TENANTS MANAGEMENT & SECURITY AUDIT
    # =========================================================================
    async def _run_group_b_tenants(self, client: AsyncClient):
        su_headers = {"Authorization": f"Bearer {self.state.get('superuser_token', '')}"} if self.state.get("superuser_token") else {}

        async def t_create_tenant(c: AsyncClient):
            slug = f"e2e-corp-{uuid.uuid4().hex[:6]}"
            r = await c.post("/api/v1/tenants", json={
                "name": "E2E Test Corp",
                "slug": slug,
                "is_active": True,
                "quota_limits": {"tier": "PRO", "max_users": 50}
            }, headers=su_headers)
            if r.status_code == 201:
                self.state["tenant_slug"] = slug
                self.state["tenant_id"] = r.json()["id"]
            return {"ok": r.status_code == 201, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_create_dup_tenant(c: AsyncClient):
            slug = self.state.get("tenant_slug", "fallback-slug")
            r = await c.post("/api/v1/tenants", json={"name": "Dup", "slug": slug}, headers=su_headers)
            return {"ok": r.status_code == 409, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_list_tenants_unauth(c: AsyncClient):
            r = await c.get("/api/v1/tenants")
            # Bảo mật: Endpoint quản lý toàn bộ danh sách Tenants KHÔNG ĐƯỢC phép public!
            is_protected = r.status_code in (401, 403)
            return {
                "ok": is_protected,
                "fail_type": "SECURITY_FLAW",
                "severity": "CRITICAL",
                "actual": f"HTTP {r.status_code} (Accessible without Bearer Token!)" if not is_protected else f"HTTP {r.status_code}",
                "details": f"Unauthenticated GET /api/v1/tenants leaked tenant data: {r.text[:300]}" if not is_protected else r.text,
            }

        async def t_suspend_tenant_unauth(c: AsyncClient):
            tid = self.state.get("tenant_id") or str(uuid.uuid4())
            r = await c.post(f"/api/v1/tenants/{tid}/suspend")
            is_protected = r.status_code in (401, 403)
            return {
                "ok": is_protected,
                "fail_type": "SECURITY_FLAW",
                "severity": "CRITICAL",
                "actual": f"HTTP {r.status_code} (Anonymous user suspended tenant!)" if not is_protected else f"HTTP {r.status_code}",
                "details": r.text,
            }

        async def t_patch_tenant_unauth(c: AsyncClient):
            tid = self.state.get("tenant_id") or str(uuid.uuid4())
            r = await c.patch(f"/api/v1/tenants/{tid}", json={"quota_limits": {"max_users": 999999}})
            is_protected = r.status_code in (401, 403)
            return {
                "ok": is_protected,
                "fail_type": "SECURITY_FLAW",
                "severity": "CRITICAL",
                "actual": f"HTTP {r.status_code} (Anonymous user modified quota_limits!)" if not is_protected else f"HTTP {r.status_code}",
                "details": r.text,
            }

        async def t_get_tenant_not_found(c: AsyncClient):
            r = await c.get(f"/api/v1/tenants/{uuid.uuid4()}", headers=su_headers)
            return {"ok": r.status_code == 404, "actual": f"HTTP {r.status_code}", "details": r.text}

        await self.run_case("B01", "Tenants", "Create tenant", "POST", "/api/v1/tenants", "HTTP 201", t_create_tenant, client)
        await self.run_case("B02", "Tenants", "Duplicate slug conflict", "POST", "/api/v1/tenants", "HTTP 409", t_create_dup_tenant, client)
        await self.run_case("B03", "Tenants Security", "List tenants requires auth (SEC-004)", "GET", "/api/v1/tenants", "HTTP 401/403 Unauthorized", t_list_tenants_unauth, client, "CRITICAL")
        await self.run_case("B04", "Tenants Security", "Suspend tenant requires Super-Admin auth", "POST", "/api/v1/tenants/{id}/suspend", "HTTP 401/403 Unauthorized", t_suspend_tenant_unauth, client, "CRITICAL")
        await self.run_case("B05", "Tenants Security", "Patch tenant quota requires auth", "PATCH", "/api/v1/tenants/{id}", "HTTP 401/403 Unauthorized", t_patch_tenant_unauth, client, "CRITICAL")
        await self.run_case("B06", "Tenants", "Get non-existent tenant", "GET", "/api/v1/tenants/{uuid}", "HTTP 404", t_get_tenant_not_found, client)

    # =========================================================================
    # GROUP C: AUTHENTICATION, JWT ROTATION & BOLA
    # =========================================================================
    async def _run_group_c_auth(self, client: AsyncClient):
        async def t_register_a(c: AsyncClient):
            r = await c.post("/api/v1/auth/register", json={
                "email": "alpha@e2e.com",
                "password": "StrongPassword123!",
                "full_name": "Alpha Owner",
                "tenant_name": "Alpha E2E Corp",
                "tenant_slug": "alpha-e2e"
            })
            if r.status_code == 201:
                self.state["user_a_id"] = r.json()["id"]
            return {"ok": r.status_code == 201, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_register_b(c: AsyncClient):
            r = await c.post("/api/v1/auth/register", json={
                "email": "beta@e2e.com",
                "password": "StrongPassword123!",
                "full_name": "Beta Owner",
                "tenant_name": "Beta E2E Corp",
                "tenant_slug": "beta-e2e"
            })
            return {"ok": r.status_code == 201, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_login_a(c: AsyncClient):
            r = await c.post("/api/v1/auth/login", json={
                "email": "alpha@e2e.com",
                "password": "StrongPassword123!"
            })
            if r.status_code == 200:
                data = r.json()
                self.state["token_a"] = data["access_token"]
                self.state["refresh_a"] = data["refresh_token"]
                self.state["tenant_a_id"] = data["tenant_id"]
            return {"ok": r.status_code == 200, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_login_b(c: AsyncClient):
            r = await c.post("/api/v1/auth/login", json={
                "email": "beta@e2e.com",
                "password": "StrongPassword123!"
            })
            if r.status_code == 200:
                data = r.json()
                self.state["token_b"] = data["access_token"]
                self.state["tenant_b_id"] = data["tenant_id"]
            return {"ok": r.status_code == 200, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_login_suspended_tenant(c: AsyncClient):
            # Tạo 1 user/tenant mới rồi suspend tenant đó -> kiểm tra có login được hay không
            await c.post("/api/v1/auth/register", json={
                "email": "suspended@e2e.com",
                "password": "StrongPassword123!",
                "full_name": "Suspended User",
                "tenant_name": "Suspended Corp",
                "tenant_slug": "suspended-corp"
            })
            lr = await c.post("/api/v1/auth/login", json={
                "email": "suspended@e2e.com",
                "password": "StrongPassword123!"
            })
            if lr.status_code != 200:
                return {"ok": False, "actual": f"Setup failed: HTTP {lr.status_code}", "details": lr.text}
            susp_tid = lr.json()["tenant_id"]
            # Suspend tenant trong DB
            if self.session_factory:
                from app.models.tenant import Tenant
                async with self.session_factory() as db:
                    await db.execute(update(Tenant).where(Tenant.id == uuid.UUID(susp_tid)).values(is_active=False))
                    await db.commit()
            # Thử login lại vào tenant đã bị suspend
            lr2 = await c.post("/api/v1/auth/login", json={
                "email": "suspended@e2e.com",
                "password": "StrongPassword123!",
                "tenant_id": susp_tid
            })
            ok = lr2.status_code in (401, 403)
            return {
                "ok": ok,
                "fail_type": "SECURITY_FLAW",
                "severity": "HIGH",
                "actual": f"HTTP {lr2.status_code} (Suspended tenant allowed login!)" if not ok else f"HTTP {lr2.status_code}",
                "details": lr2.text,
            }

        async def t_me_valid(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
            return {"ok": r.status_code == 200, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_bola_me(c: AsyncClient):
            tok_a = self.state.get("token_a", "")
            tid_b = self.state.get("tenant_b_id", str(uuid.uuid4()))
            r = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok_a}", "X-Tenant-ID": tid_b})
            return {"ok": r.status_code == 403, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_refresh_and_reuse(c: AsyncClient):
            ref1 = self.state.get("refresh_a", "")
            r1 = await c.post("/api/v1/auth/refresh", json={"refresh_token": ref1})
            if r1.status_code != 200:
                return {"ok": False, "actual": f"Refresh1 HTTP {r1.status_code}", "details": r1.text}
            rotated_access = r1.json()["access_token"]
            rotated_refresh = r1.json()["refresh_token"]
            # 1. Tái sử dụng refresh token cũ ref1 -> phải bị 401 (Reuse Detected)
            r2 = await c.post("/api/v1/auth/refresh", json={"refresh_token": ref1})
            if r2.status_code != 401:
                return {"ok": False, "actual": f"Reuse HTTP {r2.status_code} (expected 401)", "details": r2.text}
            # 2. Toàn bộ token family bị thu hồi: refresh token vừa xoay vòng cũng vô hiệu
            r3 = await c.post("/api/v1/auth/refresh", json={"refresh_token": rotated_refresh})
            if r3.status_code != 401:
                return {"ok": False, "actual": f"Family refresh HTTP {r3.status_code} (expected 401)", "details": r3.text}
            # 3. Access token vừa xoay vòng cũng bị thu hồi tức thì
            me = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {rotated_access}"})
            if me.status_code != 401:
                return {"ok": False, "actual": f"Family access /me HTTP {me.status_code} (expected 401)", "details": me.text}
            # 4. Đăng nhập lại để khôi phục phiên hợp lệ cho các test case sau
            login = await c.post("/api/v1/auth/login", json={
                "email": "alpha@e2e.com",
                "password": "StrongPassword123!"
            })
            if login.status_code != 200:
                return {"ok": False, "actual": f"Re-login HTTP {login.status_code}", "details": login.text}
            self.state["token_a"] = login.json()["access_token"]
            self.state["refresh_a"] = login.json()["refresh_token"]
            return {"ok": True, "actual": "Rotation 200, reuse 401, family revoked (refresh + access 401)", "details": r3.text}

        async def t_logout_revocation(c: AsyncClient):
            # Tạo token tạm riêng để test logout
            await c.post("/api/v1/auth/register", json={
                "email": "logout_e2e@e2e.com",
                "password": "Password123!",
                "full_name": "Logout User",
                "tenant_name": "Logout E2E Corp"
            })
            lr = await c.post("/api/v1/auth/login", json={"email": "logout_e2e@e2e.com", "password": "Password123!"})
            if lr.status_code != 200:
                return {"ok": False, "actual": f"Setup login HTTP {lr.status_code}", "details": lr.text}
            acc = lr.json()["access_token"]
            ref = lr.json()["refresh_token"]
            lo = await c.post("/api/v1/auth/logout", json={"refresh_token": ref}, headers={"Authorization": f"Bearer {acc}"})
            if lo.status_code != 200:
                return {"ok": False, "actual": f"Logout HTTP {lo.status_code}", "details": lo.text}
            me_after = await c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {acc}"})
            return {"ok": me_after.status_code == 401, "actual": f"Post-logout /me HTTP {me_after.status_code}", "details": me_after.text}

        async def t_add_dept_and_employee_login(c: AsyncClient):
            tok = self.state.get("token_a", "")
            dept_r = await c.post(
                "/api/v1/departments",
                json={"name": "Phòng Nhân Sự E2E", "code": "HR_E2E"},
                headers={"Authorization": f"Bearer {tok}"}
            )
            if dept_r.status_code != 201:
                return {"ok": False, "actual": f"Dept HTTP {dept_r.status_code}", "details": dept_r.text}
            dept_id = dept_r.json()["id"]
            mem_r = await c.post(
                "/api/v1/tenants/members",
                json={
                    "email": "employee_e2e@e2e.com",
                    "password": "EmpPassword123!",
                    "full_name": "Employee Alpha",
                    "role": "EMPLOYEE",
                    "department_id": dept_id
                },
                headers={"Authorization": f"Bearer {tok}"}
            )
            if mem_r.status_code != 201:
                return {"ok": False, "actual": f"Member HTTP {mem_r.status_code}", "details": mem_r.text}
            emp_login = await c.post(
                "/api/v1/auth/login",
                json={"email": "employee_e2e@e2e.com", "password": "EmpPassword123!"}
            )
            if emp_login.status_code == 200:
                self.state["token_employee"] = emp_login.json().get("access_token")
                self.state["employee_dept_id"] = dept_id
            ok = emp_login.status_code == 200 and emp_login.json().get("role") == "EMPLOYEE"
            return {"ok": ok, "actual": f"Emp login HTTP {emp_login.status_code}, role={emp_login.json().get('role') if emp_login.status_code==200 else None}", "details": emp_login.text}

        async def t_self_suspend_blocked(c: AsyncClient):
            tok = self.state.get("token_a", "")
            tid = self.state.get("tenant_a_id", "")
            if not tid:
                return {"ok": False, "actual": "Missing tenant_a_id", "details": ""}
            r_active = await c.patch(
                f"/api/v1/tenants/{tid}",
                json={"is_active": False},
                headers={"Authorization": f"Bearer {tok}"},
            )
            r_quota = await c.patch(
                f"/api/v1/tenants/{tid}",
                json={"quota_limits": {"max_users": 999999}},
                headers={"Authorization": f"Bearer {tok}"},
            )
            ok = r_active.status_code == 403 and r_quota.status_code == 403
            return {
                "ok": ok,
                "fail_type": "SECURITY_FLAW",
                "severity": "CRITICAL",
                "actual": f"self-suspend HTTP {r_active.status_code}, quota-edit HTTP {r_quota.status_code}",
                "details": f"is_active: {r_active.text[:200]} | quota: {r_quota.text[:200]}",
            }

        async def t_memberships_and_switch(c: AsyncClient):
            tok = self.state.get("token_a", "")
            tid_a = self.state.get("tenant_a_id", "")
            tid_b = self.state.get("tenant_b_id", str(uuid.uuid4()))
            r_list = await c.get("/api/v1/auth/memberships", headers={"Authorization": f"Bearer {tok}"})
            if r_list.status_code != 200:
                return {"ok": False, "actual": f"Memberships HTTP {r_list.status_code}", "details": r_list.text}
            tenants = [m.get("tenant_id") for m in r_list.json()]
            r_cross = await c.post(
                "/api/v1/auth/switch-tenant",
                json={"tenant_id": tid_b},
                headers={"Authorization": f"Bearer {tok}"},
            )
            r_own = await c.post(
                "/api/v1/auth/switch-tenant",
                json={"tenant_id": tid_a},
                headers={"Authorization": f"Bearer {tok}"},
            )
            ok = (
                tid_a in tenants
                and len(tenants) == 1
                and r_cross.status_code == 403
                and r_own.status_code == 200
                and bool(r_own.json().get("access_token"))
            )
            return {
                "ok": ok,
                "actual": f"memberships={len(tenants)}, cross-switch HTTP {r_cross.status_code}, own-switch HTTP {r_own.status_code}",
                "details": r_list.text[:300],
            }

        async def t_login_bruteforce_guard(c: AsyncClient):
            email = "bruteforce_e2e@e2e.com"
            codes = []
            retry_header = None
            for _ in range(6):
                r = await c.post("/api/v1/auth/login", json={"email": email, "password": "WrongPassword123!"})
                codes.append(r.status_code)
                if r.status_code == 429:
                    retry_header = r.headers.get("retry-after")
            ok = codes[:5] == [401] * 5 and codes[5] == 429 and retry_header is not None
            return {
                "ok": ok,
                "fail_type": "SECURITY_FLAW",
                "severity": "HIGH",
                "actual": f"codes={codes}, Retry-After={retry_header}",
                "details": "",
            }

        await self.run_case("C01", "Auth", "Register Tenant A", "POST", "/api/v1/auth/register", "HTTP 201", t_register_a, client, "CRITICAL")
        await self.run_case("C02", "Auth", "Register Tenant B", "POST", "/api/v1/auth/register", "HTTP 201", t_register_b, client, "CRITICAL")
        await self.run_case("C03", "Auth", "Login Tenant A", "POST", "/api/v1/auth/login", "HTTP 200", t_login_a, client, "CRITICAL")
        await self.run_case("C04", "Auth", "Login Tenant B", "POST", "/api/v1/auth/login", "HTTP 200", t_login_b, client, "CRITICAL")
        await self.run_case("C05", "Auth Security", "Block login for suspended tenant", "POST", "/api/v1/auth/login", "HTTP 401/403 Blocked", t_login_suspended_tenant, client, "HIGH")
        await self.run_case("C06", "Auth", "Get /me context", "GET", "/api/v1/auth/me", "HTTP 200", t_me_valid, client)
        await self.run_case("C07", "Auth Security", "Block BOLA cross-tenant X-Tenant-ID", "GET", "/api/v1/auth/me", "HTTP 403", t_bola_me, client, "CRITICAL")
        await self.run_case("C08", "Auth Security", "Refresh rotation + reuse + family revocation", "POST", "/api/v1/auth/refresh", "Reuse 401, family refresh/access 401", t_refresh_and_reuse, client, "CRITICAL")
        await self.run_case("C09", "Auth", "Logout token revocation", "POST", "/api/v1/auth/logout", "Post-logout /me 401", t_logout_revocation, client)
        await self.run_case("C10", "Auth & Onboarding", "Create department, add employee & login", "POST", "/api/v1/tenants/members", "HTTP 201 & Employee login 200", t_add_dept_and_employee_login, client)
        await self.run_case("C11", "Auth Security", "Tenant admin cannot self-suspend or edit quota", "PATCH", "/api/v1/tenants/{id}", "HTTP 403 for both", t_self_suspend_blocked, client, "CRITICAL")
        await self.run_case("C12", "Auth", "List memberships + tenant switch guard", "POST", "/api/v1/auth/switch-tenant", "memberships=1, cross 403, own 200", t_memberships_and_switch, client)
        await self.run_case("C13", "Auth Security", "Login brute-force guard blocks 6th attempt", "POST", "/api/v1/auth/login", "5x401 then 429 + Retry-After", t_login_bruteforce_guard, client, "HIGH")

    # =========================================================================
    # GROUP D: DOCUMENTS INGESTION & CHUNKING
    # =========================================================================
    async def _run_group_d_documents(self, client: AsyncClient):
        async def t_upload_txt(c: AsyncClient):
            tok = self.state.get("token_a", "")
            content = (
                "# CHÍNH SÁCH BẢO MẬT VÀ NGHỈ PHÉP 2026\n\n"
                "Nhân viên chính thức có 14 ngày phép có lương mỗi năm.\n"
                "Mã quy định chuẩn là QD-2026/SEC-AI.\n\n"
                "## QUY ĐỊNH LÀM VIỆC TỪ XA\n\n"
                "Kỹ sư phần mềm được làm việc từ xa tối đa 2 ngày mỗi tuần."
            )
            r = await c.post(
                "/api/v1/documents/upload",
                files={"file": ("policy_2026.txt", content.encode("utf-8"), "text/plain")},
                data={"title": "Chính Sách 2026"},
                headers={"Authorization": f"Bearer {tok}"}
            )
            if r.status_code == 201:
                self.state["doc_a_id"] = r.json()["id"]
            return {"ok": r.status_code == 201, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_upload_corrupt_pdf(c: AsyncClient):
            tok = self.state.get("token_a", "")
            # Gửi bytes rác với phần mở rộng .pdf -> kiểm tra có bị crash 500 hay trả về 400 có kiểm soát!
            r = await c.post(
                "/api/v1/documents/upload",
                files={"file": ("corrupted.pdf", b"NOT_A_REAL_PDF_BINARY_DATA_12345", "application/pdf")},
                headers={"Authorization": f"Bearer {tok}"}
            )
            ok = r.status_code in (400, 415, 422)
            return {
                "ok": ok,
                "severity": "HIGH" if r.status_code >= 500 else "MEDIUM",
                "actual": f"HTTP {r.status_code} (Unhandled crash!)" if r.status_code >= 500 else f"HTTP {r.status_code}",
                "details": r.text,
            }

        async def t_upload_corrupt_docx(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.post(
                "/api/v1/documents/upload",
                files={"file": ("corrupted.docx", b"INVALID_ZIP_FOR_DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers={"Authorization": f"Bearer {tok}"}
            )
            ok = r.status_code in (400, 415, 422)
            return {
                "ok": ok,
                "severity": "HIGH" if r.status_code >= 500 else "MEDIUM",
                "actual": f"HTTP {r.status_code} (Unhandled crash!)" if r.status_code >= 500 else f"HTTP {r.status_code}",
                "details": r.text,
            }

        async def t_upload_dup(c: AsyncClient):
            tok = self.state.get("token_a", "")
            content = (
                "# CHÍNH SÁCH BẢO MẬT VÀ NGHỈ PHÉP 2026\n\n"
                "Nhân viên chính thức có 14 ngày phép có lương mỗi năm.\n"
                "Mã quy định chuẩn là QD-2026/SEC-AI.\n\n"
                "## QUY ĐỊNH LÀM VIỆC TỪ XA\n\n"
                "Kỹ sư phần mềm được làm việc từ xa tối đa 2 ngày mỗi tuần."
            )
            r = await c.post(
                "/api/v1/documents/upload",
                files={"file": ("policy_dup.txt", content.encode("utf-8"), "text/plain")},
                headers={"Authorization": f"Bearer {tok}"}
            )
            return {"ok": r.status_code == 409, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_list_and_detail(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r_list = await c.get("/api/v1/documents", headers={"Authorization": f"Bearer {tok}"})
            if r_list.status_code != 200:
                return {"ok": False, "actual": f"List HTTP {r_list.status_code}", "details": r_list.text}
            did = self.state.get("doc_a_id")
            if not did:
                return {"ok": False, "actual": "No doc_a_id from upload", "details": ""}
            r_det = await c.get(f"/api/v1/documents/{did}", headers={"Authorization": f"Bearer {tok}"})
            return {"ok": r_det.status_code == 200 and len(r_det.json().get("parent_chunks", [])) >= 1, "actual": f"Detail HTTP {r_det.status_code}", "details": r_det.text[:400]}

        async def t_cross_tenant_doc(c: AsyncClient):
            tok_b = self.state.get("token_b", "")
            did = self.state.get("doc_a_id", str(uuid.uuid4()))
            r = await c.get(f"/api/v1/documents/{did}", headers={"Authorization": f"Bearer {tok_b}"})
            return {"ok": r.status_code in (403, 404), "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_upload_valid_pdf_and_docx(c: AsyncClient):
            import io
            import docx as python_docx
            tok = self.state.get("token_a", "")
            # 1. Tạo file PDF thật hợp lệ chuẩn bảng xref in-memory
            stream_content = b"BT /F1 12 Tf 72 720 Td (Valid PDF Policy E2E-PDF-2026) Tj ET"
            objs = [
                b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
                b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
                b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >> endobj\n",
                b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
                b"5 0 obj << /Length " + str(len(stream_content)).encode() + b" >> stream\n" + stream_content + b"\nendstream\nendobj\n",
            ]
            pdf_out = bytearray(b"%PDF-1.4\n")
            offsets = []
            for obj in objs:
                offsets.append(len(pdf_out))
                pdf_out.extend(obj)
            xref_pos = len(pdf_out)
            pdf_out.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
            for off in offsets:
                pdf_out.extend(f"{off:010d} 00000 n \n".encode())
            pdf_out.extend(f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())
            pdf_bytes = bytes(pdf_out)
            r_pdf = await c.post(
                "/api/v1/documents/upload",
                files={"file": ("valid_e2e.pdf", pdf_bytes, "application/pdf")},
                headers={"Authorization": f"Bearer {tok}"}
            )
            if r_pdf.status_code != 201:
                return {"ok": False, "actual": f"PDF HTTP {r_pdf.status_code}", "details": r_pdf.text}

            # 2. Tạo file DOCX thật hợp lệ in-memory
            doc = python_docx.Document()
            doc.add_paragraph("Quy trinh van hanh he thong AI E2E-DOCX-2026.")
            buf = io.BytesIO()
            doc.save(buf)
            r_docx = await c.post(
                "/api/v1/documents/upload",
                files={"file": ("valid_e2e.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                headers={"Authorization": f"Bearer {tok}"}
            )
            ok = r_docx.status_code == 201 and r_pdf.json().get("parent_chunk_count", 0) >= 1
            return {"ok": ok, "actual": f"PDF={r_pdf.status_code}, DOCX={r_docx.status_code}", "details": r_docx.text[:300]}

        await self.run_case("D01", "Documents", "Upload TXT document", "POST", "/api/v1/documents/upload", "HTTP 201", t_upload_txt, client, "CRITICAL")
        await self.run_case("D02", "Documents EdgeCase", "Corrupted PDF returns 400 not 500", "POST", "/api/v1/documents/upload", "HTTP 400/422", t_upload_corrupt_pdf, client, "HIGH")
        await self.run_case("D03", "Documents EdgeCase", "Corrupted DOCX returns 400 not 500", "POST", "/api/v1/documents/upload", "HTTP 400/422", t_upload_corrupt_docx, client, "HIGH")
        await self.run_case("D04", "Documents", "Duplicate SHA-256 conflict", "POST", "/api/v1/documents/upload", "HTTP 409", t_upload_dup, client)
        await self.run_case("D05", "Documents", "List & Get document hierarchical detail", "GET", "/api/v1/documents/{id}", "HTTP 200", t_list_and_detail, client)
        await self.run_case("D06", "Documents Security", "Cross-tenant document access blocked", "GET", "/api/v1/documents/{id}", "HTTP 403/404", t_cross_tenant_doc, client, "CRITICAL")
        async def _setup_finance_doc(c: AsyncClient):
            tok = self.state.get("token_a", "")
            dept_res = await c.post(
                "/api/v1/departments",
                json={"name": "Phòng Tài Chính E2E", "code": f"FIN_{uuid.uuid4().hex[:4].upper()}"},
                headers={"Authorization": f"Bearer {tok}"},
            )
            if dept_res.status_code != 201:
                return None, f"Create dept HTTP {dept_res.status_code}: {dept_res.text}"
            dept_id = dept_res.json()["id"]
            up = await c.post(
                "/api/v1/documents/upload",
                files={"file": ("finance_secret.txt", "# TÀI LIỆU MẬT TÀI CHÍNH\n\nChỉ phòng tài chính được xem FIN-SECRET-77.".encode("utf-8"), "text/plain")},
                data={"department_id": dept_id},
                headers={"Authorization": f"Bearer {tok}"},
            )
            if up.status_code != 201:
                return None, f"Upload finance doc HTTP {up.status_code}: {up.text}"
            self.state["finance_dept_id"] = dept_id
            self.state["finance_doc_id"] = up.json()["id"]
            return dept_id, ""

        async def t_employee_upload_foreign_dept(c: AsyncClient):
            emp_tok = self.state.get("token_employee", "")
            dept_id, err = await _setup_finance_doc(c)
            if dept_id is None:
                return {"ok": False, "actual": err, "details": ""}
            r = await c.post(
                "/api/v1/documents/upload",
                files={"file": ("employee_attack.txt", b"employee tries to write into finance", "text/plain")},
                data={"department_id": dept_id},
                headers={"Authorization": f"Bearer {emp_tok}"},
            )
            ok = r.status_code == 403
            return {
                "ok": ok,
                "fail_type": "SECURITY_FLAW",
                "severity": "CRITICAL",
                "actual": f"HTTP {r.status_code}" + (" (employee wrote to foreign dept!)" if not ok else ""),
                "details": r.text[:300],
            }

        async def t_employee_read_foreign_dept(c: AsyncClient):
            emp_tok = self.state.get("token_employee", "")
            did = self.state.get("finance_doc_id", str(uuid.uuid4()))
            r_get = await c.get(f"/api/v1/documents/{did}", headers={"Authorization": f"Bearer {emp_tok}"})
            r_search = await c.post(
                "/api/v1/search/hybrid",
                json={"query": "FIN-SECRET-77", "top_k": 5},
                headers={"Authorization": f"Bearer {emp_tok}"},
            )
            r_owner = await c.get(f"/api/v1/documents/{did}", headers={"Authorization": f"Bearer {self.state.get('token_a', '')}"})
            result_docs = [x.get("document_id") for x in r_search.json().get("results", [])] if r_search.status_code == 200 else []
            finance_leaked = did in result_docs
            ok = r_get.status_code in (403, 404) and not finance_leaked and r_owner.status_code == 200
            return {
                "ok": ok,
                "fail_type": "SECURITY_FLAW",
                "severity": "CRITICAL",
                "actual": f"employee GET={r_get.status_code}, employee search hits={len(result_docs)}, finance_leaked={finance_leaked}, owner GET={r_owner.status_code}",
                "details": r_search.text[:300],
            }

        async def t_employee_list_scope(c: AsyncClient):
            emp_tok = self.state.get("token_employee", "")
            did = self.state.get("finance_doc_id", "")
            r = await c.get("/api/v1/documents", headers={"Authorization": f"Bearer {emp_tok}"})
            if r.status_code != 200:
                return {"ok": False, "actual": f"List HTTP {r.status_code}", "details": r.text}
            items = r.json()
            ids = [d.get("id") for d in items] if isinstance(items, list) else [d.get("id") for d in items.get("items", [])]
            ok = did not in ids and self.state.get("doc_a_id") in ids
            return {
                "ok": ok,
                "fail_type": "SECURITY_FLAW",
                "severity": "HIGH",
                "actual": f"employee list count={len(ids)}, finance_visible={did in ids}, tenant_wide_visible={self.state.get('doc_a_id') in ids}",
                "details": r.text[:300],
            }

        await self.run_case("D07", "Documents", "Upload real valid PDF & DOCX files", "POST", "/api/v1/documents/upload", "HTTP 201 for both", t_upload_valid_pdf_and_docx, client)
        await self.run_case("D08", "Documents Security", "Employee cannot upload into foreign department", "POST", "/api/v1/documents/upload", "HTTP 403", t_employee_upload_foreign_dept, client, "CRITICAL")
        await self.run_case("D09", "Documents Security", "Employee cannot read/search foreign department doc", "GET", "/api/v1/documents/{id}", "404 & finance doc absent from search", t_employee_read_foreign_dept, client, "CRITICAL")
        await self.run_case("D10", "Documents Security", "Employee document list scoped to dept + tenant-wide", "GET", "/api/v1/documents", "finance hidden, tenant-wide visible", t_employee_list_scope, client, "HIGH")

    # =========================================================================
    # GROUP E: HYBRID SEARCH (FAISS MMAP + FTS + RRF)
    # =========================================================================
    async def _run_group_e_search(self, client: AsyncClient):
        async def t_search_hybrid(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.post(
                "/api/v1/search/hybrid",
                json={"query": "QD-2026/SEC-AI ngày nghỉ phép", "top_k": 5},
                headers={"Authorization": f"Bearer {tok}"}
            )
            ok = r.status_code == 200 and r.json().get("total_found", 0) >= 1
            return {"ok": ok, "actual": f"HTTP {r.status_code}, found={r.json().get('total_found') if r.status_code==200 else 0}", "details": r.text[:400]}

        async def t_search_empty_query(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.post("/api/v1/search/hybrid", json={"query": "   ", "top_k": 5}, headers={"Authorization": f"Bearer {tok}"})
            return {"ok": r.status_code == 422, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_search_department_filter_bug(c: AsyncClient):
            """
            Kiểm tra lọc theo department_id thật:
            1. Tạo phòng ban thật qua POST /api/v1/departments.
            2. Upload tài liệu vào phòng ban đó.
            3. Search với top_k=1 và department_id vừa tạo -> Pre-filtering giữ lại đúng kết quả!
            """
            tok = self.state.get("token_a", "")
            dept_res = await c.post(
                "/api/v1/departments",
                json={"name": "Phòng Kỹ Thuật E2E", "code": f"ENG_{uuid.uuid4().hex[:4].upper()}"},
                headers={"Authorization": f"Bearer {tok}"},
            )
            if dept_res.status_code != 201:
                return {"ok": False, "actual": f"Create dept HTTP {dept_res.status_code}", "details": dept_res.text}
            dept_x = dept_res.json()["id"]
            self.state["dept_a_id"] = dept_x

            content_dept = "# CHÍNH SÁCH RIÊNG PHÒNG KỸ THUẬT\n\nPhòng kỹ thuật được nghỉ phép thêm vào ngày sinh nhật công ty (DEPT-UNIQUE-99)."
            up = await c.post(
                "/api/v1/documents/upload",
                files={"file": ("dept_rule.txt", content_dept.encode("utf-8"), "text/plain")},
                data={"department_id": dept_x},
                headers={"Authorization": f"Bearer {tok}"}
            )
            if up.status_code != 201:
                return {"ok": False, "actual": f"Setup upload HTTP {up.status_code}", "details": up.text}
            self.state["dept_doc_id"] = up.json()["id"]
            sr = await c.post(
                "/api/v1/search/hybrid",
                json={"query": "nghỉ phép", "top_k": 1, "department_id": dept_x},
                headers={"Authorization": f"Bearer {tok}"}
            )
            if sr.status_code != 200:
                return {"ok": False, "actual": f"Search HTTP {sr.status_code}", "details": sr.text}
            found = sr.json().get("total_found", 0)
            return {
                "ok": found >= 1,
                "severity": "MEDIUM",
                "actual": f"HTTP 200, found={found}",
                "details": sr.text[:400],
            }

        async def t_search_isolation(c: AsyncClient):
            tok_b = self.state.get("token_b", "")
            r = await c.post("/api/v1/search/hybrid", json={"query": "QD-2026/SEC-AI", "top_k": 5}, headers={"Authorization": f"Bearer {tok_b}"})
            ok = r.status_code == 200 and r.json().get("total_found", 0) == 0
            return {"ok": ok, "actual": f"HTTP {r.status_code}, found={r.json().get('total_found') if r.status_code==200 else -1}", "details": r.text}

        await self.run_case("E01", "Search", "Hybrid search semantic + keyword", "POST", "/api/v1/search/hybrid", "HTTP 200 with hits", t_search_hybrid, client)
        await self.run_case("E02", "Search EdgeCase", "Whitespace-only query rejected", "POST", "/api/v1/search/hybrid", "HTTP 422", t_search_empty_query, client, "LOW")
        await self.run_case("E03", "Search LogicBug", "Pre-filter department_id before top_k cutoff", "POST", "/api/v1/search/hybrid", "HTTP 200 found >= 1", t_search_department_filter_bug, client, "MEDIUM")
        await self.run_case("E04", "Search Security", "Cross-tenant search isolation", "POST", "/api/v1/search/hybrid", "HTTP 200 found = 0", t_search_isolation, client, "CRITICAL")

    # =========================================================================
    # GROUP F: MCP SERVER (JSON-RPC 2.0)
    # =========================================================================
    async def _run_group_f_mcp(self, client: AsyncClient):
        async def t_mcp_init(c: AsyncClient):
            r = await c.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            ok = r.status_code == 200 and r.json().get("result", {}).get("protocolVersion") == "2024-11-05"
            return {"ok": ok, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_mcp_unauth_and_invalid_token(c: AsyncClient):
            # Gửi token giả mạo tới tools/list -> phải bị -32001
            r = await c.post(
                "/api/v1/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                headers={"Authorization": "Bearer INVALID_FAKE_JWT"}
            )
            ok = r.status_code == 200 and r.json().get("error", {}).get("code") == -32001
            return {"ok": ok, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_mcp_tool_search(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.post(
                "/api/v1/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "search_knowledge_base", "arguments": {"query": "làm việc từ xa", "top_k": 3}}
                },
                headers={"Authorization": f"Bearer {tok}"}
            )
            res = r.json().get("result", {})
            ok = r.status_code == 200 and res.get("isError") is False and len(res.get("content", [])) >= 1
            return {"ok": ok, "actual": f"HTTP {r.status_code}", "details": r.text[:400]}

        async def t_mcp_tool_doc_bola(c: AsyncClient):
            tok_b = self.state.get("token_b", "")
            doc_a = self.state.get("doc_a_id", str(uuid.uuid4()))
            r = await c.post(
                "/api/v1/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "get_document_context", "arguments": {"document_id": doc_a}}
                },
                headers={"Authorization": f"Bearer {tok_b}"}
            )
            res = r.json().get("result", {})
            ok = r.status_code == 200 and res.get("isError") is True
            return {"ok": ok, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_mcp_parse_error(c: AsyncClient):
            r = await c.post("/api/v1/mcp", content=b"{invalid_json", headers={"Content-Type": "application/json"})
            ok = r.status_code == 200 and r.json().get("error", {}).get("code") == -32700
            return {"ok": ok, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_mcp_sse(c: AsyncClient):
            r = await c.get("/api/v1/mcp/sse")
            ok = r.status_code == 200 and "text/event-stream" in r.headers.get("content-type", "")
            return {"ok": ok, "actual": f"HTTP {r.status_code}", "details": r.text}

        await self.run_case("F01", "MCP", "MCP initialize handshake", "POST", "/api/v1/mcp", "protocolVersion 2024-11-05", t_mcp_init, client)
        await self.run_case("F02", "MCP Security", "Reject invalid Bearer token with -32001", "POST", "/api/v1/mcp", "JSON-RPC error -32001", t_mcp_unauth_and_invalid_token, client)
        await self.run_case("F03", "MCP", "Call tool search_knowledge_base", "POST", "/api/v1/mcp", "Valid tool content", t_mcp_tool_search, client)
        await self.run_case("F04", "MCP Security", "Cross-tenant get_document_context blocked", "POST", "/api/v1/mcp", "isError=True", t_mcp_tool_doc_bola, client)
        await self.run_case("F05", "MCP", "Malformed JSON returns -32700", "POST", "/api/v1/mcp", "JSON-RPC error -32700", t_mcp_parse_error, client)
        async def t_mcp_no_token(c: AsyncClient):
            r = await c.post("/api/v1/mcp", json={"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}})
            ok = r.status_code == 200 and r.json().get("error", {}).get("code") == -32001
            return {
                "ok": ok,
                "fail_type": "SECURITY_FLAW",
                "severity": "CRITICAL",
                "actual": f"HTTP {r.status_code} (anonymous tools/list allowed!)" if not ok else f"HTTP {r.status_code}, error -32001",
                "details": r.text,
            }

        await self.run_case("F06", "MCP", "SSE endpoint discovery", "GET", "/api/v1/mcp/sse", "text/event-stream", t_mcp_sse, client)
        await self.run_case("F07", "MCP Security", "Anonymous tools/list fails closed with -32001", "POST", "/api/v1/mcp", "JSON-RPC error -32001", t_mcp_no_token, client, "CRITICAL")

    # =========================================================================
    # GROUP G: CHAT RAG, STREAMING SSE, QUOTA & FEEDBACK
    # =========================================================================
    async def _run_group_g_chat_rag(self, client: AsyncClient):
        async def t_chat_query(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.post(
                "/api/v1/chat/query",
                json={"query": "Nhân viên có bao nhiêu ngày nghỉ phép?", "top_k": 3, "mode": "mock"},
                headers={"Authorization": f"Bearer {tok}"}
            )
            data = r.json() if r.status_code == 200 else {}
            ok = (
                r.status_code == 200
                and len(data.get("retrieved_chunks", [])) >= 1
                and len(data.get("citations", [])) >= 1
            )
            return {"ok": ok, "actual": f"HTTP {r.status_code}, chunks={len(data.get('retrieved_chunks', []))}", "details": r.text[:400]}

        async def t_chat_real_mode_unconfigured(c: AsyncClient):
            """
            Kiểm tra Edge Case: Nếu client yêu cầu mode='real' mà không có API Key/Endpoint LLM thật,
            hệ thống có báo lỗi 501/503 rõ ràng hay âm thầm fallback về mock mà gắn nhãn mode='real' (gây hiểu lầm)?
            """
            tok = self.state.get("token_a", "")
            r = await c.post(
                "/api/v1/chat/query",
                json={"query": "Quy định nghỉ phép?", "top_k": 2, "mode": "real"},
                headers={"Authorization": f"Bearer {tok}"}
            )
            # Nếu trả về HTTP 200 với mode='real' nhưng lại chạy _generate_mock_response -> Bug logic/API contract!
            if r.status_code == 200 and r.json().get("mode") == "real":
                return {
                    "ok": False,
                    "severity": "MEDIUM",
                    "actual": "HTTP 200 returned mock text labeled as mode='real'!",
                    "details": r.text[:350],
                }
            return {"ok": True, "actual": f"HTTP {r.status_code}", "details": r.text[:300]}

        async def t_chat_stream(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.post(
                "/api/v1/chat/stream",
                json={"query": "Chế độ làm việc từ xa?", "top_k": 2, "mode": "mock"},
                headers={"Authorization": f"Bearer {tok}"}
            )
            if r.status_code != 200 or "text/event-stream" not in r.headers.get("content-type", ""):
                return {"ok": False, "actual": f"HTTP {r.status_code}", "details": r.text[:300]}
            events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
            types = [e.get("type") for e in events]
            # Bắt buộc sự kiện 'chunks' phải trả về TRƯỚC 'token'
            ok = (
                "chunks" in types
                and "token" in types
                and types.index("chunks") < types.index("token")
                and types[-1] == "done"
            )
            return {"ok": ok, "actual": f"HTTP 200, order={types[:4]}...{types[-1:]}", "details": r.text[:300]}

        async def t_feedback(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.post(
                "/api/v1/chat/feedback",
                json={"query": "Phép năm?", "answer": "14 ngày.", "rating": "THUMBS_UP", "comment": "Tốt"},
                headers={"Authorization": f"Bearer {tok}"}
            )
            return {"ok": r.status_code == 201, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_delete_doc_and_search_integrity(c: AsyncClient):
            """
            Kiểm tra Bug tích hợp DB <-> FAISS:
            Sau khi xóa tài liệu dept_doc_id ở DB, liệu FAISS mmap có còn giữ vector mồ côi hoặc
            endpoint delete có đồng bộ xóa vector không?
            """
            tok = self.state.get("token_a", "")
            did = self.state.get("dept_doc_id")
            if not did:
                return {"ok": False, "actual": "Missing dept_doc_id", "details": ""}
            d_res = await c.delete(f"/api/v1/documents/{did}", headers={"Authorization": f"Bearer {tok}"})
            if d_res.status_code != 200:
                return {"ok": False, "actual": f"Delete HTTP {d_res.status_code}", "details": d_res.text}
            # Tìm lại từ khóa độc nhất 'DEPT-UNIQUE-99' của tài liệu vừa xóa
            s_res = await c.post(
                "/api/v1/search/hybrid",
                json={"query": "DEPT-UNIQUE-99", "top_k": 5},
                headers={"Authorization": f"Bearer {tok}"}
            )
            get_after = await c.get(f"/api/v1/documents/{did}", headers={"Authorization": f"Bearer {tok}"})
            results = s_res.json().get("results", []) if s_res.status_code == 200 else []
            orphan_found = any(r.get("document_id") == did or "DEPT-UNIQUE-99" in r.get("parent_content", "") for r in results)
            ok = s_res.status_code == 200 and get_after.status_code == 404 and not orphan_found
            return {
                "ok": ok,
                "actual": f"Post-delete GET={get_after.status_code}, search={s_res.status_code}, orphan_hits={1 if orphan_found else 0}",
                "details": s_res.text[:400],
            }

        async def t_rate_limit(c: AsyncClient):
            tok = self.state.get("token_b", "")  # Dùng Tenant B để không vướng quota Tenant A
            codes = []
            for _ in range(32):
                r = await c.post("/api/v1/chat/query", json={"query": "ping quota", "top_k": 1, "mode": "mock"}, headers={"Authorization": f"Bearer {tok}"})
                codes.append(r.status_code)
            ok = 429 in codes
            return {"ok": ok, "actual": f"429 count = {codes.count(429)} / 32 requests", "details": f"Status codes: {codes}"}

        await self.run_case("G01", "Chat RAG", "Grounded RAG query with citations", "POST", "/api/v1/chat/query", "HTTP 200 with citations", t_chat_query, client)
        await self.run_case("G02", "Chat EdgeCase", "Real LLM mode fails clearly or invokes real model", "POST", "/api/v1/chat/query", "Real call or HTTP 501/503 (no silent mock)", t_chat_real_mode_unconfigured, client, "MEDIUM")
        await self.run_case("G03", "Chat SSE", "Streaming SSE response", "POST", "/api/v1/chat/stream", "HTTP 200 text/event-stream", t_chat_stream, client)
        await self.run_case("G04", "Chat Feedback", "Submit THUMBS_UP feedback", "POST", "/api/v1/chat/feedback", "HTTP 201", t_feedback, client)
        await self.run_case("G05", "Documents-Search Sync", "Delete document & verify search consistency", "DELETE", "/api/v1/documents/{id}", "HTTP 200 & 0 orphan results", t_delete_doc_and_search_integrity, client)
        async def t_stream_rate_limit_precheck(c: AsyncClient):
            tok = self.state.get("token_b", "")
            r = await c.post(
                "/api/v1/chat/stream",
                json={"query": "stream after quota exhausted", "top_k": 1, "mode": "mock"},
                headers={"Authorization": f"Bearer {tok}"},
            )
            retry = r.headers.get("retry-after")
            ok = r.status_code == 429 and retry is not None
            return {
                "ok": ok,
                "actual": f"HTTP {r.status_code}, Retry-After={retry}",
                "details": r.text[:300],
            }

        await self.run_case("G06", "Quota Manager", "Token Bucket 30 RPM rate limiting", "POST", "/api/v1/chat/query", "HTTP 429 after 30 requests", t_rate_limit, client)
        await self.run_case("G07", "Quota Manager", "SSE precheck returns 429 + Retry-After before stream", "POST", "/api/v1/chat/stream", "HTTP 429 with Retry-After", t_stream_rate_limit_precheck, client, "HIGH")

    # =========================================================================
    # GROUP H: THREE-TIER DASHBOARDS
    # =========================================================================
    async def _run_group_h_dashboards(self, client: AsyncClient):
        async def t_workspace(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.get("/api/v1/dashboards/workspace", headers={"Authorization": f"Bearer {tok}"})
            return {"ok": r.status_code == 200, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_tenant_dash(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.get("/api/v1/dashboards/tenant", headers={"Authorization": f"Bearer {tok}"})
            return {"ok": r.status_code == 200, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_platform_forbidden(c: AsyncClient):
            tok = self.state.get("token_a", "")
            r = await c.get("/api/v1/dashboards/platform", headers={"Authorization": f"Bearer {tok}"})
            return {"ok": r.status_code == 403, "actual": f"HTTP {r.status_code}", "details": r.text}

        async def t_platform_superuser(c: AsyncClient):
            if not self.session_factory:
                return {"ok": False, "actual": "Skipped in remote mode", "details": "Cannot elevate superuser remotely"}
            from app.models.user import User
            async with self.session_factory() as db:
                await db.execute(update(User).where(User.email == "alpha@e2e.com").values(is_superuser=True))
                await db.commit()
            tok = self.state.get("token_a", "")
            r = await c.get("/api/v1/dashboards/platform", headers={"Authorization": f"Bearer {tok}"})
            return {"ok": r.status_code == 200 and r.json().get("system_health") == "OPERATIONAL", "actual": f"HTTP {r.status_code}", "details": r.text}

        await self.run_case("H01", "Dashboards", "Employee workspace dashboard", "GET", "/api/v1/dashboards/workspace", "HTTP 200", t_workspace, client)
        await self.run_case("H02", "Dashboards", "Business admin tenant dashboard", "GET", "/api/v1/dashboards/tenant", "HTTP 200", t_tenant_dash, client)
        await self.run_case("H03", "Dashboards Security", "Platform dashboard blocks non-superuser", "GET", "/api/v1/dashboards/platform", "HTTP 403", t_platform_forbidden, client, "CRITICAL")
        await self.run_case("H04", "Dashboards", "Platform dashboard allows superuser", "GET", "/api/v1/dashboards/platform", "HTTP 200", t_platform_superuser, client)

    # =========================================================================
    # GROUP I: CODE & MODULE INTEGRITY CHECKS (CLI / MIGRATIONS / DEPENDENCIES)
    # =========================================================================
    async def _run_group_i_static_checks(self, client: AsyncClient):
        async def t_mcp_cli_import(c: AsyncClient):
            try:
                import app.mcp.cli  # noqa: F401
                return {"ok": True, "actual": "Imported app.mcp.cli cleanly", "details": ""}
            except Exception as e:
                return {
                    "ok": False,
                    "severity": "HIGH",
                    "actual": f"ImportError: {e}",
                    "details": traceback.format_exc(),
                }

        async def t_alembic_heads(c: AsyncClient):
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            alembic_cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
            alembic_cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
            script = ScriptDirectory.from_config(alembic_cfg)
            heads = script.get_heads()
            ok = len(heads) == 1
            return {
                "ok": ok,
                "actual": f"Heads: {heads}",
                "details": f"Total revisions: {len(list(script.walk_revisions()))}",
            }

        async def t_corrupt_parser_handling(c: AsyncClient):
            from fastapi import HTTPException
            from app.services.parsers import extract_document_content
            try:
                extract_document_content("bad.pdf", b"NOT_A_PDF", "application/pdf")
                return {"ok": False, "severity": "MEDIUM", "actual": "Corrupt PDF parsed without error", "details": ""}
            except HTTPException as he:
                return {"ok": True, "actual": f"Controlled HTTPException {he.status_code}", "details": he.detail}
            except Exception as e:
                return {
                    "ok": False,
                    "severity": "HIGH",
                    "actual": f"Uncaught {type(e).__name__} (Causes HTTP 500 on upload!)",
                    "details": traceback.format_exc(),
                }

        async def t_suspended_tenant_service_login(c: AsyncClient):
            if not self.session_factory:
                return {"ok": True, "actual": "Skipped in remote mode", "details": ""}
            from fastapi import HTTPException
            from app.services.auth_service import AuthService
            from app.schemas.auth import UserRegisterRequest, UserLoginRequest
            from app.models.tenant import Tenant
            async with self.session_factory() as db:
                _, tenant, _ = await AuthService.register(db, UserRegisterRequest(
                    email=f"susp_{uuid.uuid4().hex[:6]}@e2e.com",
                    password="StrongPassword123!",
                    full_name="Suspended Tester",
                    tenant_name="Suspended Co",
                    tenant_slug=f"susp-{uuid.uuid4().hex[:6]}"
                ))
                tenant_id = tenant.id
                await db.execute(update(Tenant).where(Tenant.id == tenant_id).values(is_active=False))
                await db.commit()
                from sqlalchemy import select
                from app.models.membership import Membership
                from app.models.user import User
                u_res = await db.execute(select(User).join(Membership).where(Membership.tenant_id == tenant_id))
                u = u_res.scalar_one()
                try:
                    await AuthService.authenticate(db, UserLoginRequest(
                        email=u.email,
                        password="StrongPassword123!",
                        tenant_id=tenant_id
                    ))
                    return {
                        "ok": False,
                        "fail_type": "SECURITY_FLAW",
                        "severity": "HIGH",
                        "actual": "Suspended tenant allowed AuthService.authenticate() to succeed!",
                        "details": f"Tenant {tenant_id} has is_active=False but AuthService issued login context.",
                    }
                except HTTPException as he:
                    return {"ok": he.status_code in (401, 403), "actual": f"Blocked with HTTP {he.status_code}", "details": he.detail}

        async def t_dept_filter_service_bug(c: AsyncClient):
            if not self.session_factory:
                return {"ok": True, "actual": "Skipped in remote mode", "details": ""}
            from app.services.department_service import DepartmentService
            from app.services.document_service import DocumentService
            from app.services.search_service import SearchService
            from app.schemas.department import DepartmentCreate
            from app.schemas.search import SearchRequest
            async with self.session_factory() as db:
                tid = uuid.UUID(self.state["tenant_a_id"])
                uid = uuid.UUID(self.state["user_a_id"])
                dept = await DepartmentService.create_department(
                    db, tid, DepartmentCreate(name="Service Test Dept", code=f"SVC_{uuid.uuid4().hex[:4].upper()}")
                )
                content_dept = "# QUY CHẾ PHÒNG KỸ THUẬT\n\nPhòng kỹ thuật được nghỉ phép thêm ngày sinh nhật (DEPT-SVC-99)."
                await DocumentService.ingest_document(
                    db=db,
                    tenant_id=tid,
                    user_id=uid,
                    file_name="dept_service_test.txt",
                    file_bytes=content_dept.encode("utf-8"),
                    content_type="text/plain",
                    department_id=dept.id,
                )
                res = await SearchService.hybrid_search(
                    db, tid, SearchRequest(query="nghỉ phép", top_k=1, department_id=dept.id)
                )
                return {
                    "ok": res.total_found >= 1,
                    "severity": "MEDIUM",
                    "actual": f"found={res.total_found}",
                    "details": str([r.model_dump() for r in res.results]),
                }

        await self.run_case("I01", "Integrity", "Import MCP CLI Stdio runner", "IMPORT", "app.mcp.cli", "Clean import without ImportError", t_mcp_cli_import, client, "HIGH")
        await self.run_case("I02", "Integrity", "Alembic migration single head check", "ALEMBIC", "backend/alembic", "Single head revision", t_alembic_heads, client, "HIGH")
        await self.run_case("I03", "Parsers EdgeCase", "Corrupted PDF raises HTTPException instead of raw crash", "PARSE", "parsers.extract_document_content", "HTTPException 400", t_corrupt_parser_handling, client, "HIGH")
        await self.run_case("I04", "Auth Security", "AuthService blocks login to suspended tenant", "SERVICE", "AuthService.authenticate", "HTTP 403 Forbidden", t_suspended_tenant_service_login, client, "HIGH")
        await self.run_case("I05", "Search LogicBug", "SearchService preserves department_id match before top_k cutoff", "SERVICE", "SearchService.hybrid_search", "total_found >= 1", t_dept_filter_service_bug, client, "MEDIUM")

    async def _ensure_fallback_seed_data(self):
        """Nếu auth/documents router đang bị 404, seed dữ liệu qua Service để test tiếp Groups E, F, G, H."""
        from app.services.auth_service import AuthService
        from app.services.document_service import DocumentService
        from app.schemas.auth import UserRegisterRequest
        async with self.session_factory() as db:
            if not self.state.get("token_a"):
                u_a, t_a, m_a = await AuthService.register(db, UserRegisterRequest(
                    email="alpha@e2e.com",
                    password="StrongPassword123!",
                    full_name="Alpha Owner",
                    tenant_name="Alpha E2E Corp",
                    tenant_slug="alpha-e2e"
                ))
                toks_a = AuthService.create_tokens(u_a.id, t_a.id, m_a.role)
                self.state["user_a_id"] = str(u_a.id)
                self.state["tenant_a_id"] = str(t_a.id)
                self.state["token_a"] = toks_a.access_token

            if not self.state.get("token_b"):
                u_b, t_b, m_b = await AuthService.register(db, UserRegisterRequest(
                    email="beta@e2e.com",
                    password="StrongPassword123!",
                    full_name="Beta Owner",
                    tenant_name="Beta E2E Corp",
                    tenant_slug="beta-e2e"
                ))
                toks_b = AuthService.create_tokens(u_b.id, t_b.id, m_b.role)
                self.state["tenant_b_id"] = str(t_b.id)
                self.state["token_b"] = toks_b.access_token

            if not self.state.get("doc_a_id"):
                content = (
                    "# CHÍNH SÁCH BẢO MẬT VÀ NGHỈ PHÉP 2026\n\n"
                    "Nhân viên chính thức có 14 ngày phép có lương mỗi năm.\n"
                    "Mã quy định chuẩn là QD-2026/SEC-AI.\n\n"
                    "## QUY ĐỊNH LÀM VIỆC TỪ XA\n\n"
                    "Kỹ sư phần mềm được làm việc từ xa tối đa 2 ngày mỗi tuần."
                )
                doc = await DocumentService.ingest_document(
                    db=db,
                    tenant_id=uuid.UUID(self.state["tenant_a_id"]),
                    user_id=uuid.UUID(self.state["user_a_id"]),
                    file_name="policy_2026.txt",
                    file_bytes=content.encode("utf-8"),
                    content_type="text/plain",
                    title="Chính Sách 2026",
                )
                self.state["doc_a_id"] = str(doc.id)

    # =========================================================================
    # REPORT GENERATION INTO endpoint_test/
    # =========================================================================
    def write_reports(self):
        out_dir = os.path.join(PROJECT_ROOT, "endpoint_test")
        os.makedirs(out_dir, exist_ok=True)

        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == "PASS")
        failed = [r for r in self.results if r.status != "PASS"]

        # 1. Ghi file JSON chứa kết quả kiểm thử mới nhất
        bugs_json_path = os.path.join(out_dir, self.json_name)
        with open(bugs_json_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                    "summary": {
                        "total_tests": total,
                        "passed": passed,
                        "failed_or_bugs": len(failed),
                    },
                    "bugs": [r.to_dict() for r in failed],
                    "all_results": [r.to_dict() for r in self.results],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        # 2. Ghi báo cáo chi tiết Markdown theo quy ước endpoint_test_report_<YYYY-MM-DD>.md
        now_utc = datetime.now(timezone.utc)
        report_date = now_utc.astimezone().strftime("%Y-%m-%d")
        report_filename = self.report_name or f"endpoint_test_report_{report_date}.md"
        report_md_path = os.path.join(out_dir, report_filename)
        lines = [
            "# BÁO CÁO KIỂM THỬ TOÀN DIỆN ENDPOINTS (POST-FIX VERIFICATION)",
            "",
            f"- **Thời gian chạy (UTC):** `{now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}`",
            f"- **Ngày báo cáo (local):** `{report_date}`",
            f"- **Chế độ kiểm thử:** `{'Live Server: ' + self.base_url if self.base_url else 'Full ASGI Integration (SQLite Async + FAISS mmap)'}`",
            f"- **Tổng số kịch bản kiểm thử:** `{total}`",
            f"- **Đạt (PASSED):** `{passed}`",
            f"- **Phát hiện Lỗi / Lỗ hổng (FAILED / BUGS):** `{len(failed)}`",
            "",
            "---",
            "",
            "## 1. DANH SÁCH LỖI & LỖ HỔNG ĐƯỢC PHÁT HIỆN (BUG REGISTRY)",
            "",
        ]

        if not failed:
            lines.append("🎉 Không phát hiện lỗi nào!")
        else:
            lines.append("| ID | Mức độ | Nhóm | Endpoint | Kết quả mong đợi | Thực tế ghi nhận |")
            lines.append("|---|---|---|---|---|---|")
            for r in failed:
                lines.append(
                    f"| **{r.test_id}** | `{r.severity}` | {r.group} | `{r.method} {r.endpoint}` | {r.expected} | {r.actual} |"
                )

            lines.append("")
            lines.append("### Chi tiết kỹ thuật & Bằng chứng tái hiện (Reproduction Logs)")
            lines.append("")
            for r in failed:
                lines.append(f"#### ❌ [{r.test_id}] {r.name} (`{r.severity}`)")
                lines.append(f"- **Endpoint:** `{r.method} {r.endpoint}`")
                lines.append(f"- **Kỳ vọng:** {r.expected}")
                lines.append(f"- **Thực tế:** {r.actual}")
                lines.append("- **Chi tiết log / Response:**")
                lines.append("```text")
                lines.append(r.details[:1500])
                lines.append("```")
                lines.append("")

        lines.extend([
            "---",
            "",
            "## 2. BẢNG KẾT QUẢ TOÀN BỘ TEST CASES",
            "",
            "| ID | Nhóm | Tên kiểm thử | Endpoint | Trạng thái | Độ trễ (ms) | Thực tế |",
            "|---|---|---|---|---|---|---|",
        ])
        for r in self.results:
            badge = "✅ PASS" if r.status == "PASS" else f"❌ {r.status}"
            lines.append(
                f"| {r.test_id} | {r.group} | {r.name} | `{r.method} {r.endpoint}` | {badge} | {r.latency_ms} | {r.actual} |"
            )

        with open(report_md_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print("\n" + "=" * 75)
        print(f"📊 TỔNG KẾT: {passed}/{total} PASSED | ❌ {len(failed)} LỖI/LỖ HỔNG PHÁT HIỆN")
        print(f"📝 Đã xuất báo cáo vào: {report_md_path}")
        print(f"🐞 Đã xuất JSON lỗi vào: {bugs_json_path}")
        print("=" * 75)


async def main():
    parser = argparse.ArgumentParser(description="Comprehensive API & Security Endpoint Tester")
    parser.add_argument("--url", default=None, help="Base URL nếu chạy trên server live (vd: http://localhost:8000)")
    parser.add_argument(
        "--report-name",
        default=None,
        help="Tên file báo cáo Markdown (mặc định: endpoint_test_report_<YYYY-MM-DD>.md theo ngày local)",
    )
    parser.add_argument("--json-name", default="latest_results.json", help="Tên file kết quả JSON")
    args = parser.parse_args()

    tester = ComprehensiveTester(
        base_url=args.url,
        report_name=args.report_name,
        json_name=args.json_name,
    )
    await tester.execute_all()


if __name__ == "__main__":
    asyncio.run(main())
