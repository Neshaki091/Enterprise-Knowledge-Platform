#!/usr/bin/env bash
# ==============================================================================
# 07 — Deploy trực tiếp từ máy local (đường nhẹ, không cần GitHub secrets).
#
# Dùng:
#   bash CI-CD/07_deploy_now.sh                 # deploy APP_VERSION hiện tại
#   bash CI-CD/07_deploy_now.sh 2.3.4           # deploy tag cụ thể đã có trên registry
#   bash CI-CD/07_deploy_now.sh --build         # build/push local rồi deploy APP_VERSION
#   bash CI-CD/07_deploy_now.sh --build 2.3.4   # build/push tag cụ thể rồi deploy
#   bash CI-CD/07_deploy_now.sh --dry-run       # chỉ in việc sẽ làm
#
# Không dùng GitHub Actions/Environment/approval. Vẫn có health gate + auto-rollback
# của deploy_vps.sh. Image phải có trên Docker Hub (dùng --build nếu chưa).
# ==============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

require_vps_config

BUILD=0
DRY_RUN=0
TAG_ARG=""
for arg in "$@"; do
    case "$arg" in
        --build) BUILD=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        --*) die "Tham số không hợp lệ: $arg" ;;
        *)
            [ -z "$TAG_ARG" ] || die "Chỉ nhận một tag"
            TAG_ARG="$arg"
            ;;
    esac
done

if [ -n "$TAG_ARG" ]; then
    TAG="$TAG_ARG"
else
    TAG="$(sed -n 's/^APP_VERSION = "\([^"]*\)"[[:space:]]*$/\1/p' \
        "$REPO_ROOT/backend/app/core/version.py" | tail -n 1)"
fi
[ -n "$TAG" ] || die "Không đọc được APP_VERSION và không có tag truyền vào"
[ "$TAG" != "latest" ] || die "Không dùng tag 'latest'"
case "$TAG" in
    *[!A-Za-z0-9_.-]*) die "Tag chứa ký tự không hợp lệ: $TAG" ;;
esac

say "Deploy tag: $TAG"
IMAGE_PREFIX="${DOCKERHUB_USERNAME:-neshaki091}"
[ -n "$IMAGE_PREFIX" ] || die "DOCKERHUB_USERNAME rỗng trong $CONFIG_FILE"
say "Registry namespace: $IMAGE_PREFIX"

if [ "$BUILD" -eq 1 ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "DRY-RUN: DOCKER_IMAGE_PREFIX=$IMAGE_PREFIX IMAGE_TAG=$TAG bash deploy/registry/build_and_push.sh"
    else
        command -v docker >/dev/null 2>&1 || die "Chưa có docker trong PATH"
        docker info >/dev/null 2>&1 || die "Docker daemon chưa chạy"
        say "Build + push image $TAG từ local (cần đang đăng nhập Docker Hub)"
        (cd "$REPO_ROOT" && DOCKER_IMAGE_PREFIX="$IMAGE_PREFIX" IMAGE_TAG="$TAG" \
            bash deploy/registry/build_and_push.sh)
    fi
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY-RUN: ssh ${VPS_USER}@${VPS_HOST} → git pull → IMAGE_TAG=$TAG (prefix $IMAGE_PREFIX) → deploy_vps.sh"
    exit 0
fi

say "Cập nhật VPS ${VPS_USER}@${VPS_HOST}:${VPS_APP_DIR}"
vps_ssh "VPS_APP_DIR='${VPS_APP_DIR}' IMAGE_TAG='${TAG}' IMAGE_PREFIX='${IMAGE_PREFIX}' bash -s" <<'REMOTE'
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
cd "$VPS_APP_DIR"

sudo -n true || { echo "sudo cần mật khẩu trên VPS" >&2; exit 1; }
git pull --ff-only
test -f .env || { echo "Thiếu .env trên VPS" >&2; exit 1; }

ENV_PREFIX="$(grep -E '^DOCKER_IMAGE_PREFIX=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
if [ -n "$ENV_PREFIX" ] && [ "$ENV_PREFIX" != "$IMAGE_PREFIX" ]; then
    echo "Lệch registry: máy local build '$IMAGE_PREFIX' nhưng .env trên VPS là '$ENV_PREFIX'." >&2
    echo "Sửa DOCKER_IMAGE_PREFIX trong .env trên VPS cho khớp rồi chạy lại." >&2
    exit 1
fi

OLD_TAG="$(sudo grep -E '^IMAGE_TAG=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
on_exit() {
    rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ] && [ -n "$OLD_TAG" ]; then
        sudo sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=${OLD_TAG}/" .env || true
        echo "Deploy lỗi: đã khôi phục IMAGE_TAG=${OLD_TAG} trong .env" >&2
    fi
    exit "$rc"
}
trap on_exit EXIT

if sudo grep -q '^IMAGE_TAG=' .env; then
    sudo sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=${IMAGE_TAG}/" .env
else
    printf 'IMAGE_TAG=%s\n' "$IMAGE_TAG" | sudo tee -a .env >/dev/null
fi
sudo chmod 600 .env

sudo bash deploy/registry/deploy_vps.sh
echo "IMAGE_TAG sau deploy: $(grep '^IMAGE_TAG=' .env || echo '?')"
REMOTE

if [ -n "${PUBLIC_HEALTH_URL:-}" ] && ! is_placeholder "$PUBLIC_HEALTH_URL"; then
    say "Kiểm tra public health"
    body="$(curl -fsS --max-time 10 "$PUBLIC_HEALTH_URL")" \
        || die "Deploy xong nhưng không gọi được $PUBLIC_HEALTH_URL"
    printf '%s\n' "$body"
    printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"healthy"' \
        || die "Public health không healthy"
fi

say "Hoàn tất deploy $TAG."
