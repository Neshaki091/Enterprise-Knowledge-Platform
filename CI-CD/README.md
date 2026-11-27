# CI-CD — Hướng dẫn phát hành EKP qua GitHub Actions

Thư mục này chứa bộ script hỗ trợ vận hành pipeline:

```
Tag vX.Y.Z ──▶ GitHub Actions ──▶ Docker Hub ──▶ duyệt ──▶ SSH VPS ──▶ health gate
               (test/build)       (image)       (người)   (pull-only)  (rollback nếu lỗi)
```

Nguyên tắc cốt lõi: **VPS không build**. Image được build trên GitHub runner
(hoặc trên máy local), VPS chỉ `git pull` code/config và pull image từ Docker Hub.

Có hai đường phát hành:

| Đường | Dùng khi | Lệnh chính |
|---|---|---|
| Đầy đủ (GitHub Actions) | Cần test tự động, duyệt, lịch sử run | `04_release.sh` → approve trên GitHub |
| Nhẹ (máy local) | Chưa muốn cấu hình secrets/environment/gh | `07_deploy_now.sh --build` |

Cả hai đều gọi chung `deploy_vps.sh` nên vẫn có health gate và auto-rollback.

---

## 1. Thành phần

| Thành phần | Vai trò |
|---|---|
| `.github/workflows/release.yml` | Pipeline phát hành khi push tag `v*` |
| `.github/workflows/ci.yml` | Test backend + build frontend trên push/PR vào `main` |
| `deploy/registry/deploy_vps.sh` | Pull image, recreate stack, health gate, auto-rollback, prune |
| `deploy/registry/build_and_push.sh` | Build/push `ekp-backend` + `ekp-nginx` |
| `CI-CD/config.sh` | Giá trị hạ tầng của bạn (gitignore, không commit) |
| `CI-CD/00..07_*.sh` | Script hỗ trợ từng bước (xem bảng ở mục 5) |

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
sudo ufw status                 # port 22 phải mở cho GitHub runner
```

Nếu sudo còn hỏi mật khẩu, thêm rule (chỉ nên làm trên VPS demo một mục đích):

```bash
echo "$USER ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/90-ekp-deploy
sudo chmod 440 /etc/sudoers.d/90-ekp-deploy
```

### Trên GitHub

- Repo private, Actions bật.
- Environment tên `production` có **Required reviewers**.
- Nếu bạn là người duyệt duy nhất: **tắt “Prevent self-review”**.
- Docker Hub: tạo Access Token quyền **Read & Write**.
- `gh` CLI (tuỳ chọn nhưng nên có): `winget install --id GitHub.cli`, mở lại
  Git Bash, `gh auth login`.

### Trên máy local

- Git Bash, `ssh`, `ssh-keyscan`, `curl`, `git`.
- `gh` nếu dùng `02`/`03`.

---

## 3. Cấu hình

```bash
cd "/f/IT project/Enterprise/SaaS_Chatbot_platform"
cp CI-CD/config.example.sh CI-CD/config.sh
```

Sửa `CI-CD/config.sh`:

| Biến | Ý nghĩa | Ghi chú |
|---|---|---|
| `VPS_HOST` | Hostname hoặc IP khi SSH | Phải khớp `known_hosts`; nên dùng DuckDNS |
| `VPS_USER` | User SSH | `whoami` trên VPS |
| `VPS_APP_DIR` | Đường dẫn tuyệt đối repo trên VPS | `pwd` trong repo |
| `VPS_SSH_KEY` | Private key riêng cho CI | Mặc định `~/.ssh/ekp_deploy` |
| `VPS_KNOWN_HOSTS` | Host key đã pin | Mặc định `~/.ssh/ekp_known_hosts` |
| `REPO_SLUG` | `owner/repo` | |
| `GITHUB_ENV_NAME` | Environment duyệt | Mặc định `production` |
| `DOCKERHUB_USERNAME` | Namespace Docker Hub | |
| `PUBLIC_HEALTH_URL` | URL health công khai | Phải là domain có TLS; để trống nếu chưa có |

`CI-CD/config.sh` đã nằm trong `.gitignore`; **không commit** file này.

Đổi từ domain sang IP (hoặc ngược lại) thì phải chạy lại `01` và `02`: host key
được pin theo đúng chuỗi host, lệch sẽ báo `Host key verification failed`.

---

## 4. Thiết lập lần đầu (theo thứ tự)

```bash
cd "/f/IT project/Enterprise/SaaS_Chatbot_platform"

# 1) Kiểm tra VPS bằng SSH hiện có (chưa cần deploy key)
bash CI-CD/00_check_vps.sh
#    Kỳ vọng: SUDO_OK, COMPOSE_CONFIG_OK, CHECK_OK

# 2) Tạo deploy key riêng + pin host key + test sudo
bash CI-CD/01_setup_ssh.sh
#    So fingerprint ed25519 in ra với trên VPS:
#    ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub

# 3) Trên GitHub tạo Environment "production" + Required reviewers
#    (Settings → Environments), tắt Prevent self-review nếu chỉ có 1 người duyệt

# 4) Cài gh + đăng nhập, rồi đẩy secrets/variables
winget install --id GitHub.cli      # mở lại Git Bash
gh auth login
bash CI-CD/02_github_secrets.sh     # nhập Docker Hub token tại prompt

# 5) CI trên main phải xanh trước khi release
bash CI-CD/03_check_ci.sh --watch
```

> Nếu `00` báo `Permission denied (publickey)` — thường gặp khi bạn chỉ vào VPS
> bằng GCP Console **browser SSH**, máy local không có private key:
> chạy `01` trước. Script tạo key và in sẵn một lệnh `echo 'ssh-ed25519 ...' >>
> ~/.ssh/authorized_keys`; mở browser SSH, dán 3 lệnh đó, rồi chạy lại `01`.
> Khi `01` báo `KEY_OK / SUDO_OK` thì quay lại `00`.

`02` đặt:

| Nơi lưu | Tên |
|---|---|
| Repo secrets | `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` |
| Environment secrets | `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_KNOWN_HOSTS` |
| Environment variables | `VPS_APP_DIR`, `PUBLIC_HEALTH_URL` (tuỳ chọn) |

Docker Hub secrets phải ở **cấp repository** vì job publish không dùng environment.

---

## 5. Các script hỗ trợ

| Script | Việc làm | Chạy khi |
|---|---|---|
| `00_check_vps.sh` | Repo sạch, `git pull` non-interactive, `.env`, compose config, sudo, Docker, UFW | Trước khi bật CI/CD, sau khi VPS thay đổi |
| `01_setup_ssh.sh` | Tạo ed25519, cài public key, `ssh-keyscan` pin host key, test | Một lần (hoặc khi đổi host/key) |
| `02_github_secrets.sh` | Kiểm tra Environment, đẩy secrets/variables lên GitHub | Một lần (hoặc khi đổi giá trị) |
| `03_check_ci.sh` | Liệt kê/theo dõi run `ci.yml` trên main | Trước mỗi release |
| `04_release.sh` | Đọc `APP_VERSION`, kiểm tra, push main + tag annotated | Mỗi lần phát hành |
| `05_verify.sh` | Health HTTPS + `.env`/container/image/health trên VPS | Sau khi deploy |
| `06_rollback.sh` | Rollback về tag `previous`, tự khôi phục `.env` nếu lỗi | Khi bản mới có vấn đề |
| `07_deploy_now.sh` | Build/push local (tuỳ chọn) rồi SSH deploy ngay, không cần GitHub | Đường nhẹ, xem mục 5b |
| `_lib.sh` | Nạp config, validate, wrapper SSH pin host key | Được source bởi các script trên |
| `config.example.sh` | Mẫu cấu hình | Copy thành `config.sh` |

`04_release.sh` có `--dry-run` (chỉ kiểm tra) và `--yes` (bỏ xác nhận).
`03_check_ci.sh` có `--watch`. `06_rollback.sh` có `--yes`.
`07_deploy_now.sh` có `--build` và `--dry-run`.

---

## 5b. Đường nhẹ — deploy từ máy local

Không cần GitHub secrets, Environment, approval hay `gh`. Dùng đúng những thứ
đã có: Docker Hub login trên máy local + deploy key SSH ở `01_setup_ssh.sh`.

Chuẩn bị một lần:

```bash
docker login -u neshaki091          # nếu máy chưa đăng nhập Docker Hub
bash CI-CD/01_setup_ssh.sh          # nếu chưa có key/known_hosts
bash CI-CD/00_check_vps.sh          # xác nhận VPS sẵn sàng
```

Phát hành:

```bash
# 1) Bump version (tránh ghi đè image đã publish)
sed -i 's/APP_VERSION = "2.3.2"/APP_VERSION = "2.3.3"/' backend/app/core/version.py
git add backend/app/core/version.py
git commit -m "release: v2.3.3"
git push origin main

# 2) Xem trước rồi chạy thật
bash CI-CD/07_deploy_now.sh --dry-run
bash CI-CD/07_deploy_now.sh --build
```

`07` build/push `ekp-backend` + `ekp-nginx` với tag `APP_VERSION`, rồi SSH vào
VPS: `git pull --ff-only`, cập nhật `IMAGE_TAG` trong `.env`, chạy
`deploy_vps.sh` (health gate + auto-rollback). Nếu deploy lỗi, script khôi phục
`IMAGE_TAG` cũ trong `.env`; image/migration xử lý như mục 8.

Các biến thể:

```bash
bash CI-CD/07_deploy_now.sh 2.3.3        # deploy tag đã có trên Docker Hub, không build
bash CI-CD/07_deploy_now.sh --build 2.3.3
```

Lưu ý đường nhẹ:

- Không có bước test tự động trước khi deploy. Chạy `pytest`/build frontend ở
  local nếu cần; CI trên `main` vẫn chạy độc lập khi bạn push.
- Không có lịch sử approval trên GitHub; log nằm ở terminal local và trên VPS
  (`docker logs`, output `deploy_vps.sh`).
- **Không push tag `v*`** khi chưa cấu hình đủ secrets/environment — workflow
  release sẽ chạy và dừng ở job publish/deploy. Tag `v*` chỉ dành cho đường đầy đủ.
- Rollback vẫn dùng `06_rollback.sh` hoặc lệnh thủ công ở mục 8.

---

## 6. Phát hành bản mới

Version chỉ nằm ở một file:

```bash
sed -i 's/APP_VERSION = "2.3.2"/APP_VERSION = "2.3.3"/' backend/app/core/version.py
git add backend/app/core/version.py
git commit -m "release: v2.3.3"
git push origin main

bash CI-CD/04_release.sh --dry-run   # kiểm tra tag/version/nhánh
bash CI-CD/04_release.sh             # push main + tag v2.3.3
```

Sau đó vào GitHub → **Actions → Release & Deploy → Approve deployment**.
Job deploy chỉ chạy sau khi được duyệt; hết health gate script mới prune image cũ.

Quy tắc:

- Không phát hành lại tag đã có (pipeline sẽ rebuild và ghi đè image tag đó).
- Không dùng `latest` cho production; deploy script từ chối tag này.
- Tag phải trỏ tới commit có `.github/workflows/release.yml` và
  `APP_VERSION` khớp tag.
- File tracked còn sửa thì `04` sẽ chặn; commit/stash trước.

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

Pipeline có hai lớp bảo vệ:

1. `deploy_vps.sh` health fail → tự rollback về image trước bằng tag
   `rollback-YYYYmmdd-HHMMSS`; job đỏ.
2. Workflow khôi phục `IMAGE_TAG` cũ trong `.env` để lần `docker compose` thủ công
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
| Docker Hub `unauthorized` / `insufficient_scope` | Token sai/thiếu quyền | Tạo token Read & Write, cập nhật `02` |
| Deploy job chờ mãi | Chưa approve hoặc bật Prevent self-review | Approve; tắt self-review nếu chỉ có 1 reviewer |
| `ConnectTimeout` | Firewall chặn 22 từ GitHub runner | Mở UFW/GCP firewall cho dải IP `actions` |
| Workflow không chạy khi push tag | Tag không bắt đầu bằng `v` / tag cũ thiếu workflow | Tạo tag mới trên commit hiện tại |
| Health fail, job đỏ | Backend/migration/DB lỗi | Xem log trong Actions; sửa và phát hành patch; VPS đã rollback |
| Hết dung lượng khi pull | Image cũ còn nhiều | `deploy_vps.sh` prune mặc định; kiểm tra `docker system df` |

---

## 10. Quy tắc an toàn

- **Không commit** `.env`, `CI-CD/config.sh`, private key, token.
- Deploy key chỉ dùng cho CI; có thể thu hồi bằng cách xoá dòng trong
  `~/.ssh/authorized_keys` trên VPS.
- Token Docker Hub chỉ cấp quyền cần thiết (Read & Write), không dùng mật khẩu.
- Environment `production` phải có reviewer; workflow có `permissions: contents: read`.
- Script kiểm tra tag (charset, không `latest`), đường dẫn, kiến trúc image và
  từ chối chạy nếu thiếu cấu hình.
- Prune chỉ xoá image cũ của `ekp-backend`/`ekp-nginx` (giữ bản đang chạy + backup)
  và layer dangling; không xoá image tagged khác, không bao giờ `down -v`, không
  xoá volume PostgreSQL/FAISS.
- Host key được pin; không dùng `StrictHostKeyChecking=no` cho deploy thật.

---

## 11. Kiểm tra nhanh trạng thái

```bash
# CI main
bash CI-CD/03_check_ci.sh

# VPS + stack
bash CI-CD/00_check_vps.sh
bash CI-CD/05_verify.sh

# Image trên Docker Hub (public)
curl -fsS https://hub.docker.com/v2/repositories/neshaki091/ekp-backend/tags/ | head
```

Nếu mọi bước trên đạt, quy trình còn lại chỉ là: bump `APP_VERSION` → commit →
push main → `04_release.sh` → approve → `05_verify.sh`.
Chưa cấu hình GitHub thì dùng đường nhẹ: bump `APP_VERSION` → commit → push →
`07_deploy_now.sh --build` → `05_verify.sh`.

Chi tiết khái niệm và lý do thiết kế: xem [learn.md](learn.md).
