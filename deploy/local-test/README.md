# Kiểm thử Production Stack trên máy local

Mục đích: chạy đúng cấu hình `docker-compose.prod.yml` (Nginx TLS 443, bootstrap :80,
SSE, RLS, rate limit) ngay trên laptop mà không cần VM/DuckDNS/Let's Encrypt thật.
Chứng chỉ tự ký thay cho cert thật; hostname test cố định `saas-test.local`.

Không dùng kit này cho dev/Playwright hằng ngày — dev giữ `docker compose up -d --build`
với `nginx/nginx.conf` (HTTP, single-origin).

## 1. Chuẩn bị (một lần)

```bash
cd <repo-root>

# Biến môi trường test (đã gitignore). Không dùng key Google thật.
umask 077
cat > .env.local-test <<EOF
SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
APP_DB_PASSWORD=$(openssl rand -hex 16)
CORS_ALLOW_ORIGINS=https://example.vercel.app
GOOGLE_API_KEY=
LLM_API_URL=
EMBEDDING_API_URL=
EOF

# Cert tự ký, đúng đường dẫn /etc/letsencrypt/live/saas-backend của bản production.
mkdir -p deploy/local-test/tls/live/saas-backend
openssl req -x509 -nodes -newkey rsa:2048 -days 2 \
  -keyout deploy/local-test/tls/live/saas-backend/privkey.pem \
  -out   deploy/local-test/tls/live/saas-backend/fullchain.pem \
  -subj "//CN=saas-test.local"      # Git Bash/MSYS cần "//CN"; Linux/macOS dùng "/CN"

# Server 443 production (file runtime đã gitignore).
cp nginx/prod/tls-server.conf nginx/runtime/10-tls.conf
```

## 2. Khởi động / dừng

```bash
docker compose --env-file .env.local-test \
  -f docker-compose.yml -f docker-compose.prod.yml \
  -f deploy/local-test/compose.local-test.yml up -d --build

docker compose --env-file .env.local-test \
  -f docker-compose.yml -f docker-compose.prod.yml \
  -f deploy/local-test/compose.local-test.yml down -v      # -v xoá volume test
```

Volume riêng (`postgres_data_local_test`, `faiss_storage_local_test`) nên không đụng
dữ liệu dev. Sau khi test xong nên `down -v` và xoá `nginx/runtime/10-tls.conf`,
`deploy/local-test/tls/`, `.env.local-test`.

## 3. Checklist đã xác minh (2026-11-26, Docker Desktop)

Lệnh gọi đều thêm `--resolve saas-test.local:443:127.0.0.1 --insecure` (curl) để bỏ qua
self-signed. Kết quả kỳ vọng:

| Kiểm tra | Lệnh rút gọn | Kết quả đạt |
|---|---|---|
| Bootstrap HTTP redirect | `curl -s -o /dev/null -w '%{http_code}' -H 'Host: saas-test.local' http://127.0.0.1/` | `301` → `https://saas-test.local/` |
| ACME path không redirect | `... http://127.0.0.1/.well-known/acme-challenge/x` | `404` (không 301) |
| Health qua HTTPS | `curl -sk https://saas-test.local/health` | JSON `"status":"healthy"`, DB connected |
| TLS versions | `openssl s_client -tls1_1/-tls1_2/-tls1_3` | 1.1 bị từ chối; 1.2 & 1.3 OK |
| Security headers | `curl -skI https://saas-test.local/health` | Mỗi header đúng 1 lần, có HSTS + CSP |
| Swagger/OpenAPI bị chặn | `/docs`, `/redoc`, `/openapi.json` | `404` (SPA fallback không che nữa) |
| CORS allowlist | preflight `Origin: https://example.vercel.app` | `200` + `access-control-allow-origin` đúng origin |
| CORS origin lạ | preflight `Origin: https://evil.example.com` | `400`, không có ACAO |
| Auth + tenant | register (`@example.com`) → login | `201` → `200`, token OWNER, `tenant_id` |
| SSE realtime | `/api/v1/dashboards/tenant/realtime?interval_sec=0.5&max_events=1` | `text/event-stream`, chunked, event đầu ~0,04 s |
| Body guard | POST 27 MB | Nginx `413` trước khi tới backend |
| Rate limit tầng IP | 140 request dồn dập `/api/v1/health` | ~125 × `200` + ~15 × `503` (600 r/m, burst 100) |
| RLS fail-closed | psql role `saas_app`, `SELECT count(*) FROM tenants` | `0` dù bảng có dữ liệu; `row_security=on` |
| DB role | `pg_roles` | `saas_app`: `rolsuper=f`, `rolbypassrls=f`; RLS `FORCE` trên bảng tenant |
| Container posture | `docker inspect` | chỉ Nginx publish 80/443; `unless-stopped`; limit 300/350/50 MB; log json-file 10 MB × 3 |
| RAM thực tế | `docker stats --no-stream` | Postgres ~74 MB, Backend ~143 MB, Nginx ~3 MB |

Ghi chú: `GOOGLE_API_KEY` để trống nên các luồng upload/hybrid search/RAG chat không
gọi API Google và không được kiểm thử ở đây (dùng `scripts/verify_staging.py` trên VM
với key thật). Health sẽ báo `llm_real_configured:false`, `embedding_real_configured:false`.
