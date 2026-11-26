from fastapi import APIRouter
from app.api.v1.endpoints import health, tenants, auth, documents, search, mcp, chat, dashboards, departments

api_router = APIRouter()

# Đăng ký các endpoints của API v1
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(tenants.router)
api_router.include_router(departments.router, prefix="/departments", tags=["Departments Management"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])
api_router.include_router(search.router, prefix="/search", tags=["Search"])
api_router.include_router(mcp.router, prefix="/mcp", tags=["Model Context Protocol"])
api_router.include_router(chat.router, prefix="/chat", tags=["Chat & RAG"])
api_router.include_router(dashboards.router, prefix="/dashboards", tags=["Dashboards"])
