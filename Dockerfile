# ==============================================================================
# Multi-stage Production Dockerfile for Enterprise AI Knowledge Platform
# Target: Minimum image size (<150MB) and hardened non-root security execution
# ==============================================================================

# STAGE 1: Builder stage
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# STAGE 2: Minimal Runtime stage
FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Tạo người dùng không đặc quyền (Non-root user)
RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -s /bin/bash -m appuser

# Sao chép các gói Python đã cài đặt từ builder
COPY --from=builder /root/.local /home/appuser/.local

# Sao chép mã nguồn ứng dụng
COPY --chown=appuser:appgroup backend /app/backend
COPY --chown=appuser:appgroup scripts/docker_entrypoint.sh /app/scripts/docker_entrypoint.sh
# Công cụ bảo trì dữ liệu chat (export RAGAS + prune retention) chạy trong container.
COPY --chown=appuser:appgroup scripts/export_ragas_dataset.py /app/scripts/export_ragas_dataset.py
COPY --chown=appuser:appgroup scripts/prune_chat_history.py /app/scripts/prune_chat_history.py

# Tạo thư mục lưu trữ index vector FAISS với quyền ghi
RUN mkdir -p /app/backend/storage/indexes && \
    chmod +x /app/scripts/docker_entrypoint.sh && \
    chown -R appuser:appgroup /app/backend/storage

USER appuser

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Entrypoint chạy migrations trước, sau đó FastAPI với duy nhất 1 worker bất đồng bộ
CMD ["/app/scripts/docker_entrypoint.sh"]
