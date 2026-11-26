#!/usr/bin/env bash
# ==============================================================================
# Trên VPS: pull image từ registry và chạy stack production. KHÔNG build.
#
#   cp .env.example .env      # lần đầu, rồi điền secret + DOCKER_IMAGE_PREFIX/IMAGE_TAG
#   sudo bash deploy/registry/deploy_vps.sh
#
# Cờ:
#   --no-pull   Không pull, chỉ restart stack với image đang có.
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
for arg in "$@"; do
    case "$arg" in
        --no-pull) PULL=0 ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
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

echo "==> Image sẽ chạy:"
"${COMPOSE[@]}" config --images

if [ "$PULL" -eq 1 ]; then
    echo "==> Pull image từ registry"
    if ! "${COMPOSE[@]}" pull; then
        echo "Pull thất bại. Nếu image private, chạy: docker login <registry>" >&2
        exit 1
    fi
fi

echo "==> Khởi động stack"
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
    echo "Health check chưa đạt sau 60 giây." >&2
    echo "Xem log: ${COMPOSE[*]} logs --tail=100 backend nginx postgres" >&2
    exit 1
fi

echo
echo "Deploy xong. Kiểm tra tiếp:"
echo "  curl -fsS http://127.0.0.1/health"
echo "  sudo bash deploy/tls/issue_tls.sh <ten>.duckdns.org <email>   # nếu chưa có TLS"
echo "  curl -fsS https://<ten>.duckdns.org/health"
