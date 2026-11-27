from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core import rls
from app.core.database import verify_rls_database_role
from app.core.http_client import close_shared_http_client
from app.api.v1.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý vòng đời khởi động và dọn dẹp tài nguyên của ứng dụng."""
    # Khởi động (Startup) - từ chối chạy production với cấu hình bảo mật yếu
    settings.validate_runtime_security()
    await verify_rls_database_role()
    print(f"🚀 Khởi động {settings.PROJECT_NAME} ({settings.ENVIRONMENT})")
    yield
    # Dọn dẹp khi tắt (Shutdown)
    print("🛑 Đang dọn dẹp tài nguyên và tắt ứng dụng...")
    await close_shared_http_client()


# Vô hiệu hóa Swagger Docs trên production để bảo vệ an toàn (SEC-025)
docs_url = "/docs" if settings.ENVIRONMENT != "production" else None
redoc_url = "/redoc" if settings.ENVIRONMENT != "production" else None
openapi_url = "/openapi.json" if settings.ENVIRONMENT != "production" else None

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="2.3.2",
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
    lifespan=lifespan
)

# Cấu hình CORS chặt chẽ theo biến môi trường CORS_ALLOW_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rls_context_middleware(request, call_next):
    """Thiết lập ngữ cảnh RLS (tenant/user/bypass) cho từng request PostgreSQL."""
    tenant_id, user_id, bypass = rls.resolve_rls_context(
        request.headers.get("authorization"),
        request.headers.get("X-Tenant-ID"),
        request.url.path,
    )
    tenant_token = rls.current_tenant_id.set(tenant_id)
    user_token = rls.current_user_id.set(user_id)
    bypass_token = rls.rls_bypass.set(bypass)
    try:
        return await call_next(request)
    finally:
        rls.rls_bypass.reset(bypass_token)
        rls.current_user_id.reset(user_token)
        rls.current_tenant_id.reset(tenant_token)


@app.middleware("http")
async def add_security_headers(request, call_next):
    """Bổ sung security headers phòng thủ chiều sâu ở tầng ứng dụng."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response


# Đăng ký Master Router API v1
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/", tags=["Root"])
async def root():
    return {
        "message": f"Chào mừng tới {settings.PROJECT_NAME}",
        "docs": docs_url or "Docs disabled in production",
        "api_v1": settings.API_V1_PREFIX
    }
