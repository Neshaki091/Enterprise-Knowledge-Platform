# Học CI/CD qua chính dự án EKP

Tài liệu này giải thích **khái niệm** và **cách hệ thống CI/CD hiện tại của EKP
hoạt động**, không chỉ các lệnh cần chạy. Đọc xong bạn có thể bảo vệ thiết kế này
trước câu hỏi kiểu: *Tại sao không build trên VPS? Tại sao pin host key? Rollback
có mất dữ liệu không?*

> Repo hiện dùng **đường nhẹ**: build/push và deploy có thể chạy từ máy local
> hoặc trên GitHub (`build.yml`). Pipeline cũ có approval/Environment đã được
> lược bỏ để giảm phức tạp; khái niệm approval vẫn được nhắc ở mục 11.

---

## 1. CI/CD là gì

**CI (Continuous Integration)** là việc mỗi thay đổi code được tự động kiểm tra
(test, build, lint) ngay khi đưa lên nhánh chung, để lỗi lộ ra sớm thay vì lúc deploy.
Repo vẫn giữ `.github/workflows/ci.yml`: push/PR vào `main` sẽ chạy pytest và build
frontend. Workflow này **không cần secrets** và không deploy. Workflow
`.github/workflows/build.yml` (chạy tay) build/push image và có thể deploy luôn
lên VPS nếu đủ secrets; xem mục 5.

**CD (Continuous Delivery/Deployment)** là đưa artifact đã kiểm tra tới môi trường
chạy. EKP chọn **Continuous Delivery**: pipeline chuẩn bị image bất biến, nhưng
việc đưa lên production do con người kích hoạt bằng một lệnh. Lý do: một lần deploy
sai có thể làm hỏng bản demo đang dùng; một cổng người kiểm soát là rẻ so với
downtime, đồng thời bạn biết chính xác thời điểm thay đổi.

**Artifact** ở đây là Docker image. Pipeline không copy source lên VPS rồi build
lại; nó chuyển **cùng một image** đã build/test tới nơi khác.

---

## 2. Bức tranh toàn cảnh

```
Máy local                              Docker Hub                 VPS
─────────                              ──────────                 ───
docker login
   │
   ▼
build_and_push.sh
   ├─ docker build linux/amd64
   └─ docker push ───────────────────▶ ekp-backend:X.Y.Z
                                       ekp-nginx:X.Y.Z
                                            │
07_deploy_now.sh                            │ pull
   ├─ ssh (key + known_hosts) ──────────────┼──────────────────▶ git pull --ff-only
   │                                        │                    cập nhật IMAGE_TAG
   │                                        │                    deploy_vps.sh
   │                                        │                      ├─ docker pull
   │                                        │                      ├─ compose up
   │                                        │                      ├─ health /health
   │                                        │                      └─ rollback nếu lỗi
   └─ curl PUBLIC_HEALTH_URL (tuỳ chọn) ◀───┴────────────────────── exit code
```

Ba ranh giới tin cậy: **máy local hoặc GitHub runner** (giữ credential build +
deploy key), **Docker Hub** (giữ artifact), **VPS** (chạy production). GitHub có
thể chạy CI, build/push và deploy (`build.yml`, tuỳ chọn); deploy từ local là
fallback khi không muốn đưa key VPS lên GitHub.

Bước build và deploy trong sơ đồ có thể chuyển sang GitHub runner; các bước còn
lại giữ nguyên. Đánh đổi chính: GitHub sạch và nhẹ máy hơn, nhưng cần Docker Hub
token + deploy key trên GitHub và phụ thuộc Actions.

---

## 3. Các thành phần

| Thành phần | Trách nhiệm |
|---|---|
| `CI-CD/07_deploy_now.sh` | Orchestrator: đọc version, build/push (tuỳ chọn), SSH, gọi deploy |
| `CI-CD/00_check_vps.sh` | Preflight VPS: repo sạch, `git pull`, `.env`, compose, sudo, Docker |
| `CI-CD/01_setup_ssh.sh` | Tạo deploy key, pin host key, test SSH/sudo |
| `CI-CD/05_verify.sh` | Kiểm tra health công khai + trạng thái stack trên VPS |
| `CI-CD/06_rollback.sh` | Rollback về tag `previous`, khôi phục `.env` nếu lỗi |
| `CI-CD/_lib.sh` | Nạp config, validate đầu vào, wrapper SSH an toàn |
| `deploy/registry/build_and_push.sh` | Build/push backend + nginx (`linux/amd64`) |
| `deploy/registry/deploy_vps.sh` | Pull-only deploy, health gate, auto-rollback, prune |
| `.github/workflows/ci.yml` | CI tuỳ chọn: pytest + frontend build, không secrets |
| `.github/workflows/build.yml` | Build/push image và deploy VPS (tuỳ chọn) |

Vì sao tách `07` và `deploy_vps.sh`? `07` là phần **điều phối từ xa** (build,
copy config, SSH); `deploy_vps.sh` là phần **thực thi trên VPS**. Tách như vậy để
có thể chạy `deploy_vps.sh` thủ công khi cần, và để mọi đường deploy (local hay
CI) dùng chung một logic an toàn.

---

## 4. Tag, digest và tính bất biến

- **Tag** (`2.3.3`) là nhãn người đọc; có thể bị ghi đè.
- **Digest** (`sha256:...`) là hash nội dung image; không đổi nếu nội dung không đổi.

Production phải pin tag cụ thể, và tag đã phát hành **không được di chuyển**; nếu
cần sửa, phát hành version mới. `deploy_vps.sh` từ chối `latest` vì nó không cho
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

`07_deploy_now.sh` đọc chính file này để chọn tag. Nhờ vậy không còn cảnh
`main.py` ghi 2.3.2 còn health ghi 2.3.1; và khi release chỉ sửa một file.

`build_and_push.sh` tag cả `:X.Y.Z` và `:latest` (git tag có tiền tố `v`, image
tag không có `v`); VPS chỉ dùng tag cụ thể. `:latest` chỉ tiện cho thử nghiệm,
không phải căn cứ để deploy.

---

## 5. Build ở đâu và vì sao

`build_and_push.sh` build hai image với `--platform linux/amd64` để khớp e2-micro:

- `Dockerfile` backend: multi-stage, runtime slim, user không đặc quyền.
- `frontend/Dockerfile`: Node build SPA → Nginx runtime.

Có hai nơi build, dùng chung một script:

- **Máy local**: `07 --build`; cần Docker login.
- **GitHub runner**: workflow `build.yml` chạy tay; cần secret `DOCKERHUB_TOKEN`.
  Workflow kiểm tra tag khớp `APP_VERSION`, từ chối `latest` và từ chối ghi đè
  tag đã có trên Docker Hub. Nếu bật deploy, cần thêm VPS secrets và workflow
  tự SSH deploy; nếu tắt, local chạy `07_deploy_now.sh <tag>` để deploy image có sẵn.

Cả hai cho ra cùng loại image `linux/amd64`; khác nhau ở nơi thực thi và mức độ
tin cậy của môi trường build. GitHub runner sạch hơn máy cá nhân (checkout đúng
commit, không có file chưa commit), nhưng cần quản lý token và phụ thuộc Actions.

**Vì sao không build trên VPS?** e2-micro có 1 GB RAM. `pip install faiss-cpu`,
`npm ci`, `vite build` dễ OOM, phải bật swap và rất chậm. Build trên máy khỏe
(local hoặc GitHub runner):
- tận dụng CPU/RAM lớn, cache layer;
- VPS không cần toolchain → giảm bề mặt tấn công, giảm dung lượng;
- cùng một image chạy ở local test và VPS → dễ tái lập lỗi.

Đánh đổi: build local cần Docker, login registry và đủ dung lượng đĩa; build phụ
thuộc máy cá nhân. Build trên GitHub sạch và nhẹ máy hơn, nhưng cần Docker Hub
token, phụ thuộc Actions và tốn phút runner (repo private). Cả hai đều phải giữ
nguyên tắc: Dockerfile pin dependency, đúng `PLATFORM`, không build lại cùng tag
đã phát hành.

---

## 6. Deploy qua SSH: vì sao an toàn hơn vẻ ngoài

`_lib.sh` gọi SSH với:

```
ssh -i key -o IdentitiesOnly=yes -o BatchMode=yes \
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile=known_hosts ...
```

Các điểm cần hiểu:

**BatchMode=yes**: không cho ssh hỏi mật khẩu/passphrase. Nếu key sai, fail ngay
thay vì treo.

**StrictHostKeyChecking=yes + known_hosts**: chống MITM. Nếu ai đó chiếm DNS và
trả về server giả, host key khác → ssh từ chối. `ssh-keyscan` chỉ là bước thu thập;
việc **so fingerprint** mới là bước tin cậy. Vì vậy `01_setup_ssh.sh` in fingerprint
để bạn đối chiếu với `/etc/ssh/ssh_host_ed25519_key.pub` trên VPS.

**Deploy key riêng**: không dùng key cá nhân; thu hồi bằng cách xoá khỏi
`authorized_keys`. Quyền tối thiểu: chỉ user đó, chỉ SSH.

**sudo -n**: `-n` nghĩa là không hỏi mật khẩu. Nếu user chưa có NOPASSWD, script
fail sớm với thông báo rõ thay vì treo. Trên GCP, user tạo instance thường đã nằm
trong `google-sudoers` (NOPASSWD).

**GIT_TERMINAL_PROMPT=0**: `git pull` trên VPS không được hỏi username/PAT. Nếu
credential hết hạn, fail ngay.

Lệnh remote được truyền qua stdin (`bash -s`), không nhét chuỗi dài vào argv; tag
và đường dẫn đã được validate charset trước khi nội suy, tránh shell injection.

**Trường hợp chỉ có GCP browser SSH**: máy local không có private key nào, nên
`ssh user@host` từ Git Bash sẽ `Permission denied (publickey)`. `01_setup_ssh.sh`
giải quyết bằng cách tạo key trước, in sẵn lệnh thêm public key để dán vào terminal
browser, rồi test lại. Đây cũng là lý do script tạo `known_hosts` **trước** khi thử
cài key: dù bước cài tự động thất bại, bạn vẫn có host key đã pin để dùng tiếp.

**Local hay GitHub?** Cơ chế SSH y hệt. Deploy từ local giữ key trên máy bạn;
deploy từ GitHub đặt key trong secret của repo, nên dùng **key riêng** có thể thu
hồi độc lập. Workflow hiện không có approval mặc định; nếu cần audit, thêm
Environment `production` có Required reviewers. Dù ở đâu, token registry chỉ nên
có quyền Read/Write và key phải thu hồi được bất cứ lúc nào.

---

## 7. Bên trong `deploy_vps.sh`

Đây là nơi "data-safe" được thực thi:

1. **Preflight**: Docker/Compose v2, lock `flock` (chống 2 deploy chồng nhau),
   compose/.env tồn tại, chmod 600, tag hợp lệ (không `latest`), đĩa tối thiểu 512 MB.
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

## 8. Health check đi qua những đâu

```
Máy local / CI runner → https://<domain>/health
                │ TLS + DNS
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
  Đây là cổng chặn chính, quyết định rollback.
- `07` và `05` kiểm tra thêm **public URL** để bắt lỗi TLS/DNS/CORS mà loopback
  không thấy. Đây là hai lớp khác nhau, không thay thế nhau.
- HTTP 503 khi DB mất kết nối; script coi đó là fail.

---

## 9. Mô hình mối đe doạ và biện pháp

| Rủi ro | Hậu quả | Biện pháp trong EKP |
|---|---|---|
| Lộ Docker Hub token | Kẻ xấu push image độc | Token Read/Write tối thiểu; lưu trong credential store của Docker; thu hồi được |
| Lộ private key deploy | Chiếm SSH VPS | Key riêng, không commit; xoá khỏi `authorized_keys` khi nghi ngờ |
| Máy local bị chiếm | Kẻ xấu deploy image độc | Không lưu secret trong repo; token/key thu hồi được; health gate vẫn chạy |
| Secret GitHub bị lộ | Push image hoặc SSH deploy trái phép | Repo private; key deploy riêng; token Read/Write; thu hồi secret/key khi nghi ngờ |
| MITM SSH | Chạy lệnh trên server giả | `known_hosts` pin + fingerprint; `StrictHostKeyChecking=yes` |
| Tag rác/`latest` | Deploy nhầm bản | Validate tag, từ chối `latest`, đọc `APP_VERSION` |
| Tag cũ bị di chuyển | Artifact không tái lập | Quy ước không tái sử dụng tag; bump version |
| Deploy nhầm thời điểm | Bản lỗi lên production | Lệnh thủ công có chủ đích; health gate; auto-rollback |
| Hai deploy chồng nhau | Container trạng thái hỗn hợp | `flock` trong deploy script |
| Migration không tương thích | Rollback image chạy trên schema mới bị lỗi | Quy tắc expand/contract; không rollback DB tự động |
| Prune nhầm dữ liệu | Mất DB/FAISS | Script không bao giờ `down -v`; chỉ `rmi` image EKP |
| Secret trong log | Lộ token | Không echo; token nhập ở `docker login`, không ghi file |

Nguyên tắc: **fail closed**. Thiếu biến, thiếu key, sai host, tag lạ, health không
đạt → dừng, không cố chạy tiếp.

---

## 10. Luồng lỗi và cách đọc

| Tầng | Dấu hiệu | Xem ở đâu |
|---|---|---|
| GitHub build | Tag đã tồn tại, không khớp `APP_VERSION`, login Docker Hub fail | Log workflow `build.yml`; kiểm tra secret `DOCKERHUB_TOKEN` |
| GitHub deploy | Thiếu secret, publickey, host key, ConnectTimeout, sudo | Log job deploy; kiểm tra `VPS_*`, key/fingerprint, firewall port 22 |
| Build local | Docker lỗi, hết đĩa, sai arch | Output `build_and_push.sh`, `docker system df` |
| Push | `unauthorized`, `insufficient_scope` | `docker login`, quyền token |
| SSH | Timeout, publickey, host key, sudo | Output `07`, `00`, `01` |
| git pull | Credential/non-ff | Log remote, `GIT_TERMINAL_PROMPT=0 git pull` |
| compose up | Thiếu biến, port, arch | `compose ps`, log backend |
| health | 503/không healthy | Log backend, DB, migration |
| public health | TLS/DNS/CORS | `curl -v`, cert, DNS |

Nguyên tắc debug: **đọc log của tầng gần lỗi nhất**, đừng đoán. Nếu deploy fail,
VPS đã ở trạng thái rollback (hoặc trạng thái lỗi nếu rollback cũng fail) — kiểm tra
`docker ps` trước khi thao tác tiếp.

---

## 11. Các lựa chọn khác và trade-off

| Cách | Ưu | Nhược | Khi nào chọn |
|---|---|---|---|
| **GitHub build + deploy** (`build.yml`, đang có) | Một click; runner sạch; local không cần Docker/SSH; vẫn health gate/rollback | GitHub giữ deploy key + Docker token; cần cấu hình secrets một lần; phụ thuộc Actions/phút runner | Muốn tự động hoá nhưng vẫn kiểm soát bằng lệnh chạy tay |
| **Build + deploy từ máy local** | Không cần secrets trên cloud; dùng Docker login + SSH key sẵn có | Máy local phải đăng nhập registry; log không tập trung | Một người vận hành, demo nhanh, hoặc fallback khi GitHub lỗi |
| GitHub Actions đầy đủ (đã lược bỏ, còn trong git history) | Tag-driven, approval, audit, chạy trên runner khỏe | Cần secrets/environment/gh; thêm một hệ thống phải cấu hình và bảo trì | Có nhiều người deploy, cần audit |
| VPS tự poll registry (timer) | Không mở SSH, không cần runner deploy | Chậm, khó approve, khó biết ai deploy, cần script so digest | Mạng chặn inbound, nhiều VPS |
| Self-hosted runner trên VPS | Không mở SSH | Runner ngốn RAM/CPU trên máy 1 GB, bề mặt tấn công lớn | Không khuyến nghị ở đây |
| Kubernetes + GitOps | Chuẩn công nghiệp, tự reconcile | Nặng, vận hành phức tạp, không hợp e2-micro | Khi scale nhiều node |
| PaaS (Cloud Run...) | Ít vận hành | Khác ràng buộc đồ án (VPS/Postgres tự quản) | Khi bỏ ràng buộc |

Đường nhẹ không phải là "bỏ CI/CD" mà là **giữ trigger ở nơi bạn kiểm soát**:
local hoặc GitHub, nhưng phần CD quan trọng nhất vẫn là `deploy_vps.sh` (pull-only,
health gate, rollback, prune an toàn). Image có thể build ở local hoặc trên GitHub
runner (`build.yml`); nếu dùng `build.yml` thì GitHub giữ Docker token + deploy key,
còn local không cần Docker/SSH. Không có approval mặc định trong cả hai đường;
log nằm ở terminal, workflow và VPS. Khi cần audit hoặc nhiều người deploy, thêm
Environment có Required reviewers hoặc khôi phục pipeline tag-driven từ git history.

---

## 12. Mở rộng

- **Thêm approval/audit**: gắn Environment `production` có Required reviewers cho
  job deploy của `build.yml`; không cần khôi phục `release.yml`.
- **Tag-driven release**: nếu muốn push tag `v*` tự build/deploy, thêm trigger
  `push.tags` và guard version; hoặc khôi phục `release.yml` cũ từ git history.
- Thêm **staging environment**: cùng workflow, tag `v*-rc*`, secret trỏ VPS staging,
  deploy job thứ hai.
- **GHCR private**: đổi `DOCKER_IMAGE_PREFIX=ghcr.io/...`, cấp PAT `read:packages`
  cho VPS nếu private.
- **Image scanning/SBOM**: thêm Trivy/Grype sau build trước khi deploy.
- **Backup trước deploy**: `pg_dump` vào object storage trước migration lớn; hiện
  rollback chỉ ở tầng image.
- **Ký image (cosign)**: đảm bảo VPS chỉ chạy image do bạn tạo.
- **Tag ruleset**: nếu quay lại đường GitHub, chỉ maintainer được tạo tag `v*`.

---

## 13. Câu hỏi tự kiểm tra

1. **Vì sao VPS không build image?**
   e2-micro 1 GB RAM; build dễ OOM/chậm; image build trên máy khỏe đảm bảo tái lập,
   giảm toolchain và bề mặt tấn công trên VPS.

2. **Vì sao cần `known_hosts` thay vì `StrictHostKeyChecking=no`?**
   Không có pin, kẻ tấn công DNS có thể đưa SSH tới server giả và nhận key deploy.

3. **Tại sao không dùng `latest` và không build lại tag cũ?**
   `latest` không cho biết đang chạy bản nào; build lại tag cũ ghi đè digest đã
   phát hành, làm mất khả năng tái lập và rollback đúng artifact.

4. **Rollback có khôi phục dữ liệu PostgreSQL không?**
   Không. Chỉ image/container. Migration đã chạy vẫn ở đó; vì vậy migration phải
   tương thích ngược.

5. **Vì sao deploy kiểm tra loopback còn `05` kiểm tra public URL?**
   Loopback kiểm tra backend/DB ngay trên VPS, không phụ thuộc DNS/TLS; public URL
   bắt lỗi TLS, DNS, CORS mà loopback không thấy.

6. **Vì sao script prune không xoá volume?**
   Volume chứa dữ liệu thật; image có thể pull lại, dữ liệu thì không. Mọi thao
   tác xoá volume phải là hành động riêng, có backup.

7. **Đường nhẹ mất gì so với pipeline tag-driven đầy đủ?**
   Không mất CI/build — `ci.yml` test và `build.yml` build/deploy được. Cái mất là
   approval/audit mặc định và một phần kiểm soát local; đổi lại GitHub giữ deploy
   key và cần cấu hình secrets một lần. Health gate/rollback vẫn giữ nguyên.

8. **Vì sao dùng key riêng cho GitHub thay vì key local?**
   Thu hồi độc lập: nếu secret lộ, chỉ xoá public key đó khỏi VPS, không ảnh hưởng
   deploy từ máy local. Cùng một key cho cả hai nơi thì khó thu hồi riêng.

9. **Nếu mất tag `previous`, rollback thế nào?**
   Dùng tag cụ thể còn trên Docker Hub (nếu chưa overwrite) hoặc image ID còn trên
   VPS; nếu đã prune, pull lại tag cũ từ registry rồi deploy thủ công.
