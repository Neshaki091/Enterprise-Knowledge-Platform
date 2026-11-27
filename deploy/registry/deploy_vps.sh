#!/usr/bin/env bash
# ==============================================================================
# Deploy production stack trên VPS: pull image app từ registry, recreate container,
# kiểm tra health end-to-end, tự rollback khi lỗi, prune image cũ (KHÔNG đụng data).
#
#   sudo bash deploy/registry/deploy_vps.sh
#   sudo bash deploy/registry/deploy_vps.sh --no-pull --no-prune
#   sudo bash deploy/registry/deploy_vps.sh --no-rollback
#   sudo bash deploy/registry/deploy_vps.sh --keep 2
#
# Cờ:
#   --no-pull        Không pull registry, chỉ dùng image local.
#   --no-prune       Không xóa image cũ sau khi deploy thành công.
#   --no-rollback    Không tự rollback khi deploy/health check thất bại.
#   --keep N         Giữ N bản backup mỗi repo (0-5, mặc định 1).
#   -h, --help       In hướng dẫn này.
#
# Biến môi trường:
#   IMAGE_TAG               Override tag trong .env (không chấp nhận 'latest').
#   KEEP_BACKUPS            Mặc định 1 (giữ bản đang chạy + 1 backup/repo).
#   HEALTH_TIMEOUT_SECONDS  Mặc định 120 giây (migration/index có thể lâu).
#   COMPOSE_FILE, ENV_FILE  Override đường dẫn.
#
# An toàn dữ liệu & registry:
#   - Không bao giờ chạy `down -v`; volume PostgreSQL/FAISS, TLS và DuckDNS giữ nguyên.
#   - Chỉ pull/prune image backend + nginx; PostgreSQL không bị pull hay prune.
#   - Prune xóa image ekp-backend/ekp-nginx cũ, giữ bản đang chạy + KEEP_BACKUPS.
#   - Rollback image KHÔNG rollback migration CSDL (migration phải tương thích ngược).
#   - Tag 'previous' trỏ về image vừa bị thay thế để rollback thủ công nhanh.
# ==============================================================================
set -Eeuo pipefail

trap 'echo "[deploy] LỖI tại dòng ${LINENO}. Xem log phía trên." >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO_ROOT}/docker-compose.registry.yml}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"

PULL=1
PRUNE_OLD=1
ROLLBACK_ON_FAILURE=1
KEEP_BACKUPS="${KEEP_BACKUPS:-1}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-120}"

fail() { echo "[deploy] $*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-pull) PULL=0 ;;
        --no-prune) PRUNE_OLD=0 ;;
        --no-rollback) ROLLBACK_ON_FAILURE=0 ;;
        --keep)
            [ "$#" -ge 2 ] || fail "Thiếu giá trị cho --keep"
            KEEP_BACKUPS="$2"
            shift
            ;;
        --keep=*) KEEP_BACKUPS="${1#*=}" ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) fail "Tham số không hợp lệ: $1" ;;
    esac
    shift
done

case "$KEEP_BACKUPS" in
    ''|*[!0-9]*) fail "KEEP_BACKUPS phải là số nguyên 0-5" ;;
esac
[ "$KEEP_BACKUPS" -ge 0 ] && [ "$KEEP_BACKUPS" -le 5 ] || fail "KEEP_BACKUPS phải nằm trong 0-5"

case "$HEALTH_TIMEOUT_SECONDS" in
    ''|*[!0-9]*) fail "HEALTH_TIMEOUT_SECONDS phải là số nguyên" ;;
esac
[ "$HEALTH_TIMEOUT_SECONDS" -ge 30 ] && [ "$HEALTH_TIMEOUT_SECONDS" -le 600 ] \
    || fail "HEALTH_TIMEOUT_SECONDS phải nằm trong 30-600"

# ------------------------------------------------------------------ preflight
command -v docker >/dev/null 2>&1 || fail "Không tìm thấy docker"
docker info >/dev/null 2>&1 || fail "Không gọi được Docker daemon. Chạy bằng sudo hoặc thêm user vào group docker."
docker compose version >/dev/null 2>&1 || fail "Cần Docker Compose v2 (lệnh 'docker compose')"
command -v curl >/dev/null 2>&1 || fail "Không tìm thấy curl"
command -v awk >/dev/null 2>&1 || fail "Không tìm thấy awk"

if command -v flock >/dev/null 2>&1; then
    exec 9>/tmp/ekp-deploy.lock
    flock -n 9 || fail "Một tiến trình deploy khác đang chạy (lock /tmp/ekp-deploy.lock)"
fi

[ -f "$COMPOSE_FILE" ] || fail "Không tìm thấy $COMPOSE_FILE"
[ -f "$ENV_FILE" ] || fail "Không tìm thấy $ENV_FILE. Tạo từ .env.example rồi điền secret."

env_mode="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || true)"
case "$env_mode" in
    *00) ;;
    *) chmod 600 "$ENV_FILE" 2>/dev/null && echo "==> Đã chmod 600 $ENV_FILE (bảo vệ secret)" || true ;;
esac

cd "$REPO_ROOT"

read_env() {
    grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs 2>/dev/null || true
}

DOCKER_IMAGE_PREFIX="${DOCKER_IMAGE_PREFIX:-$(read_env DOCKER_IMAGE_PREFIX)}"
DOCKER_IMAGE_PREFIX="${DOCKER_IMAGE_PREFIX:-neshaki091}"
ENV_IMAGE_TAG="$(read_env IMAGE_TAG)"
TARGET_TAG="${IMAGE_TAG:-$ENV_IMAGE_TAG}"

[ -n "$TARGET_TAG" ] || fail "Thiếu IMAGE_TAG trong $ENV_FILE"
[ "$TARGET_TAG" != "latest" ] || fail "Production không dùng tag 'latest'. Pin IMAGE_TAG cụ thể (vd 2.3.2)."
case "$TARGET_TAG" in
    *[!A-Za-z0-9_.-]*) fail "IMAGE_TAG chứa ký tự không hợp lệ: $TARGET_TAG" ;;
esac

available_kb="$(df -Pk "$REPO_ROOT" | awk 'NR==2 {print $4}')"
if [ -n "$available_kb" ]; then
    if [ "$available_kb" -lt 524288 ]; then
        fail "Chỉ còn $((available_kb / 1024)) MB trống. Cần tối thiểu 512 MB để pull/recreate an toàn."
    elif [ "$available_kb" -lt 1048576 ]; then
        echo "==> CẢNH BÁO: chỉ còn $((available_kb / 1024)) MB trống; nên dọn bớt trước khi deploy."
    fi
fi

# ------------------------------------------------------- trạng thái hiện tại
# Compose đọc ${IMAGE_TAG}/${DOCKER_IMAGE_PREFIX}; export để giá trị shell
# thắng giá trị trong .env (compose ưu tiên biến môi trường của tiến trình).
export IMAGE_TAG="$TARGET_TAG"
export DOCKER_IMAGE_PREFIX

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

prev_backend_id="$(docker inspect --format '{{.Image}}' saas_fastapi_backend 2>/dev/null || true)"
prev_nginx_id="$(docker inspect --format '{{.Image}}' saas_nginx_proxy 2>/dev/null || true)"
prev_backend_ref="$(docker inspect --format '{{.Config.Image}}' saas_fastapi_backend 2>/dev/null || true)"

backend_ref="${DOCKER_IMAGE_PREFIX}/ekp-backend:${TARGET_TAG}"
nginx_ref="${DOCKER_IMAGE_PREFIX}/ekp-nginx:${TARGET_TAG}"

echo "==> Repo: $REPO_ROOT"
echo "==> Image đích:"
echo "      $backend_ref"
echo "      $nginx_ref"
if [ -n "$prev_backend_id" ]; then
    echo "==> Backend đang chạy: ${prev_backend_ref:-$prev_backend_id} (${prev_backend_id:0:19})"
else
    echo "==> Chưa có container backend đang chạy (deploy lần đầu, không có gì để rollback)"
fi

# Validate compose + biến .env trước khi pull để bắt lỗi cấu hình sớm.
config_err="$("${COMPOSE[@]}" config -q 2>&1)" || {
    printf '%s\n' "$config_err" >&2
    fail "docker-compose.registry.yml không hợp lệ hoặc thiếu biến bắt buộc trong $ENV_FILE"
}

# --------------------------------------------------------------------- pull
if [ "$PULL" -eq 1 ]; then
    echo "==> Pull backend + nginx (PostgreSQL giữ nguyên container đang chạy)"
    "${COMPOSE[@]}" pull backend nginx
else
    echo "==> Bỏ qua pull (--no-pull)"
fi

for ref in "$backend_ref" "$nginx_ref"; do
    docker image inspect "$ref" >/dev/null 2>&1 \
        || fail "Không có image $ref trên máy. Bỏ --no-pull hoặc kiểm tra IMAGE_TAG/đăng nhập registry."
done

# Chặn chạy nhầm kiến trúc: image amd64 chạy trên ARM sẽ lỗi hoặc chậm bất thường.
normalize_arch() {
    case "$1" in
        x86_64) echo amd64 ;;
        aarch64) echo arm64 ;;
        *) echo "$1" ;;
    esac
}
server_arch="$(normalize_arch "$(docker version --format '{{.Server.Arch}}' 2>/dev/null || true)")"
for ref in "$backend_ref" "$nginx_ref"; do
    img_arch="$(normalize_arch "$(docker image inspect --format '{{.Architecture}}' "$ref" 2>/dev/null || true)")"
    if [ -n "$server_arch" ] && [ -n "$img_arch" ] && [ "$img_arch" != "$server_arch" ]; then
        fail "Image $ref là $img_arch nhưng máy chủ là $server_arch. Build/pull đúng nền tảng."
    fi
done

# ------------------------------------------------------------- health check
health_body=""
ROLLBACK_TAG_USED=""

health_ok() {
    local body
    body="$(curl -fsS --max-time 5 http://127.0.0.1/health 2>/dev/null || true)"
    if printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"healthy"'; then
        health_body="$body"
        return 0
    fi
    return 1
}

wait_health() {
    local timeout="$1" deadline
    deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -le "$deadline" ]; do
        if health_ok; then
            return 0
        fi
        sleep 3
    done
    return 1
}

show_failure_context() {
    echo "---- docker compose ps ----" >&2
    "${COMPOSE[@]}" ps >&2 || true
    echo "---- backend logs (80 dòng cuối) ----" >&2
    "${COMPOSE[@]}" logs --tail=80 backend >&2 || true
    echo "---- nginx logs (40 dòng cuối) ----" >&2
    "${COMPOSE[@]}" logs --tail=40 nginx >&2 || true
}

rollback_previous() {
    local rollback_tag
    if [ "$ROLLBACK_ON_FAILURE" -eq 0 ]; then
        echo "==> Không tự rollback (--no-rollback)." >&2
        return 1
    fi
    if [ "$TARGET_TAG" = "previous" ]; then
        echo "==> Đang rollback thủ công về 'previous'; không tự rollback ngược lại bản lỗi." >&2
        return 1
    fi
    if [ -z "$prev_backend_id" ] || [ -z "$prev_nginx_id" ]; then
        echo "==> Không có image trước đó để rollback (deploy lần đầu)." >&2
        return 1
    fi
    rollback_tag="rollback-$(date +%Y%m%d-%H%M%S)"
    echo "==> TỰ ROLLBACK về image trước đó (tag $rollback_tag)" >&2
    docker tag "$prev_backend_id" "${DOCKER_IMAGE_PREFIX}/ekp-backend:${rollback_tag}"
    docker tag "$prev_nginx_id" "${DOCKER_IMAGE_PREFIX}/ekp-nginx:${rollback_tag}"
    if IMAGE_TAG="$rollback_tag" "${COMPOSE[@]}" up -d --remove-orphans --no-build \
        && wait_health 90; then
        ROLLBACK_TAG_USED="$rollback_tag"
        echo "==> Rollback THÀNH CÔNG về $rollback_tag." >&2
        return 0
    fi
    echo "==> Rollback THẤT BẠI. Cần xử lý thủ công ngay." >&2
    return 1
}

fail_deploy() {
    echo "==> $1" >&2
    show_failure_context
    rollback_previous || true
    if [ -n "$ROLLBACK_TAG_USED" ]; then
        echo "==> Container đang chạy $ROLLBACK_TAG_USED nhưng .env vẫn ghi IMAGE_TAG=${ENV_IMAGE_TAG:-$TARGET_TAG}." >&2
        echo "    Sửa IMAGE_TAG trong .env trước khi thao tác 'docker compose' thủ công." >&2
    fi
    echo "==> LƯU Ý: migration CSDL (nếu có) KHÔNG được rollback; image cũ phải tương thích ngược." >&2
    exit 1
}

# ---------------------------------------------------------------- deploy up
echo "==> Recreate stack backend + nginx (PostgreSQL và volume giữ nguyên)"
if ! "${COMPOSE[@]}" up -d --remove-orphans --no-build; then
    fail_deploy "docker compose up thất bại"
fi

echo "==> Chờ health end-to-end qua Nginx (tối đa ${HEALTH_TIMEOUT_SECONDS}s)"
if ! wait_health "$HEALTH_TIMEOUT_SECONDS"; then
    fail_deploy "HEALTH CHECK THẤT BẠI sau ${HEALTH_TIMEOUT_SECONDS}s"
fi
echo "==> HEALTH OK: $health_body"

# ------------------------------------------------- bản backup + prune image
active_backend_id="$(docker inspect --format '{{.Image}}' saas_fastapi_backend 2>/dev/null || true)"
active_nginx_id="$(docker inspect --format '{{.Image}}' saas_nginx_proxy 2>/dev/null || true)"

# Tag 'previous' = image vừa bị thay thế, phục vụ rollback thủ công.
# Không ghi đè khi chính lần chạy này đang deploy tag 'previous'.
if [ "$KEEP_BACKUPS" -ge 1 ] && [ "$TARGET_TAG" != "previous" ]; then
    [ -n "$prev_backend_id" ] && docker tag "$prev_backend_id" "${DOCKER_IMAGE_PREFIX}/ekp-backend:previous" || true
    [ -n "$prev_nginx_id" ] && docker tag "$prev_nginx_id" "${DOCKER_IMAGE_PREFIX}/ekp-nginx:previous" || true
fi

prune_repo() {
    local repo="$1" active_id="$2" previous_id image_id created kept
    previous_id=""
    if [ "$KEEP_BACKUPS" -ge 1 ]; then
        previous_id="$(docker image inspect --format '{{.ID}}' "${repo}:previous" 2>/dev/null || true)"
    fi
    kept=0
    # Mới nhất trước; mỗi image chỉ xét một lần dù có nhiều tag.
    while IFS='|' read -r created image_id; do
        [ -n "$image_id" ] || continue
        if [ "$image_id" = "$active_id" ] || [ "$image_id" = "$previous_id" ]; then
            continue
        fi
        kept=$((kept + 1))
        if [ "$kept" -le "$KEEP_BACKUPS" ]; then
            echo "    giữ backup: ${image_id:0:19} (${created%% *})"
            continue
        fi
        echo "    xóa image cũ: ${image_id:0:19}"
        docker rmi "$image_id" >/dev/null 2>&1 || echo "    (bỏ qua: image vẫn đang được dùng)"
    done < <(
        docker images --no-trunc --format '{{.CreatedAt}}|{{.ID}}' "$repo" \
            | sort -r \
            | awk -F'|' '!seen[$2]++'
    )
}

if [ "$PRUNE_OLD" -eq 1 ]; then
    echo "==> Dọn image cũ (giữ bản đang chạy + tối đa $KEEP_BACKUPS backup/repo; KHÔNG xóa volume)"
    prune_repo "${DOCKER_IMAGE_PREFIX}/ekp-backend" "$active_backend_id"
    prune_repo "${DOCKER_IMAGE_PREFIX}/ekp-nginx" "$active_nginx_id"
    docker image prune -f >/dev/null 2>&1 || true
else
    echo "==> Bỏ qua prune (--no-prune)"
fi

# --------------------------------------------------------------- tổng kết
if [ -n "$ENV_IMAGE_TAG" ] && [ "$ENV_IMAGE_TAG" != "$TARGET_TAG" ]; then
    echo "==> LƯU Ý: .env đang ghi IMAGE_TAG=$ENV_IMAGE_TAG nhưng lần này deploy $TARGET_TAG."
    echo "    Cập nhật IMAGE_TAG trong .env để 'docker compose up' thủ công không quay lui phiên bản."
fi

echo
echo "================= DEPLOY THÀNH CÔNG: $TARGET_TAG ================="
"${COMPOSE[@]}" ps
echo
echo "Image đang có:"
docker images --format '  {{.Repository}}:{{.Tag}}  {{.ID}}  {{.Size}}' \
    "${DOCKER_IMAGE_PREFIX}/ekp-backend" "${DOCKER_IMAGE_PREFIX}/ekp-nginx" || true
echo
echo "Dung lượng đĩa còn trống: $(df -Ph "$REPO_ROOT" | awk 'NR==2 {print $4}')"
echo
if [ "$TARGET_TAG" != "previous" ] \
    && docker image inspect "${DOCKER_IMAGE_PREFIX}/ekp-backend:previous" >/dev/null 2>&1; then
    echo "Rollback thủ công về bản ngay trước:"
    echo "  sudo IMAGE_TAG=previous bash deploy/registry/deploy_vps.sh --no-pull --no-prune"
    echo
fi
echo "Lưu ý: rollback image không rollback migration CSDL."
echo "=================================================================="
