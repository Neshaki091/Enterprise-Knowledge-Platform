# Triển khai Production $0 — GCP e2-micro + DuckDNS + Let's Encrypt + Vercel

Runbook một lần cho Sprint 10 (vận hành 4 tuần). Sau khi hoàn tất, hệ thống chạy
nhiều tháng mà không cần cấu hình lại: cert tự gia hạn, DNS tự cập nhật, SPA trên Vercel.

```
Trình duyệt ──HTTPS──► Vercel (*.vercel.app, SPA tĩnh)
      │
      └──HTTPS (API + SSE)──► <ten>.duckdns.org ──► Nginx 443 (container)
                                                    └──► FastAPI :8000 ──► PostgreSQL/FAISS
```

Chi phí: GCP Always Free (e2-micro, 1 GB egress/tháng) + DuckDNS $0 + Let's Encrypt $0
+ Vercel Hobby $0. Ràng buộc thật sự là **egress ≤ 1 GB/tháng** (ngân sách Sprint 9: 0,6–0,8 GB).

> **Phương án khuyến nghị — VPS không build:** build image backend + nginx trên máy
> local, push lên Docker Hub/GHCR, VPS chỉ `pull` và chạy
> `docker-compose.registry.yml`. Xem [registry/README.md](registry/README.md).
> Runbook dưới đây là phương án build trực tiếp trên VM (fallback khi không dùng registry).

---

## 0. Chuẩn bị

- VM GCP e2-micro (us-west1/us-central1/us-east1), Ubuntu 22.04, repo đã clone.
- Tài khoản DuckDNS (subdomain + token).
- Tài khoản Vercel (Hobby, phi thương mại).
- Docker + Compose plugin: kiểm tra `docker compose version`; nếu thiếu:
  `curl -fsSL https://get.docker.com | sudo sh` (script chính thức của Docker).

---

## 1. Swap + kernel (1 lần)

```bash
sudo bash scripts/setup_gcp_swap_and_kernel.sh
free -h   # xác nhận /swapfile 4.0 GB
```

## 2. Cấu hình `.env` production

```bash
cp .env.example .env
chmod 600 .env
```

Sửa tối thiểu các biến sau (không commit `.env`):

| Biến | Giá trị |
|---|---|
| `SECRET_KEY` | `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | chuỗi ngẫu nhiên mạnh |
| `APP_DB_PASSWORD` | chuỗi ngẫu nhiên mạnh (role `saas_app`, NOSUPERUSER/NOBYPASSRLS) |
| `CORS_ALLOW_ORIGINS` | `https://<project>.vercel.app` (đúng origin, không wildcard) |
| `GOOGLE_API_KEY` | key Gemini API thật (không commit) |

`docker-compose.yml` đã ép `ENVIRONMENT=production`, `DEBUG=false`, `DB_RLS_ENABLED=true`;
backend sẽ từ chối khởi động nếu cấu hình bảo mật chưa đạt (fail-closed).

> Volume PostgreSQL cũ: role `saas_app` chỉ được tạo khi volume trống. Nếu tái sử dụng
> volume từ máy khác, xem mục 3 trong `AGENTS.md` trước khi bật RLS.

## 3. DuckDNS (1 lần, tự cập nhật mãi mãi)

```bash
sudo bash deploy/duckdns/install_duckdns.sh
```

Script tạo `/etc/duckdns/duckdns.env` (chmod 600), cài updater + systemd timer 5 phút/lần,
chạy thử và in IP công khai/DNS. Nếu DNS chưa khớp, đợi 1–2 phút rồi chạy lại script
(script idempotent, giữ nguyên cấu hình cũ).

## 4. Firewall

```bash
sudo bash deploy/firewall.sh
```

UFW mở 22/80/443. GCP VPC cần rule tương ứng (Console ➔ VPC network ➔ Firewall, hoặc):

```bash
gcloud compute firewall-rules create allow-http-https \
  --allow tcp:80,tcp:443 --source-ranges 0.0.0.0/0 --target-tags http-server
```

Gắn network tag `http-server` cho VM. **Không** mở 5432/8000.

## 5. Khởi động stack production (bootstrap HTTP)

**Phương án registry (khuyến nghị, VPS không build):**

```bash
# .env phải có DOCKER_IMAGE_PREFIX và IMAGE_TAG (xem registry/README.md)
docker compose --env-file .env -f docker-compose.registry.yml pull
docker compose --env-file .env -f docker-compose.registry.yml up -d --remove-orphans
curl -fsS http://127.0.0.1/health
```

Hoặc dùng script: `sudo bash deploy/registry/deploy_vps.sh`.

**Phương án build trực tiếp trên VM (fallback):**

```bash
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
curl -fsS http://127.0.0.1/health   # {"status":"healthy","database":"connected",...}
```

Lúc này Nginx chỉ mở :80 (ACME + health + redirect), chưa có TLS — đúng thiết kế.

## 6. Cấp TLS (1 lần, tự gia hạn mãi mãi)

```bash
sudo bash deploy/tls/issue_tls.sh <ten>.duckdns.org ban@email.com
```

Script sẽ: kiểm tra webroot ACME qua Nginx → xác minh DNS khớp IP công khai → xin cert
`saas-backend` (webroot, không downtime) → cài deploy hook reload Nginx sau mỗi lần renew →
copy `nginx/prod/tls-server.conf` vào `nginx/runtime/10-tls.conf` → `nginx -t` + reload →
gọi thử `https://<domain>/health`.

Kiểm tra:

```bash
curl -fsS https://<ten>.duckdns.org/health
curl -sI https://<ten>.duckdns.org | grep -i strict-transport-security
sudo certbot renew --dry-run
systemctl list-timers duckdns-update.timer certbot.timer
```

## 7. Vercel (SPA split-origin)

1. Push repo lên GitHub/GitLab (Vercel import qua Git provider), đặt **Root Directory = `frontend`**, Framework = Vite.
2. Environment Variable: `VITE_API_BASE_URL=https://<ten>.duckdns.org` (Production).
3. Sửa placeholder trong `frontend/vercel.json`:
   `connect-src ... https://REPLACE_ME.duckdns.org` ➔ host DuckDNS thật.
4. Deploy, mở `https://<project>.vercel.app`:
   - Đăng ký/đăng nhập, upload tài liệu, chat RAG (SSE) phải chạy.
   - DevTools ➔ Console không có lỗi CSP/CORS; Network ➔ request API trỏ về DuckDNS.

Lưu ý: preview deployment có origin khác nên chỉ gọi được API nếu thêm origin đó vào
`CORS_ALLOW_ORIGINS`; mặc định chỉ production origin được phép.

## 8. Nghiệm thu nhanh

```bash
# Từ laptop (không phải VM)
curl -fsS https://<ten>.duckdns.org/health
openssl s_client -connect <ten>.duckdns.org:443 -servername <ten>.duckdns.org </dev/null \
  | openssl x509 -noout -dates -issuer
python scripts/verify_staging.py https://<ten>.duckdns.org

# Header bảo mật phải xuất hiện ĐÚNG MỘT LẦN (edge Nginx là nguồn duy nhất).
curl -sI https://<ten>.duckdns.org/health | grep -icE '^(referrer-policy|x-content-type-options|x-frame-options|permissions-policy):'
# -> 4

# Swagger/OpenAPI phải bị chặn ở edge.
curl -s -o /dev/null -w '%{http_code}\n' https://<ten>.duckdns.org/openapi.json   # -> 404

# SSE phải trả event ngay, không bị buffer (dùng token thật sau khi login).
curl -N -H "Authorization: Bearer <access_token>" \
  'https://<ten>.duckdns.org/api/v1/dashboards/tenant/realtime?interval_sec=1&max_events=1'
```

`verify_staging.py` chạy đủ 5 chặng: health → register/login → upload → hybrid search → RAG chat.
Muốn tái hiện toàn bộ kiểm thử production stack ngay trên laptop (self-signed cert,
RLS fail-closed, rate limit, 413): xem [local-test/README.md](local-test/README.md).

## 9. Vận hành định kỳ (không cần cấu hình lại)

- **Cert**: `certbot.timer` tự renew khi còn 30 ngày; deploy hook reload Nginx.
- **DNS**: `duckdns-update.timer` 5 phút/lần giữ subdomain và cập nhật IP sau reboot.
- **Uptime**: BetterStack/Uptime Kuma free gọi `https://<domain>/health` mỗi 5 phút (~8 MB/tháng).
- **Backup**: `pg_dump` hằng đêm + copy FAISS index + checksum (Sprint 10, bài 10.3).
- **Egress**: bật cảnh báo 80% hạn mức 1 GB trên GCP Billing.

## 10. Rollback

- **Chỉ tắt TLS (về HTTP bootstrap)**: `sudo rm nginx/runtime/10-tls.conf && sudo docker exec saas_nginx_proxy nginx -s reload`
  — khi đó :80 redirect sang https không tồn tại; chỉ dùng để debug ngắn.
- **Về Phase 2 single-origin (HTTP, SPA trong Nginx)**:
  `sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml down`
  rồi `sudo docker compose up -d --build`.
- **Rollback phiên bản app (registry)**: sửa `IMAGE_TAG` trong `.env` về tag cũ rồi chạy `sudo bash deploy/registry/deploy_vps.sh`; volume dữ liệu và cert được giữ nguyên.
- **Rollback phiên bản app (build tại chỗ)**: nếu dùng git, `git checkout <tag>`; nếu không, giữ một bản copy thư mục release trước đó và chạy lại `sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` từ bản đó.

## 11. Bảo mật

- Không commit `.env`, `/etc/duckdns/duckdns.env`, token, API key. Nếu lộ: rotate token DuckDNS + key Google.
- `/etc/letsencrypt` là tài sản quan trọng — thêm vào backup định kỳ.
- Chỉ expose 80/443; PostgreSQL và FastAPI chỉ trong mạng Docker.
- Security headers do Nginx phát duy nhất; `proxy_hide_header` loại bản trùng từ
  FastAPI middleware (tránh 2 giá trị `Referrer-Policy` xung đột). Backend vẫn giữ
  middleware để phòng thủ chiều sâu khi chạy trực tiếp trong dev.

## 12. Vì sao không cần Firebase/DDNS phức tạp

Hostname DuckDNS cố định và cert tự gia hạn, nên `VITE_API_BASE_URL` không bao giờ phải
build lại. Script đồng bộ `api_url` lên Firebase là không cần thiết và làm CSP khó siết.
