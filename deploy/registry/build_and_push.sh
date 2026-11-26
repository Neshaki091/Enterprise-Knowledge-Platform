#!/usr/bin/env bash
# ==============================================================================
# Build image backend + nginx trên máy LOCAL và push lên container registry.
#
#   bash deploy/registry/build_and_push.sh
#   IMAGE_TAG=2.1.0 bash deploy/registry/build_and_push.sh
#   DOCKER_IMAGE_PREFIX=ghcr.io/neshaki091 bash deploy/registry/build_and_push.sh
#
# Cờ:
#   --no-push   Chỉ build + tag để kiểm thử local, không push.
#   --login     Chạy `docker login` trước khi push (mặc định dùng phiên đăng nhập sẵn có).
#
# Biến môi trường:
#   DOCKER_IMAGE_PREFIX  namespace registry (mặc định: neshaki091)
#   IMAGE_TAG            tag phiên bản (mặc định: git short SHA)
#   PLATFORM             nền tảng image (mặc định: linux/amd64 cho GCP e2-micro)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DOCKER_IMAGE_PREFIX="${DOCKER_IMAGE_PREFIX:-neshaki091}"
if [ -z "${IMAGE_TAG:-}" ]; then
    IMAGE_TAG="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d-%H%M)"
fi
PLATFORM="${PLATFORM:-linux/amd64}"

PUSH=1
LOGIN=0
for arg in "$@"; do
    case "$arg" in
        --no-push) PUSH=0 ;;
        --login) LOGIN=1 ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) echo "Tham số không hợp lệ: $arg" >&2; exit 2 ;;
    esac
done

BACKEND_IMAGE="${DOCKER_IMAGE_PREFIX}/ekp-backend"
NGINX_IMAGE="${DOCKER_IMAGE_PREFIX}/ekp-nginx"

if ! docker info >/dev/null 2>&1; then
    echo "Docker daemon chưa chạy." >&2
    exit 1
fi

if [ "$LOGIN" -eq 1 ]; then
    docker login
fi

echo "==> Build backend : ${BACKEND_IMAGE}:${IMAGE_TAG} (${PLATFORM})"
docker build \
    --platform "$PLATFORM" \
    -f "${REPO_ROOT}/Dockerfile" \
    -t "${BACKEND_IMAGE}:${IMAGE_TAG}" \
    -t "${BACKEND_IMAGE}:latest" \
    "$REPO_ROOT"

echo "==> Build nginx   : ${NGINX_IMAGE}:${IMAGE_TAG} (${PLATFORM})"
docker build \
    --platform "$PLATFORM" \
    -f "${REPO_ROOT}/frontend/Dockerfile" \
    -t "${NGINX_IMAGE}:${IMAGE_TAG}" \
    -t "${NGINX_IMAGE}:latest" \
    "$REPO_ROOT"

docker image ls --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}' "${BACKEND_IMAGE}"
docker image ls --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}' "${NGINX_IMAGE}"

if [ "$PUSH" -eq 0 ]; then
    echo "==> Bỏ qua push (--no-push)."
    exit 0
fi

echo "==> Push images"
docker push "${BACKEND_IMAGE}:${IMAGE_TAG}"
docker push "${BACKEND_IMAGE}:latest"
docker push "${NGINX_IMAGE}:${IMAGE_TAG}"
docker push "${NGINX_IMAGE}:latest"

echo
echo "Đã push xong. Trên VPS đặt trong .env:"
echo "  DOCKER_IMAGE_PREFIX=${DOCKER_IMAGE_PREFIX}"
echo "  IMAGE_TAG=${IMAGE_TAG}"
echo "rồi chạy: bash deploy/registry/deploy_vps.sh"
