# Học CI/CD qua chính dự án EKP

Tài liệu này giải thích **khái niệm** và **cách hệ thống CI/CD của EKP hoạt động**,
không chỉ các lệnh cần chạy. Đọc xong bạn có thể bảo vệ thiết kế này trước câu hỏi
kiểu: *Tại sao không build trên VPS? Tại sao cần duyệt? Rollback có mất dữ liệu không?*

---

## 1. CI/CD là gì

**CI (Continuous Integration)** là việc mỗi thay đổi code được tự động kiểm tra
(test, build, lint) ngay khi đưa lên nhánh chung, để lỗi lộ ra sớm thay vì lúc deploy.

**CD** có hai mức:

- **Continuous Delivery**: pipeline chuẩn bị sẵn một artifact (image) và dừng ở
  cổng duyệt; con người bấm approve mới phát hành.
- **Continuous Deployment**: không có cổng duyệt, artifact đạt test là tự lên production.

EKP chọn **Continuous Delivery**: tag → test → build/push → **duyệt** → deploy.
Lý do: một lần deploy sai có thể làm hỏng bản demo đang cho người khác dùng; cổng
duyệt là rẻ so với chi phí downtime, đồng thời tạo audit trail (ai duyệt, khi nào).

**Artifact** ở đây là Docker image bất biến. Pipeline không copy source lên VPS và
chạy lại; nó chỉ chuyển **cùng một image** đã được test tới nơi khác.

---

## 2. Bức tranh toàn cảnh

```
Máy local                    GitHub                        Docker Hub              VPS
─────────                    ──────                        ──────────              ───
git tag vX.Y.Z
   │
   ▼
git push tag ──────────────▶ meta (validate tag)
                              │
                              ▼
                            verify: pytest + npm build + version guard
                              │
                              ▼
                            publish: docker build ─────────▶ ekp-backend:X.Y.Z
                                     docker push             ekp-nginx:X.Y.Z
                                                              (và :latest)
                              │
                              ▼
                            environment "production"
                            (required reviewer)
                              │  approve
                              ▼
                            ssh ────────────────────────────────────────────────▶ git pull --ff-only
                                                                                 sed IMAGE_TAG
                                                                                 deploy_vps.sh
                                                                                   ├─ docker pull
                                                                                   ├─ compose up
                                                                                   ├─ health /health
                                                                                   └─ rollback nếu lỗi
                              ◀────────────────────────────────────────────────  exit code
                              │
                              ▼
                            curl PUBLIC_HEALTH_URL (tùy chọn)
```

Ba ranh giới tin cậy: GitHub (nơi giữ secret/token), Docker Hub (nơi giữ artifact),
VPS (nơi chạy production). Mỗi ranh giới có biện pháp riêng, giải thích ở mục 9.

---

## 3. GitHub Actions: các khái niệm cần nắm

| Khái niệm | Trong EKP |
|---|---|
| **Workflow** | `.github/workflows/release.yml`, `.github/workflows/ci.yml` |
| **Event** | `push` tag `v*`, `workflow_dispatch` (chạy tay), `pull_request` (CI) |
| **Job** | `meta`, `verify`, `publish`, `deploy` — mỗi job là một máy ảo riêng |
| **Step** | Một lệnh/action trong job; step sau thấy file của step trước |
| **Runner** | `ubuntu-latest` của GitHub (amd64, RAM/CPU đủ build) |
| **Context** | `github.*`, `needs.*`, `vars.*`, `secrets.*`, `inputs.*` |
| **Output** | `meta` xuất `tag`/`git_ref`; job sau dùng qua `needs.meta.outputs.*` |
| **Artifact** | Image đẩy lên Docker Hub, không dùng upload-artifact |
| **Environment** | `production`, gắn required reviewer + secrets riêng |
| **Concurrency** | `production-release` + `cancel-in-progress: false` chống 2 deploy chồng nhau |
| **Permissions** | Workflow chỉ có `contents: read`; không cần quyền ghi repo |

**Vì sao job tách nhau?** Mỗi job chạy trên máy ảo mới. `verify` và `publish` cùng
checkout đúng tag, nên bước build dùng đúng commit đã test — không phải trạng thái
mơ hồ của working tree. `deploy` không checkout source mà chỉ SSH, giảm bề mặt.

**Vì sao có `meta`?** Một chỗ duy nhất chuẩn hoá input: tag push hay chạy tay đều
thành `tag=2.3.3` và `git_ref=refs/tags/v2.3.3`. Validate ở đây chặn tag rác trước
khi tốn runner minutes.

**`workflow_dispatch` dùng khi nào?** Retry một tag đã có (sau khi sửa hạ tầng) mà
không tạo commit mới. Lưu ý nó **rebuild** tag đó, nên không dùng để rollback.

---

## 4. Tag, digest và tính bất biến

- **Tag** (`2.3.3`) là nhãn người đọc; có thể bị ghi đè.
- **Digest** (`sha256:...`) là hash nội dung image; không đổi nếu nội dung không đổi.

Production phải pin tag cụ thể, và tag đã phát hành **không được di chuyển**; nếu
cần sửa, phát hành version mới. `latest` bị `deploy_vps.sh` từ chối vì nó không cho
biết đang chạy bản nào và dễ gây "deploy ngầm" khi ai đó push `latest`.

Trong EKP, phiên bản ứng dụng nằm ở **một nguồn duy nhất**:

```
backend/app/core/version.py   APP_VERSION = "2.3.3"
        │
        ├─ FastAPI(version=APP_VERSION)
        ├─ /health trả "version"
        ├─ MCP SERVER_VERSION
        └─ test_health.py assert
```

CI kiểm tra `APP_VERSION` khớp tag trước khi build. Nhờ vậy không còn cảnh
`main.py` ghi 2.3.2 còn health ghi 2.3.1; và khi release chỉ sửa một file.

Build script tag cả `:X.Y.Z` và `:latest` (git tag có tiền tố `v`, image tag
không có `v`); VPS chỉ dùng tag cụ thể.

---

## 5. Job `verify`: test đúng thứ sẽ phát hành

Các bước:

1. Checkout đúng `refs/tags/vX.Y.Z` (không phải branch tip).
2. So `APP_VERSION` với tag; lệch là fail ngay.
3. `pip install -r requirements.txt` + `pytest backend/tests`.
4. `npm ci` + `npm run build` với Node 22.

Tại sao chạy lại test dù `main` đã có CI? Tag có thể trỏ tới commit cũ chưa từng
qua CI, hoặc CI main đã đỏ nhưng ai đó vẫn tag. Pipeline tự bảo vệ mình, không tin
trạng thái bên ngoài. Đây là nguyên tắc "**trust the artifact, verify the source**".

Cache pip/npm giảm thời gian nhưng không thay đổi kết luận; cache key dựa trên
lockfile/requirements.

---

## 6. Job `publish`: build một lần, chạy nhiều nơi

GitHub runner build:

- `Dockerfile` backend: multi-stage, runtime slim, user không đặc quyền.
- `frontend/Dockerfile`: Node build SPA → Nginx runtime.

Script `deploy/registry/build_and_push.sh` dùng `--platform linux/amd64` để khớp
e2-micro. Sau push, workflow ghi `RepoDigests` vào Step Summary — đây là bằng chứng
artifact nào đã được tạo.

**Vì sao không build trên VPS?** e2-micro có 1 GB RAM. `pip install faiss-cpu`,
`npm ci`, `vite build` dễ OOM, phải bật swap và rất chậm. Build trên runner:
- tận dụng CPU/RAM lớn, cache layer;
- VPS không cần toolchain → giảm bề mặt tấn công, giảm dung lượng;
- cùng một image chạy ở local test và VPS → dễ tái lập lỗi.

Đánh đổi: phụ thuộc Docker Hub + runner; nếu registry sập, deploy bị chặn. Với quy
mô demo, đánh đổi này hợp lý hơn build trên máy 1 GB.

---

## 7. Cổng duyệt (Environment)

`deploy` job khai báo:

```yaml
environment:
  name: production
```

GitHub hiểu đây là môi trường production. Nếu environment có **Required reviewers**,
job dừng ở trạng thái *Waiting for approval* cho tới khi một reviewer bấm duyệt.
Secret của environment (VPS key, host) chỉ job này đọc được; job `publish` không có
quyền thấy chúng.

Chi tiết dễ sai:

- Nếu chỉ có một reviewer, phải **tắt Prevent self-review**, nếu không bạn không
  thể tự duyệt và job treo mãi.
- Docker Hub secrets nằm ở **repo level** vì `publish` không dùng environment.
- Approval diễn ra **sau khi publish**. Nghĩa là image đã sẵn sàng, người duyệt chỉ
  quyết định thời điểm đưa lên production.

Audit trail: GitHub ghi ai approve, thời điểm, run nào. Đây là thứ pipeline
"auto mọi thứ" không có.

---

## 8. Deploy qua SSH: vì sao an toàn hơn vẻ ngoài

Workflow tạo file tạm:

```
~/.ssh/id_ed25519     ← secret VPS_SSH_KEY
~/.ssh/known_hosts    ← secret VPS_KNOWN_HOSTS
```

rồi gọi:

```
ssh -i key -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=known_hosts ...
```

Các điểm cần hiểu:

**BatchMode=yes**: không cho ssh hỏi mật khẩu/passphrase. Nếu key sai, fail ngay
thay vì treo runner 6 giờ.

**StrictHostKeyChecking=yes + known_hosts**: chống MITM. Nếu ai đó chiếm DNS và
trả về server giả, host key khác → ssh từ chối. `ssh-keyscan` chỉ là bước thu thập;
việc **so fingerprint** mới là bước tin cậy. Vì vậy `01_setup_ssh.sh` in fingerprint
để bạn đối chiếu với `/etc/ssh/ssh_host_ed25519_key.pub` trên VPS.

**Deploy key riêng**: không dùng key cá nhân; thu hồi bằng cách xoá khỏi
`authorized_keys`. Quyền tối thiểu: chỉ user đó, chỉ SSH.

**sudo -n**: `-n` nghĩa là không hỏi mật khẩu. Nếu user chưa có NOPASSWD, script
fail sớm với thông báo rõ thay vì treo. Trên GCP, user tạo instance thường đã nằm
trong `google-sudoers` (NOPASSWD).

**Không dùng mật khẩu**: password auth nên tắt; key-only giảm brute force.

**GIT_TERMINAL_PROMPT=0**: `git pull` trên VPS không được hỏi username/PAT. Nếu
credential hết hạn, fail ngay. Đây là lý do `00_check_vps.sh` chạy pull với biến này.

Lệnh remote được truyền qua stdin (`bash -s`), không nhét chuỗi dài vào argv; tag
và đường dẫn đã được validate charset trước khi nội suy, tránh shell injection.

**Trường hợp chỉ có GCP browser SSH**: máy local không có private key nào, nên
`ssh user@host` từ Git Bash sẽ `Permission denied (publickey)`. `01_setup_ssh.sh`
giải quyết bằng cách tạo key trước, in sẵn lệnh thêm public key để dán vào terminal
browser, rồi test lại. Đây cũng là lý do script tạo `known_hosts` **trước** khi thử
cài key: dù bước cài tự động thất bại, bạn vẫn có host key đã pin để dùng tiếp.

---

## 9. Mô hình mối đe doạ và biện pháp

| Rủi ro | Hậu quả | Biện pháp trong EKP |
|---|---|---|
| Lộ Docker Hub token | Kẻ xấu push image độc | Token Read/Write tối thiểu; secret GitHub; có thể thu hồi |
| Lộ private key deploy | Chiếm SSH VPS | Key riêng, không commit; xoá khỏi `authorized_keys` khi nghi ngờ |
| MITM SSH | Chạy lệnh trên server giả | `known_hosts` pin + fingerprint; `StrictHostKeyChecking=yes` |
| Tag rác/`latest` | Deploy nhầm bản | Validate `v*`, từ chối `latest`, version guard khớp tag |
| Tag cũ bị di chuyển | Artifact không tái lập | Quy ước không tái sử dụng tag; bump version |
| Approve nhầm | Bản lỗi lên production | Required reviewer; health gate; auto-rollback |
| Hai deploy chồng nhau | Container trạng thái hỗn hợp | `concurrency` + `flock` trong deploy script |
| Migration không tương thích | Rollback image chạy trên schema mới bị lỗi | Quy tắc expand/contract; không rollback DB tự động |
| Prune nhầm dữ liệu | Mất DB/FAISS | Script không bao giờ `down -v`; chỉ `docker rmi` image EKP |
| Secret trong log | Lộ token | Không echo; GitHub mask; token nhập prompt, không ghi file |

Nguyên tắc: **fail closed**. Thiếu biến, thiếu key, sai host, tag lạ, health không
đạt → dừng, không cố chạy tiếp.

---

## 10. Bên trong `deploy_vps.sh`

Đây là nơi "data-safe" được thực thi:

1. **Preflight**: Docker/Compose v2, lock `flock`, compose/.env tồn tại, chmod 600,
   tag hợp lệ (không `latest`), đĩa tối thiểu 512 MB.
2. **Chụp trạng thái cũ**: image ID của backend/nginx đang chạy, trước khi pull.
3. **Validate**: `docker compose config -q` — bắt thiếu biến trước khi thay đổi gì.
4. **Pull có chọn lọc**: chỉ `backend` + `nginx`; PostgreSQL không bị pull/recreate.
5. **Kiểm tra kiến trúc**: image phải cùng arch với server (amd64/arm64).
6. **Recreate**: `up -d --remove-orphans --no-build`.
7. **Health gate**: gọi `http://127.0.0.1/health` (Nginx :80 proxy sang backend).
   Backend health kiểm tra `SELECT 1`, thư mục FAISS, cấu hình LLM/embedding.
8. **Rollback nếu health fail**: tag image cũ thành `rollback-<timestamp>`,
   `IMAGE_TAG=rollback-... compose up -d`, chờ health 90s. In log backend/nginx.
9. **Sau thành công**: gắn tag `previous` cho image vừa bị thay thế, prune image cũ
   của đúng 2 repo, giữ bản đang chạy + `--keep` backup (mặc định 1).
10. **Không đụng volume**; `docker image prune -f` chỉ xoá image dangling
    (không có tag), không xoá image tagged của dự án khác.

Vì sao rollback theo **image ID** chứ không phải tag? Image ID là bất biến; tag có
thể đã bị di chuyển. Ta chụp ID trước khi pull để chắc chắn quay lại đúng layer cũ.

Vì sao vẫn cảnh báo migration? Rollback image không thể "undo" `alembic upgrade`.
Nếu migration phá vỡ tương thích ngược, image cũ có thể không chạy. Quy tắc:
migration phải expand/contract; deploy version mới trước, xoá cột cũ ở version sau.

**Health timeout**: mặc định 120s; có thể tăng bằng `HEALTH_TIMEOUT_SECONDS`.
Nếu DB lớn khiến migration lâu hơn healthcheck của compose, `up` có thể fail trước
khi script kịp chờ; khi đó tăng `start_period` trong healthcheck hoặc chạy migration
trước theo cửa sổ bảo trì.

---

## 11. Health check đi qua những đâu

```
CI runner → https://<domain>/health
                │ TLS
                ▼
             Nginx :443 (hoặc :80 /health không redirect)
                │ proxy
                ▼
             FastAPI /api/v1/health
                ├─ SELECT 1  → PostgreSQL
                ├─ kiểm tra thư mục FAISS
                └─ trả {"status":"healthy","version":...}
```

- `deploy_vps.sh` kiểm tra **loopback** để không phụ thuộc DNS/TLS lúc deploy.
- Workflow kiểm tra **public URL** sau deploy để bắt lỗi TLS/DNS/CORS mà loopback
  không thấy. Đây là hai lớp khác nhau, không thay thế nhau.
- HTTP 503 khi DB mất kết nối; script coi đó là fail.

---

## 12. Các lựa chọn khác và trade-off

| Cách | Ưu | Nhược | Khi nào chọn |
|---|---|---|---|
| **SSH từ CI** (đang dùng) | Đơn giản, kiểm soát thời điểm, audit, rollback sẵn | Cần mở 22 cho runner, quản lý key, DNS/registry là SPOF | VPS nhỏ, ít môi trường |
| **Build + deploy từ máy local** (`07_deploy_now.sh`) | Không cần secrets/environment/gh; dùng Docker login + SSH key sẵn có; vẫn health gate/rollback | Máy local phải đăng nhập registry; không test tự động trước deploy; log không tập trung | Một người vận hành, giai đoạn đầu, demo nhanh |
| VPS tự poll registry (timer) | Không mở SSH, không cần runner deploy | Chậm, khó approve, khó biết ai deploy, cần script so digest | Mạng chặn inbound, nhiều VPS |
| Self-hosted runner trên VPS | Không mở SSH | Runner ngốn RAM/CPU trên máy 1 GB, bề mặt tấn công lớn | Không khuyến nghị ở đây |
| Kubernetes + GitOps | Chuẩn công nghiệp, tự reconcile | Nặng, vận hành phức tạp, không hợp e2-micro | Khi scale nhiều node |
| PaaS (Cloud Run...) | Ít vận hành | Khác ràng buộc đồ án (VPS/Postgres tự quản) | Khi bỏ ràng buộc |

Pipeline hiện tại là điểm cân bằng giữa **an toàn dữ liệu**, **chi phí 0 đồng**
và **khả năng giải thích khi phỏng vấn**.

Đường nhẹ không phải là "bỏ CI/CD" mà là **dịch chuyển trigger từ GitHub sang
máy bạn**: vẫn giữ nguyên phần CD quan trọng nhất là `deploy_vps.sh` (pull-only,
health gate, rollback, prune an toàn). Khác biệt nằm ở ba điểm: (1) image do máy
local build/push nên phải `docker login` trước; (2) không có job verify nên bạn
tự chịu trách nhiệm chạy test; (3) không có approval/audit trên GitHub — log chỉ
ở terminal và trên VPS. Khi cần chia sẻ quy trình với người khác hoặc muốn lịch
sử deploy đáng tin, hãy chuyển sang đường đầy đủ: chỉ cần cấu hình secrets một
lần, không phải viết lại script.

---

## 13. Luồng lỗi và cách đọc

| Tầng | Dấu hiệu | Xem ở đâu |
|---|---|---|
| meta | Tag sai/`latest` | Log job meta |
| verify | Test/version/frontend fail | Pytest/npm log |
| publish | Docker login/build/push fail | Log build, Docker Hub |
| approval | Job đứng chờ | Environments/Review deployments |
| SSH | Timeout, publickey, host key, sudo | Log deploy |
| git pull | Credential/non-ff | Log deploy |
| compose up | Thiếu biến, port, arch | `compose ps`, log backend |
| health | 503/không healthy | Log backend, DB, migration |
| public health | TLS/DNS/CORS | `curl -v`, cert, DNS |

Nguyên tắc debug: **đọc log của tầng gần lỗi nhất**, đừng đoán. Nếu deploy fail,
VPS đã ở trạng thái rollback (hoặc trạng thái lỗi nếu rollback cũng fail) — kiểm tra
`docker ps` trước khi thao tác tiếp.

---

## 14. Mở rộng

- Thêm **staging environment**: cùng workflow, tag `v*-rc*`, secret trỏ VPS staging,
  deploy job thứ hai.
- **GHCR private**: đổi `DOCKER_IMAGE_PREFIX=ghcr.io/...`, cấp PAT `read:packages`
  cho VPS nếu private; thêm `permissions: packages: write` cho job publish để dùng
  `GITHUB_TOKEN` thay token riêng.
- **Image scanning/SBOM**: thêm Trivy/Grype sau publish trước approval.
- **Branch protection + tag ruleset**: chỉ maintainer được tạo tag `v*`.
- **Thông báo**: Slack/Discord webhook ở job deploy, không chứa secret.
- **Backup trước deploy**: `pg_dump` vào object storage trước migration lớn; hiện
  rollback chỉ ở tầng image.
- **Ký image (cosign)**: đảm bảo VPS chỉ chạy image do pipeline tạo.

---

## 15. Câu hỏi tự kiểm tra

1. **Vì sao VPS không build image?**
   e2-micro 1 GB RAM; build dễ OOM/chậm; image build trên runner đảm bảo tái lập,
   giảm toolchain và bề mặt tấn công trên VPS.

2. **Vì sao cần `known_hosts` thay vì `StrictHostKeyChecking=no`?**
   Không có pin, kẻ tấn công DNS có thể đưa SSH tới server giả và nhận key deploy.

3. **Tại sao tag phải khớp `APP_VERSION`?**
   Tránh phát hành image ghi version này nhưng source ghi version khác; health/API
   và tag phải nhất quán để điều tra sự cố.

4. **Rollback có khôi phục dữ liệu PostgreSQL không?**
   Không. Chỉ image/container. Migration đã chạy vẫn ở đó; vì vậy migration phải
   tương thích ngược.

5. **Tại sao approval đặt sau publish?**
   Image được build/test xong mới cần quyết định phát hành; duyệt trước build sẽ
   kéo dài thời gian và không giải quyết rủi ro.

6. **Vì sao script prune không xoá volume?**
   Volume chứa dữ liệu thật; image có thể pull lại, dữ liệu thì không. Mọi thao
   tác xoá volume phải là hành động riêng, có backup.

7. **Nếu mất tag `previous`, rollback thế nào?**
   Dùng tag cụ thể còn trên Docker Hub (nếu chưa overwrite) hoặc image ID còn trên
   VPS; nếu đã prune, pull lại tag cũ từ registry rồi deploy thủ công.

8. **Vì sao workflow chỉ có `contents: read`?**
   Không cần ghi repo. Giảm thiệt hại nếu workflow bị sửa độc hại; quyền tối thiểu.

9. **Vì sao vẫn cần `git pull` trên VPS nếu image đã chứa code?**
   Compose, Nginx config, script deploy và TLS/webroot mount nằm trong repo, không
   nằm trong image app. `git pull` giữ các file đó đồng bộ với tag phát hành.

10. **Hai lớp health check khác nhau thế nào?**
    Loopback trong deploy script kiểm tra stack nội bộ; public URL kiểm tra
    DNS/TLS/edge từ Internet. Một cái đạt không bảo đảm cái kia.

---

Tài liệu vận hành: [README.md](README.md).
