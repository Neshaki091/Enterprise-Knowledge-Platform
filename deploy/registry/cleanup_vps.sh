#!/usr/bin/env bash
# ==============================================================================
# Dọn sạch dữ liệu Docker còn sót trên VPS trước khi deploy lại từ registry.
#
#   sudo bash deploy/registry/cleanup_vps.sh            # chỉ dọn stack EKP cũ
#   sudo bash deploy/registry/cleanup_vps.sh --all      # dọn TOÀN BỘ Docker trên VPS
#   sudo bash deploy/registry/cleanup_vps.sh --yes      # không hỏi xác nhận
#   sudo bash deploy/registry/cleanup_vps.sh --repo-dir /opt/SaaS_Chatbot_platform
#
# CẢNH BÁO: xóa container + volume = xóa toàn bộ dữ liệu PostgreSQL/FAISS.
# Nếu còn dữ liệu cần giữ, chạy pg_dump và copy FAISS index TRƯỚC khi chạy script.
# `/etc/letsencrypt` và `/etc/duckdns` là dữ liệu trên host, script không xóa.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_DIR="${REPO_DIR:-$DEFAULT_REPO_DIR}"

DO_ALL=0
ASSUME_YES=0

while [ $# -gt 0 ]; do
    case "$1" in
        --all) DO_ALL=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        --repo-dir)
            shift
            REPO_DIR="${1:-}"
            [ -n "$REPO_DIR" ] || { echo "Thiếu giá trị cho --repo-dir" >&2; exit 2; }
            ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "Tham số không hợp lệ: $1" >&2; exit 2 ;;
    esac
    shift
done

if ! docker info >/dev/null 2>&1; then
    echo "Không gọi được Docker daemon. Chạy bằng sudo hoặc thêm user vào group docker." >&2
    exit 1
fi

echo "Repo: $REPO_DIR"
echo "Phạm vi: $([ "$DO_ALL" -eq 1 ] && echo 'TOÀN BỘ Docker trên máy' || echo 'stack EKP (container/volume/network saas_* và ekp_*)')"
echo
echo "CẢNH BÁO: thao tác này xóa container và volume, bao gồm dữ liệu PostgreSQL/FAISS."
if [ "$DO_ALL" -eq 1 ]; then
    echo "Chế độ --all sẽ xóa MỌI image/container/volume không dùng, kể cả project khác trên VPS."
fi

if [ "$ASSUME_YES" -ne 1 ]; then
    printf 'Gõ YES để tiếp tục: '
    read -r answer
    if [ "$answer" != "YES" ]; then
        echo "Đã hủy."
        exit 0
    fi
fi

# ------------------------------------------------------------------------------
# 1. Hạ stack cũ bằng compose (nếu còn file compose + .env trên VPS).
# ------------------------------------------------------------------------------
cd "$REPO_DIR" 2>/dev/null || true
ENV_ARG=()
[ -f "$REPO_DIR/.env" ] && ENV_ARG=(--env-file "$REPO_DIR/.env")

if [ -f "$REPO_DIR/docker-compose.yml" ]; then
    echo "==> down stack source-build cũ (nếu có)"
    docker compose "${ENV_ARG[@]}" -f "$REPO_DIR/docker-compose.yml" -f "$REPO_DIR/docker-compose.prod.yml" \
        down --rmi all --volumes --remove-orphans || true
fi

if [ -f "$REPO_DIR/docker-compose.registry.yml" ]; then
    echo "==> down stack registry cũ (nếu có)"
    docker compose "${ENV_ARG[@]}" -f "$REPO_DIR/docker-compose.registry.yml" \
        down --rmi all --volumes --remove-orphans || true
fi

# ------------------------------------------------------------------------------
# 2. Dọn tàn dư theo tên (kể cả container được tạo tay, ngoài compose).
# ------------------------------------------------------------------------------
echo "==> Xóa container saas_*/ekp_*"
container_ids="$( { docker ps -aq --filter 'name=saas_'; docker ps -aq --filter 'name=ekp_'; } | sort -u || true )"
if [ -n "$container_ids" ]; then
    # shellcheck disable=SC2086
    docker rm -f $container_ids
else
    echo "    (không có)"
fi

echo "==> Xóa volume saas_chatbot_platform_*/ekp_*"
volume_names="$(docker volume ls -q | grep -E '^(saas_chatbot_platform_|ekp_)' || true)"
if [ -n "$volume_names" ]; then
    # shellcheck disable=SC2086
    docker volume rm $volume_names || true
else
    echo "    (không có)"
fi

echo "==> Xóa network saas_chatbot_platform_*/ekp_*"
network_names="$(docker network ls --format '{{.Name}}' | grep -E '^(saas_chatbot_platform_|ekp_)' || true)"
if [ -n "$network_names" ]; then
    # shellcheck disable=SC2086
    docker network rm $network_names || true
else
    echo "    (không có)"
fi

echo "==> Xóa image build cũ saas_chatbot_platform-*"
old_images="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^saas_chatbot_platform-' || true)"
if [ -n "$old_images" ]; then
    # shellcheck disable=SC2086
    docker image rm $old_images || true
else
    echo "    (không có)"
fi

# ------------------------------------------------------------------------------
# 3. Tùy chọn: prune toàn bộ (chỉ dùng khi VPS dành riêng cho EKP).
# ------------------------------------------------------------------------------
if [ "$DO_ALL" -eq 1 ]; then
    echo "==> Prune TOÀN BỘ image/container/network/volume không dùng"
    docker system prune -a -f --volumes
    docker builder prune -a -f
fi

echo
echo "==> Trạng thái sau dọn dẹp"
docker system df
echo
docker ps -a --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
echo
docker volume ls
