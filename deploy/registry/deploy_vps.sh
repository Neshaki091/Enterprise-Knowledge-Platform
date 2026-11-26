#!/usr/bin/env bash
# ==============================================================================
# Trên VPS: pull artifact image mới từ registry và chạy stack production.
# KHÔNG build trên VPS, KHÔNG xóa volume dữ liệu (PostgreSQL / FAISS).
# Sau khi /health đạt healthy, giữ lại image đang chạy + đúng 1 bản artifact
# gần nhất để rollback và xóa toàn bộ bản cũ hơn để tiết kiệm ổ đĩa.
#
#   cp .env.example .env      # lần đầu, rồi điền secret + DOCKER_IMAGE_PREFIX/IMAGE_TAG
#   sudo bash deploy/registry/deploy_vps.sh
#
# Cờ:
#   --no-pull   Không pull, chỉ restart stack với image đang có.
#   --no-prune  Không xóa các bản artifact cũ hơn sau khi deploy.
#
# Yêu cầu: repo đã clone trên VPS (để lấy nginx/prod, nginx/runtime,
# certbot/www, scripts/init_db_app_role.sh). Không cần source code để build.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO_ROOT}/docker-compose.registry.yml}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"

PULL=1
PRUNE_OLD=1
for arg in "$@"; do
    case "$arg" in
        --no-pull) PULL=0 ;;
        --no-prune) PRUNE_OLD=0 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "Tham số không hợp lệ: $arg" >&2; exit 2 ;;
    esac
done

if ! docker info >/dev/null 2>&1; then
    echo "Không gọi được Docker daemon. Chạy bằng sudo hoặc thêm user vào group docker." >&2
    exit 1
fi

if [ ! -f "$COMPOSE_FILE" ]; then
    echo "Không tìm thấy $COMPOSE_FILE" >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "Không tìm thấy $ENV_FILE. Tạo từ .env.example rồi điền secret." >&2
    exit 1
fi

cd "$REPO_ROOT"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

# Fail sớm nếu thiếu biến bắt buộc hoặc cú pháp compose sai.
"${COMPOSE[@]}" config -q

# Đọc DOCKER_IMAGE_PREFIX từ ENV_FILE mà không export toàn bộ secret ra ngoài.
DOCKER_IMAGE_PREFIX="$(grep -E '^DOCKER_IMAGE_PREFIX=' "$ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs || true)"
DOCKER_IMAGE_PREFIX="${DOCKER_IMAGE_PREFIX:-neshaki091}"

# Ghi nhận image ID cũ đang chạy trước khi pull (phục vụ giữ bản gần nhất để rollback).
prev_backend_id="$(docker inspect --format '{{.Image}}' saas_fastapi_backend 2>/dev/null || true)"
prev_nginx_id="$(docker inspect --format '{{.Image}}' saas_nginx_proxy 2>/dev/null || true)"

echo "==> Image mục tiêu sẽ chạy (giữ nguyên volume PostgreSQL & FAISS):"
"${COMPOSE[@]}" config --images

if [ "$PULL" -eq 1 ]; then
    echo "==> Pull artifact image mới từ registry (chỉ kéo image, không build)"
    if ! "${COMPOSE[@]}" pull; then
        echo "Pull thất bại. Nếu image private, chạy: docker login <registry>" >&2
        exit 1
    fi
fi

echo "==> Khởi động lại container giữ nguyên toàn bộ volume dữ liệu (không dùng down -v)"
"${COMPOSE[@]}" up -d --remove-orphans

echo "==> Chờ /health"
healthy=0
for _ in $(seq 1 30); do
    body="$(curl -fsS --max-time 3 http://127.0.0.1/health 2>/dev/null || true)"
    if printf '%s' "$body" | grep -q '"status":"healthy"'; then
        echo "$body"
        healthy=1
        break
    fi
    sleep 2
done

"${COMPOSE[@]}" ps

if [ "$healthy" -ne 1 ]; then
    echo "Health check chưa đạt sau 60 giây. Giữ nguyên artifact cũ để rollback." >&2
    echo "Xem log: ${COMPOSE[*]} logs --tail=100 backend nginx postgres" >&2
    exit 1
fi

prune_repo_keep_latest_backup() {
    local repo="$1"
    local active_id="$2"
    local preferred_prev_id="$3"

    # Danh sách ID duy nhất sắp xếp từ mới nhất -> cũ nhất theo thời điểm tạo image.
    mapfile -t ordered_ids < <(docker images "$repo" --no-trunc --format '{{.ID}}' | awk '!seen[$0]++')
    if [ "${#ordered_ids[@]}" -le 2 ]; then
        return 0
    fi

    # Luôn giữ image đang chạy + tối đa 1 bản artifact khác gần nhất để rollback.
    local backup_id=""
    if [ -n "$preferred_prev_id" ] && [ "$preferred_prev_id" != "$active_id" ]; then
        backup_id="$preferred_prev_id"
    else
        for img_id in "${ordered_ids[@]}"; do
            if [ "$img_id" != "$active_id" ]; then
                backup_id="$img_id"
                break
            fi
        done
    fi

    for img_id in "${ordered_ids[@]}"; do
        if [ "$img_id" = "$active_id" ] || [ "$img_id" = "$backup_id" ]; then
            continue
        fi
        # Xóa toàn bộ tag cũ trỏ tới image ID cũ hơn (bỏ qua nếu vẫn đang được dùng).
        mapfile -t old_refs < <(docker images "$repo" --no-trunc --format '{{.ID}} {{.Repository}}:{{.Tag}}' | awk -v id="$img_id" '$1 == id {print $2}')
        if [ "${#old_refs[@]}" -gt 0 ]; then
            echo "    Xóa artifact cũ: ${old_refs[*]}"
            docker rmi -f "${old_refs[@]}" >/dev/null 2>&1 || true
        else
            docker rmi -f "$img_id" >/dev/null 2>&1 || true
        fi
    done
}

if [ "$PRUNE_OLD" -eq 1 ]; then
    echo "==> Giữ bản đang chạy + 1 bản artifact gần nhất, xóa các bản cũ hơn"
    active_backend_id="$(docker inspect --format '{{.Image}}' saas_fastapi_backend 2>/dev/null || true)"
    active_nginx_id="$(docker inspect --format '{{.Image}}' saas_nginx_proxy 2>/dev/null || true)"

    [ -n "$active_backend_id" ] && prune_repo_keep_latest_backup "${DOCKER_IMAGE_PREFIX}/ekp-backend" "$active_backend_id" "$prev_backend_id"
    [ -n "$active_nginx_id" ] && prune_repo_keep_latest_backup "${DOCKER_IMAGE_PREFIX}/ekp-nginx" "$active_nginx_id" "$prev_nginx_id"

    # Chỉ dọn layer trung gian dangling (<none>), tuyệt đối không đụng volume.
    docker image prune -f >/dev/null 2>&1 || true
fi

echo "==> Dung lượng image & volume còn lại:"
docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}' | grep -E 'REPOSITORY|ekp-backend|ekp-nginx|postgres' || true
echo
echo "Deploy xong (dữ liệu PostgreSQL & FAISS giữ nguyên). Kiểm tra tiếp:"
echo "  curl -fsS http://127.0.0.1/health"
echo "  sudo bash deploy/tls/issue_tls.sh <ten>.duckdns.org <email>   # nếu chưa có TLS"
echo "  curl -fsS https://<ten>.duckdns.org/health"
