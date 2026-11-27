# Deploy từ Container Registry — Build local, VPS chỉ pull và chạy

Luồng triển khai đã chốt:

```
Máy local (Docker Desktop)                      VPS GCP e2-micro
┌────────────────────────────┐                 ┌──────────────────────────────┐
│ docker build backend       │                 │ 1. dọn sạch Docker cũ        │
│ docker build nginx + SPA   │ ── docker push ─▶│ 2. docker compose pull       │
│ docker push :TAG + :latest │   Docker Hub/GHCR│ 3. docker compose up -d      │
└────────────────────────────┘                 │    (KHÔNG build, KHÔNG pip)  │
                                               └──────────────────────────────┘
```

Lợi ích trên e2-micro 1 GB RAM: VPS không cài Node/npm, không chạy `pip install`,
không cần 4 GB swap chỉ để build image. Image được build một lần trên máy khỏe,
VPS chỉ tốn thời gian pull và khởi động.

> Repo vẫn cần có trên VPS để lấy `nginx/prod/*.conf`, `nginx/runtime`,
> `certbot/www`, `scripts/init_db_app_role.sh` và các script TLS/DuckDNS.
> VPS **không** dùng source để build image.

---

## 0. Chọn registry và tên image

Mặc định trong tài liệu là Docker Hub với namespace `neshaki091`:

| Image | Docker Hub | GHCR tương đương |
|---|---|---|
| Backend FastAPI | `neshaki091/ekp-backend` | `ghcr.io/neshaki091/ekp-backend` |
| Nginx + SPA | `neshaki091/ekp-nginx` | `ghcr.io/neshaki091/ekp-nginx` |

Chọn một trong hai:

- **Docker Hub public**: đơn giản nhất, không cần login khi pull. Nhưng image backend chứa toàn bộ source Python — ai cũng tải được. Chỉ chọn nếu chấp nhận mã nguồn công khai.
- **GHCR private** (khuyến nghị nếu repo đang private): `DOCKER_IMAGE_PREFIX=ghcr.io/neshaki091`, đăng nhập bằng GitHub PAT có quyền `read:packages` (VPS) và `write:packages` (máy local). GHCR cho phép image private miễn phí; Docker Hub free chỉ có 1 private repo, không đủ cho 2 image.
- **Docker Hub private**: cần gói trả phí hoặc gộp image — không khuyến nghị.

Các biến dùng chung:

```bash
export DOCKER_IMAGE_PREFIX=neshaki091          # hoặc ghcr.io/neshaki091
export IMAGE_TAG=2.1.0                         # tag bất biến, KHÔNG dùng latest cho production
```

---

## 1. Trên máy local: build và push

```bash
# Đăng nhập registry (bỏ qua nếu image public và chỉ pull)
docker login                                   # Docker Hub
# docker login ghcr.io -u Neshaki091           # GHCR: dùng PAT, không dùng mật khẩu GitHub

# Build + push cả :TAG và :latest
DOCKER_IMAGE_PREFIX=neshaki091 IMAGE_TAG=2.1.0 \
  bash deploy/registry/build_and_push.sh

# Kiểm thử build mà không push
DOCKER_IMAGE_PREFIX=neshaki091 IMAGE_TAG=test \
  bash deploy/registry/build_and_push.sh --no-push
```

Script build với `--platform linux/amd64` để khớp GCP, tag cả `:TAG` và `:latest`,
sau đó push. Nếu máy local là ARM (Apple Silicon), cờ platform vẫn đảm bảo image
chạy được trên e2-micro.

Tag nên là phiên bản hoặc git SHA:

```bash
IMAGE_TAG="$(git rev-parse --short HEAD)"
```

---

## 2. Trên VPS: dọn sạch Docker còn sót

> **CẢNH BÁO:** bước này xóa container **và volume**, tức là toàn bộ dữ liệu
> PostgreSQL/FAISS của stack cũ. Nếu cần giữ dữ liệu, backup trước:
>
> ```bash
> docker exec saas_postgres_db pg_dump -U postgres saas_knowledge_db | gzip > backup.sql.gz
> # Volume FAISS: saas_chatbot_platform_faiss_storage (build tại chỗ)
> # hoặc ekp_faiss_storage (deploy bằng docker-compose.registry.yml)
> docker run --rm -v ekp_faiss_storage:/data -v "$PWD":/backup alpine \
>   tar czf /backup/faiss.tar.gz -C /data .
> ```

Chạy trong thư mục repo trên VPS:

```bash
# Dọn stack EKP cũ (container/volume/network/image của dự án)
sudo bash deploy/registry/cleanup_vps.sh

# Nếu VPS CHỈ dùng cho EKP và muốn dọn toàn bộ Docker (image lạ, cache build...)
sudo bash deploy/registry/cleanup_vps.sh --all
```

Script sẽ:

1. `docker compose down --rmi all --volumes` cho cả stack source-build cũ và stack registry (nếu có).
2. Xóa container tên `saas_*` / `ekp_*`.
3. Xóa volume `saas_chatbot_platform_*` / `ekp_*` (PostgreSQL + FAISS).
4. Xóa network và image build cũ `saas_chatbot_platform-*`.
5. Với `--all`: `docker system prune -a --volumes` + `docker builder prune -a`.
6. In `docker system df`, danh sách container/volume còn lại để xác nhận sạch.

Không xóa `/etc/letsencrypt` và `/etc/duckdns` — đây là dữ liệu trên host,
giữ lại để cert/DNS không phải cấp lại.

Kiểm tra nhanh sau khi dọn:

```bash
docker ps -a          # không còn saas_*
docker volume ls      # không còn saas_chatbot_platform_*/ekp_*
docker system df      # dung lượng đã giải phóng
```

---

## 3. Trên VPS: cấu hình `.env`

```bash
cd /opt/SaaS_Chatbot_platform        # hoặc thư mục repo thật trên VPS
cp .env.example .env
chmod 600 .env
```

Điền tối thiểu:

```dotenv
# Registry
DOCKER_IMAGE_PREFIX=neshaki091
IMAGE_TAG=2.1.0

# Bắt buộc đổi
SECRET_KEY=<openssl rand -hex 32>
POSTGRES_PASSWORD=<chuỗi mạnh>
APP_DB_PASSWORD=<chuỗi mạnh>
CORS_ALLOW_ORIGINS=https://<project>.vercel.app

# Tùy chọn: AI thật
GOOGLE_API_KEY=<key>
```

Nếu image private:

```bash
docker login                                  # Docker Hub private
# docker login ghcr.io -u Neshaki091          # GHCR, PAT có read:packages

# Lưu ý: nếu chạy deploy_vps.sh bằng sudo, credential phải nằm trong /root:
sudo docker login
```

---

## 4. Trên VPS: pull và chạy

### One-shot (khuyến nghị)

Sau khi đã có `.env`, một lệnh duy nhất làm toàn bộ chuỗi pull → chạy → health →
CORS → TLS (và tự cài DuckDNS nếu chưa có):

```bash
sudo bash deploy/registry/bootstrap_vps.sh \
  --domain ekp-demo.duckdns.org \
  --email ban@example.com \
  --vercel-origin https://enterprise-knowledge-platform-xxxx.vercel.app
```

Script idempotent, không xóa volume; chạy lại để cập nhật TLS/CORS. Các cờ hữu ích:

- `--skip-duckdns`: bỏ qua bước DuckDNS (dùng domain riêng hoặc DNS khác).
- `--skip-firewall`: không chạy UFW.
- `--skip-tls`: chỉ deploy HTTP.
- `--no-pull`: dùng image đang có.
- Bỏ `--vercel-origin` nếu chưa biết URL Vercel; script sẽ cảnh báo và giữ CORS hiện tại.

Nếu DNS chưa trỏ đúng IP, script vẫn deploy backend nhưng bỏ qua TLS và in hướng dẫn chạy lại.

### Chạy từng bước (thủ công)

```bash
sudo bash deploy/registry/deploy_vps.sh
```

Script thực hiện:

1. Kiểm tra Docker/Compose, compose file, `.env`, tag (từ chối `latest`), dung lượng đĩa.
2. `docker compose config -q` để bắt lỗi biến thiếu trước khi pull.
3. `docker compose pull backend nginx` — chỉ pull 2 image app; PostgreSQL đang chạy **không** bị pull/recreate.
4. `docker compose up -d --remove-orphans --no-build`.
5. Chờ `/health` qua Nginx trả `"status":"healthy"` (mặc định 120s, đổi bằng `HEALTH_TIMEOUT_SECONDS`).
6. Nếu `up` hoặc health thất bại: in `compose ps` + log, tự rollback về image đang chạy trước đó (trừ khi `--no-rollback`). Migration CSDL không được rollback.
7. Sau khi thành công: gắn tag `previous` cho image vừa bị thay thế, prune image cũ của `ekp-backend`/`ekp-nginx` (giữ bản đang chạy + `--keep N`, mặc định 1), `docker image prune -f` cho layer dangling. Không đụng volume PostgreSQL/FAISS.

Cờ: `--no-pull`, `--no-prune`, `--no-rollback`, `--keep N` (0-5), `-h/--help`.

Lần đầu chưa có TLS, Nginx chỉ mở :80 (ACME + health + redirect). Cấp cert:

```bash
sudo bash deploy/tls/issue_tls.sh <ten>.duckdns.org ban@email.com
curl -fsS https://<ten>.duckdns.org/health
```

Script TLS dùng đúng container `saas_nginx_proxy` và mount `nginx/runtime`,
`certbot/www`, `/etc/letsencrypt` của compose registry, nên không cần sửa gì.

---

## 5. Cập nhật phiên bản về sau

```bash
# Local
IMAGE_TAG=2.3.2 DOCKER_IMAGE_PREFIX=neshaki091 \
  bash deploy/registry/build_and_push.sh

# VPS — cập nhật .env để 'docker compose up' thủ công về sau không quay lui
sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=2.3.2/' .env
sudo bash deploy/registry/deploy_vps.sh

# Hoặc override một lần, không sửa .env (script sẽ cảnh báo)
sudo IMAGE_TAG=2.3.2 bash deploy/registry/deploy_vps.sh
```

`deploy_vps.sh` pull đúng tag mới rồi recreate container. Volume PostgreSQL/FAISS
và cert TLS được giữ nguyên; không chạy cleanup khi chỉ nâng cấp phiên bản.
Image cũ được prune sau khi health đạt, giữ bản đang chạy + 1 backup (tag `previous`).

### Rollback

Khi deploy thành công, script gắn tag `previous` cho image vừa bị thay thế:

```bash
sudo IMAGE_TAG=previous bash deploy/registry/deploy_vps.sh --no-pull --no-prune
```

Hoặc pin lại tag cũ trong `.env` rồi chạy lại `deploy_vps.sh` (image cũ vẫn còn
trên registry). Nếu health fail, script tự rollback bằng tag `rollback-<timestamp>`.
Rollback image **không** rollback migration CSDL — migration phải tương thích ngược.

---

## 5b. CI/CD hiện tại: build local → Docker Hub → deploy

Đường deploy không cần GitHub Actions/secrets/environment; bước build có thể chạy
local hoặc trên GitHub runner (`build.yml`, tuỳ chọn, cần Docker Hub token). VPS
vẫn chỉ pull image, không build.

Luồng:

1. Máy local: `bash CI-CD/07_deploy_now.sh --build` build/push
   `ekp-backend` + `ekp-nginx` với tag `APP_VERSION`.
2. SSH vào VPS: `sudo -n` → `git pull --ff-only` → kiểm tra prefix registry →
   cập nhật `IMAGE_TAG` trong `.env` (chmod 600).
3. `deploy_vps.sh`: pull, recreate, health gate, tự rollback nếu lỗi, prune an toàn.
4. Nếu deploy lỗi, `07` khôi phục `IMAGE_TAG` cũ trong `.env`.
5. `bash CI-CD/05_verify.sh` kiểm tra public health + trạng thái stack.

### Thiết lập một lần

```bash
# Deploy key riêng + pin host key (in fingerprint để đối chiếu với VPS)
bash CI-CD/01_setup_ssh.sh

# Preflight VPS: repo sạch, git pull, .env, compose, sudo
bash CI-CD/00_check_vps.sh

# Đăng nhập Docker Hub để build/push
docker login -u <DOCKERHUB_USERNAME>
```

Cấu hình nằm trong `CI-CD/config.sh` (gitignore). Chi tiết: [CI-CD/README.md](../../CI-CD/README.md).

Nếu muốn build trên GitHub thay vì máy local: tạo secret `DOCKERHUB_TOKEN` (Read &
Write) trong repo, chạy workflow **Build & Push Images**, rồi deploy bằng
`bash CI-CD/07_deploy_now.sh <tag>` (không cần `--build`). Workflow từ chối tag
`latest`, tag không khớp `APP_VERSION`, hoặc tag đã tồn tại trên Docker Hub.

### Phát hành

```bash
# Bump APP_VERSION trong backend/app/core/version.py (nguồn duy nhất)
git add backend/app/core/version.py
git commit -m "release: v2.3.3"
git push origin main

bash CI-CD/07_deploy_now.sh --dry-run
bash CI-CD/07_deploy_now.sh --build
bash CI-CD/05_verify.sh
```

Không cần tạo/push tag `v*`; repo không còn workflow release. Không build lại
cùng một version rồi push đè image đã phát hành — hãy bump version.

Rollback: `bash CI-CD/06_rollback.sh` hoặc trên VPS:

```bash
sudo IMAGE_TAG=previous bash deploy/registry/deploy_vps.sh --no-pull --no-prune
```

---

## 6. Kiểm tra sau deploy

```bash
docker compose --env-file .env -f docker-compose.registry.yml ps
docker compose --env-file .env -f docker-compose.registry.yml images
curl -fsS http://127.0.0.1/health
curl -fsS https://<ten>.duckdns.org/health
docker stats --no-stream      # Postgres ~74 MB, backend ~143 MB, nginx ~3 MB
```

Kỳ vọng: 3 container `healthy`/`running`; Nginx chỉ publish 80/443; backend và
PostgreSQL không mở cổng ra host.

---

## 7. Xử lý sự cố

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| `unauthorized: authentication required` | Image private, VPS chưa login | `docker login` đúng registry / PAT còn hạn |
| `manifest unknown` | Sai `IMAGE_TAG` hoặc chưa push | Kiểm tra tag trên registry; push lại từ local |
| `exec format error` | Image build sai kiến trúc | Build lại với `PLATFORM=linux/amd64` |
| `toomanyrequests` | Docker Hub rate limit IP | Login Docker Hub trên VPS hoặc dùng GHCR |
| Nginx `502` | Backend chưa healthy | `docker compose logs backend`; chờ migration xong |
| `relation ... does not exist` | Volume cũ + migration lỗi | `deploy_vps.sh` chạy Alembic trong entrypoint; xem log backend |
| RLS fail-closed, 0 dữ liệu | Volume cũ thiếu role `saas_app` | Dọn volume (`cleanup_vps.sh`) để init role lại, hoặc làm theo mục 3 `AGENTS.md` |
| Hết dung lượng khi pull | Image cũ/container cũ còn sót | `docker system df` → `cleanup_vps.sh --all` |

---

## 8. Vì sao không build trên VPS?

- e2-micro chỉ có 1 GB RAM; `npm ci` + `tsc` + `vite build` và `pip install faiss-cpu`
  dễ OOM, phải bật swap 4 GB và build rất chậm.
- Build local tận dụng CPU/RAM mạnh, cache layer của Docker Desktop.
- Image là artifact bất biến: cùng một digest chạy ở local test và VPS, dễ rollback.
- VPS không cần toolchain (Node, gcc, Python headers), giảm bề mặt tấn công.
