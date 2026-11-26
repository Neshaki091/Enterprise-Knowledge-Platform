#!/usr/bin/env sh
# ==============================================================================
# Entrypoint production cho container FastAPI:
# 1. Chạy Alembic migrations tới head (bảng + RLS policies) bằng role ứng dụng.
# 2. Khởi động uvicorn với đúng 1 async worker (ràng buộc RAM e2-micro).
# ==============================================================================
set -eu

cd /app/backend

echo "[entrypoint] Áp Alembic migrations..."
alembic upgrade head

echo "[entrypoint] Khởi động FastAPI (1 worker)..."
# Backend chỉ nằm sau Nginx (không publish port ra host). Nginx ghi đè X-Forwarded-For
# bằng $remote_addr nên có thể tin header này để lấy IP thật cho login guard / audit.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 \
    --proxy-headers --forwarded-allow-ips="*"
