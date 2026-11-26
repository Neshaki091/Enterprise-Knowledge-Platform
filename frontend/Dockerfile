# ==============================================================================
# Multi-stage Dockerfile: React SPA (Vite) + Nginx static/reverse-proxy runtime.
# Build context: thư mục gốc repo (cần frontend/ và nginx/).
# Stage runtime chỉ chứa Nginx + dist đã build, không chứa Node/npm.
# ==============================================================================

# STAGE 1: Build bundle tĩnh bằng Node (devDependencies cần cho tsc/vite).
FROM node:22-alpine AS build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# .dockerignore loại trừ frontend/node_modules và frontend/dist khỏi context.
RUN npm run build

# STAGE 2: Runtime Nginx (SPA + proxy /api, SSE không buffer, security headers).
FROM nginx:1.27-alpine AS runtime

RUN rm -f /etc/nginx/conf.d/default.conf

COPY nginx/nginx.conf /etc/nginx/nginx.conf
COPY nginx/security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# Dùng 127.0.0.1 thay vì localhost: trong container Alpine, localhost ưu tiên ::1
# nhưng nginx chỉ listen IPv4, khiến healthcheck báo unhealthy dù dịch vụ vẫn chạy.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider http://127.0.0.1/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
