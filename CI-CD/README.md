# CI-CD — Phát hành EKP từ máy local

Bộ script vận hành phát hành theo **đường nhẹ**: build/push image từ máy local,
sau đó SSH vào VPS để pull image và recreate stack.

```
Máy local                              Docker Hub                 VPS
─────────                              ──────────                 ───
build/push ──────────────────────────▶ ekp-backend:X.Y.Z ───────▶ git pull --ff-only
  ▲                                    ekp-nginx:X.Y.Z            cập nhật IMAGE_TAG
  │                                                               deploy_vps.sh
  └── docker login                                                ├─ docker pull
                                                                  ├─ compose up
                                                                  ├─ health /health
                                                                  └─ rollback nếu lỗi
```

Nguyên tắc cốt lõi: **VPS không build**. Image được build trên máy local, VPS chỉ
`git pull` code/config và pull image từ Docker Hub. Mọi lần deploy đều đi qua
`deploy_vps.sh` nên vẫn có health gate, auto-rollback và prune an toàn.

> Đường GitHub Actions đầy đủ (tag `v*` → test → build → duyệt → deploy) đã được
> lược bỏ khỏi repo để giảm phức tạp. Nếu cần lại, xem lịch sử git của
> `.github/workflows/release.yml` và các script `02`–`04`.

---

## 1. Thành phần

| Thành phần | Vai trò |
|---|---|
| `CI-CD/07_deploy_now.sh` | Build/push local (tuỳ chọn) rồi SSH deploy ngay |
| `CI-CD/00_check_vps.sh` | Preflight VPS: repo sạch, `git pull`, `.env`, compose, sudo, Docker |
| `CI-CD/01_setup_ssh.sh` | Tạo deploy key ed25519, pin host key, test SSH/sudo |
| `CI-CD/05_verify.sh` | Health công khai + `.env`/container/image/health trên VPS |
| `CI-CD/06_rollback.sh` | Rollback về tag `previous`, tự khôi phục `.env` nếu lỗi |
| `CI-CD/_lib.sh` | Nạp config, validate, wrapper SSH pin host key |
| `CI-CD/config.sh` | Giá trị hạ tầng của bạn (gitignore, không commit) |
| `deploy/registry/deploy_vps.sh` | Pull image, recreate stack, health gate, auto-rollback, prune |
| `deploy/registry/build_and_push.sh` | Build/push `ekp-backend` + `ekp-nginx` (`linux/amd64`) |
| `.github/workflows/ci.yml` | CI tuỳ chọn: pytest + build frontend khi push/PR (không cần secrets) |

---

## 2. Điều kiện một lần

### Trên VPS

- Docker Engine + Docker Compose v2.
- Repo đã clone, đang ở nhánh `main`, có `.env` (chmod 600).
- `git pull` chạy được **không tương tác** (credential/PAT còn hạn).
- User SSH có `sudo -n true` thành công (NOPASSWD). Kiểm tra:

```bash
cd <app-dir>
git status --porcelain          # phải trống với file tracked
git pull --ff-only
sudo -n true && echo SUDO_OK
```

Nếu sudo còn hỏi mật khẩu, thêm rule (chỉ nên làm trên VPS demo một mục đích):

```bash
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/90-ekp-deploy
sudo chmod 440 /etc/sudoers.d/90-ekp-deploy
```

### Trên máy local

- Git Bash, `ssh`, `ssh-keyscan`, `curl`, `git`, Docker Desktop.
- Đăng nhập Docker Hub (chỉ cần khi build/push):

```bash
docker login -u neshaki091
```

---

## 3. Cấu hình

```bash
cd "/f/IT project/Enterprise/SaaS_Chatbot_platform"
cp CI-CD/config.example.sh CI-CD/config.sh
```

Sửa `CI-CD/config.sh`:

| Biến | Ý nghĩa | Ghi chú |
|---|---|---|
| `VPS_HOST` | Hostname hoặc IP khi SSH | Chỉ hostname/IP, không kèm `https://` hay `/health` |
| `VPS_USER` | User SSH | `whoami` trên VPS |
| `VPS_APP_DIR` | Đường dẫn tuyệt đối repo trên VPS | `pwd` trong repo |
| `VPS_SSH_KEY` | Private key riêng cho deploy | Mặc định `~/.ssh/ekp_deploy` |
| `VPS_KNOWN_HOSTS` | Host key đã pin | Mặc định `~/.ssh/ekp_known_hosts` |
| `DOCKERHUB_USERNAME` | Namespace Docker Hub | Dùng khi build/push local |
| `PUBLIC_HEALTH_URL` | URL health công khai | Phải là domain có TLS; để trống nếu chưa có |

`CI-CD/config.sh` đã nằm trong `.gitignore`; **không commit** file này.

Đổi từ domain sang IP (hoặc ngược lại) thì phải chạy lại `01`: host key được pin
theo đúng chuỗi host, lệch sẽ báo `Host key verification failed`.

---

## 4. Thiết lập lần đầu

```bash
cd "/f/IT project/Enterprise/SaaS_Chatbot_platform"

# 1) Kiểm tra VPS bằng SSH hiện có (chưa cần deploy key)
bash CI-CD/00_check_vps.sh
#    Kỳ vọng: SUDO_OK, COMPOSE_CONFIG_OK, CHECK_OK

# 2) Tạo deploy key riêng + pin host key + test sudo
bash CI-CD/01_setup_ssh.sh
#    So fingerprint ed25519 in ra với trên VPS:
#    ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub

# 3) Đăng nhập Docker Hub để build/push
docker login -u neshaki091
```

> Nếu `00` báo `Permission denied (publickey)` — thường gặp khi bạn chỉ vào VPS
> bằng GCP Console **browser SSH**, máy local không có private key:
> chạy `01` trước. Script tạo key và in sẵn một lệnh `echo 'ssh-ed25519 ...' >>
> ~/.ssh/authorized_keys`; mở browser SSH, dán lệnh đó, rồi chạy lại `01`.
> Khi `01` báo `KEY_OK / SUDO_OK` thì quay lại `00`.

---

## 5. Các script hỗ trợ

| Script | Việc làm | Chạy khi |
|---|---|---|
| `00_check_vps.sh` | Repo sạch, `git pull` non-interactive, `.env`, compose config, sudo, Docker | Trước khi deploy, sau khi VPS thay đổi |
| `01_setup_ssh.sh` | Tạo ed25519, cài public key, `ssh-keyscan` pin host key, test | Một lần (hoặc khi đổi host/key) |
| `05_verify.sh` | Health HTTPS + `.env`/container/image/health trên VPS | Sau khi deploy |
| `06_rollback.sh` | Rollback về tag `previous`, tự khôi phục `.env` nếu lỗi | Khi bản mới có vấn đề |
| `07_deploy_now.sh` | Build/push local (tuỳ chọn) rồi SSH deploy ngay | Mỗi lần phát hành |
| `_lib.sh` | Nạp config, validate, wrapper SSH pin host key | Được source bởi các script trên |
| `config.example.sh` | Mẫu cấu hình | Copy thành `config.sh` |

`07_deploy_now.sh` có `--build` (build/push trước khi deploy) và `--dry-run`
(chỉ in việc sẽ làm). `06_rollback.sh` có `--yes`.

---

## 6. Phát hành bản mới

Version chỉ nằm ở một file:

```bash
sed -i 's/APP_VERSION = "2.3.2"/APP_VERSION = "2.3.3"/' backend/app/core/version.py
git add backend/app/core/version.py
git commit -m "release: v2.3.3"
git push origin main
```

Rồi:

```bash
# Xem trước (không build, không đụng VPS)
bash CI-CD/07_deploy_now.sh --dry-run

# Build + push + deploy
bash CI-CD/07_deploy_now.sh --build

# Xác minh
bash CI-CD/05_verify.sh
```

`07` chạy tuần tự:

1. Đọc `APP_VERSION` (hoặc tag truyền vào), từ chối `latest`/ký tự lạ.
2. `--build`: build/push `ekp-backend` + `ekp-nginx` với `DOCKERHUB_USERNAME`.
3. SSH vào VPS: `sudo -n` → `git pull --ff-only` → kiểm tra
   `DOCKER_IMAGE_PREFIX` khớp → cập nhật `IMAGE_TAG` trong `.env` (chmod 600).
4. Chạy `deploy_vps.sh`: pull image, recreate, health gate, auto-rollback, prune.
5. Nếu deploy lỗi, khôi phục `IMAGE_TAG` cũ trong `.env`.
6. Gọi `PUBLIC_HEALTH_URL` nếu đã cấu hình.

Các biến thể:

```bash
bash CI-CD/07_deploy_now.sh 2.3.3        # deploy tag đã có trên Docker Hub, không build
bash CI-CD/07_deploy_now.sh --build 2.3.3
```

Quy tắc:

- Không build lại cùng một version rồi push đè image đã phát hành; hãy bump version.
- Không dùng `latest` cho production; deploy script từ chối tag này.
- Chạy test local trước khi deploy nếu thay đổi backend/frontend:
  `python -m pytest backend/tests` và `npm run build` trong `frontend/`.
- **Không push tag `v*`** — repo không còn workflow release; tag không cần thiết.

---

## 7. Xác minh sau deploy

```bash
bash CI-CD/05_verify.sh
```

Script kiểm tra:

- `PUBLIC_HEALTH_URL` trả `"status": "healthy"` (nếu đã cấu hình).
- Trên VPS: `IMAGE_TAG` trong `.env`, container đang chạy, image EKP còn lại,
  `/health` nội bộ qua Nginx.

Trực tiếp trên VPS nếu cần:

```bash
cd <app-dir>
grep '^IMAGE_TAG=' .env
sudo docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
curl -fsS http://127.0.0.1/health
```

---

## 8. Khi deploy thất bại / rollback

Hai lớp bảo vệ:

1. `deploy_vps.sh` health fail → tự rollback về image trước bằng tag
   `rollback-YYYYmmdd-HHMMSS`.
2. `07` khôi phục `IMAGE_TAG` cũ trong `.env` để `docker compose` thủ công
   không kéo lại image lỗi.

Rollback thủ công:

```bash
bash CI-CD/06_rollback.sh
# hoặc trên VPS:
cd <app-dir>
sudo IMAGE_TAG=previous bash deploy/registry/deploy_vps.sh --no-pull --no-prune
```

Lưu ý: rollback image **không** rollback migration CSDL. Migration phải tương thích
ngược (expand/contract) để image cũ chạy được trên schema mới.

---

## 9. Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Xử lý |
|---|---|---|
| `Permission denied (publickey)` | Key chưa vào `authorized_keys`, sai user, hoặc bạn chỉ có browser SSH | Chạy `01`, dán public key in ra vào terminal browser SSH, chạy lại `01` rồi `00` |
| `Host key verification failed` | `known_hosts` pin sai host hoặc host key đổi | Chạy lại `01`; so fingerprint trước khi chấp nhận |
| `sudo: a password is required` | Thiếu NOPASSWD | Cấu hình sudoers như mục 2 |
| `could not read Username` / `Authentication failed` | Credential git trên VPS hết hạn | Làm mới PAT/credential; test `GIT_TERMINAL_PROMPT=0 git pull` |
| Docker Hub `unauthorized` / `insufficient_scope` | Chưa login hoặc token sai/thiếu quyền | `docker login -u neshaki091`; token Read & Write |
| `manifest unknown` | Sai `IMAGE_TAG` hoặc chưa push | Kiểm tra tag trên Docker Hub; build/push lại |
| `exec format error` | Image build sai kiến trúc | Build lại với `PLATFORM=linux/amd64` |
| Nginx `502` | Backend chưa healthy | `docker compose logs backend`; chờ migration xong |
| Health fail, deploy đỏ | Backend/migration/DB lỗi | Xem log; VPS đã rollback image; sửa và phát hành patch |
| Hết dung lượng khi pull | Image cũ còn nhiều | `deploy_vps.sh` prune mặc định; kiểm tra `docker system df` |

---

## 10. Quy tắc an toàn

- **Không commit** `.env`, `CI-CD/config.sh`, private key, token.
- Deploy key chỉ dùng cho việc deploy; có thể thu hồi bằng cách xoá dòng trong
  `~/.ssh/authorized_keys` trên VPS.
- Token Docker Hub chỉ cấp quyền cần thiết (Read & Write), không dùng mật khẩu.
- Script kiểm tra tag (charset, không `latest`), đường dẫn, kiến trúc image và
  từ chối chạy nếu thiếu cấu hình.
- Prune chỉ xoá image cũ của `ekp-backend`/`ekp-nginx` (giữ bản đang chạy + backup)
  và layer dangling; không xoá image tagged khác, không bao giờ `down -v`, không
  xoá volume PostgreSQL/FAISS.
- Host key được pin; không dùng `StrictHostKeyChecking=no` cho deploy thật.

---

## 11. Kiểm tra nhanh trạng thái

```bash
# VPS + stack
bash CI-CD/00_check_vps.sh
bash CI-CD/05_verify.sh

# Image trên Docker Hub (public)
curl -fsS https://hub.docker.com/v2/repositories/neshaki091/ekp-backend/tags/ | head
```

Quy trình còn lại: bump `APP_VERSION` → commit → push main →
`07_deploy_now.sh --build` → `05_verify.sh`.

Chi tiết khái niệm và lý do thiết kế: xem [learn.md](learn.md).
