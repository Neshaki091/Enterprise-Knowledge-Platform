#!/usr/bin/env bash
# ==============================================================================
# One-shot deploy trên VPS: pull image → chạy stack → health → CORS → TLS.
#
#   sudo bash deploy/registry/bootstrap_vps.sh \
#     --domain ekp-demo.duckdns.org \
#     --email ban@example.com \
#     --vercel-origin https://enterprise-knowledge-platform-xxxx.vercel.app
#
# Cờ:
#   --domain <host>        Host backend (DuckDNS hoặc domain riêng) để cấp TLS.
#   --email <email>        Email Let's Encrypt (bắt buộc nếu muốn cấp TLS).
#   --vercel-origin <url>  Origin Vercel thật; script ghi vào .env và restart backend.
#   --skip-duckdns         Không cài/không chờ DuckDNS.
#   --skip-firewall        Không chạy UFW.
#   --skip-tls             Không cấp TLS (chỉ deploy HTTP).
#   --no-pull              Dùng image đang có, không pull.
#   -h, --help             Hiện trợ giúp.
#
# Script idempotent: chạy lại không xóa volume/dữ liệu; chỉ recreate container.
# Yêu cầu: repo đã clone, .env đã tạo từ .env_vps, chạy bằng sudo/root.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${REPO_ROOT}/docker-compose.registry.yml}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"
DUCKDNS_ENV="/etc/duckdns/duckdns.env"

DOMAIN="${TLS_DOMAIN:-}"
EMAIL="${TLS_EMAIL:-}"
VERCEL_ORIGIN=""
SKIP_DUCKDNS=0
SKIP_FIREWALL=0
SKIP_TLS=0
PULL_ARGS=()

usage() {
    sed -n '2,22p' "$0"
}

die() {
    echo "LỖI: $*" >&2
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --domain)
            [ $# -ge 2 ] && [ -n "$2" ] || die "Thiếu giá trị cho --domain"
            DOMAIN="$2"; shift 2 ;;
        --email)
            [ $# -ge 2 ] && [ -n "$2" ] || die "Thiếu giá trị cho --email"
            EMAIL="$2"; shift 2 ;;
        --vercel-origin)
            [ $# -ge 2 ] && [ -n "$2" ] || die "Thiếu giá trị cho --vercel-origin"
            VERCEL_ORIGIN="$2"; shift 2 ;;
        --skip-duckdns) SKIP_DUCKDNS=1; shift ;;
        --skip-firewall) SKIP_FIREWALL=1; shift ;;
        --skip-tls) SKIP_TLS=1; shift ;;
        --no-pull) PULL_ARGS+=(--no-pull); shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Tham số không hợp lệ: $1 (dùng --help)" ;;
    esac
done

if [ "${EUID}" -ne 0 ]; then
    die "Chạy bằng quyền root: sudo bash $0 --domain ... --email ..."
fi

[ -f "$COMPOSE_FILE" ] || die "Không tìm thấy $COMPOSE_FILE"
[ -f "$ENV_FILE" ] || die "Không tìm thấy $ENV_FILE — chuyển .env_vps sang trước"
docker info >/dev/null 2>&1 || die "Docker daemon chưa chạy"

cd "$REPO_ROOT"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${COMPOSE[@]}" config -q

# Chuẩn hóa domain người dùng nhập (bỏ https:// và dấu / cuối).
normalize_domain() {
    local d="$1"
    d="${d#http://}"
    d="${d#https://}"
    d="${d%%/*}"
    printf '%s' "$d"
}
DOMAIN="$(normalize_domain "$DOMAIN")"
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]]; then
    die "Domain không hợp lệ: $DOMAIN"
fi
if [ -n "$EMAIL" ] && ! [[ "$EMAIL" == *@*.* ]]; then
    die "Email không hợp lệ: $EMAIL"
fi
if [ -n "$VERCEL_ORIGIN" ]; then
    VERCEL_ORIGIN="${VERCEL_ORIGIN%/}"
    [[ "$VERCEL_ORIGIN" == https://* ]] || die "--vercel-origin phải bắt đầu bằng https://"
    [[ "$VERCEL_ORIGIN" != *"*"* ]] || die "--vercel-origin không được chứa '*'"
fi

# ------------------------------------------------------------------------------
# 1. DuckDNS (chỉ khi dùng domain *.duckdns.org)
# ------------------------------------------------------------------------------
if [ "$SKIP_DUCKDNS" -eq 0 ]; then
    if [ -f "$DUCKDNS_ENV" ]; then
        # shellcheck disable=SC1090
        . "$DUCKDNS_ENV"
        existing="${DUCKDNS_DOMAIN:-}"
        if [ -n "$existing" ]; then
            if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "${existing}.duckdns.org" ]; then
                die "DuckDNS đang cấu hình ${existing}.duckdns.org, khác --domain $DOMAIN. Sửa $DUCKDNS_ENV hoặc bỏ --domain."
            fi
            DOMAIN="${existing}.duckdns.org"
            echo "==> DuckDNS hiện có: $DOMAIN"
            systemctl start duckdns-update.service || true
        fi
    elif [ -n "$DOMAIN" ] && [[ "$DOMAIN" == *.duckdns.org ]]; then
        sub="${DOMAIN%.duckdns.org}"
        echo "==> Cấu hình DuckDNS cho $DOMAIN"
        DUCKDNS_DOMAIN="$sub" bash "${REPO_ROOT}/deploy/duckdns/install_duckdns.sh"
    fi
fi

# ------------------------------------------------------------------------------
# 2. Firewall (UFW; GCP VPC rule vẫn phải mở 80/443 ở Console)
# ------------------------------------------------------------------------------
if [ "$SKIP_FIREWALL" -eq 0 ]; then
    echo "==> Cấu hình UFW"
    bash "${REPO_ROOT}/deploy/firewall.sh"
fi

# ------------------------------------------------------------------------------
# 3. Pull image + chạy stack + chờ /health
# ------------------------------------------------------------------------------
echo "==> Pull image và khởi động stack"
ENV_FILE="$ENV_FILE" COMPOSE_FILE="$COMPOSE_FILE" \
    bash "${SCRIPT_DIR}/deploy_vps.sh" ${PULL_ARGS[@]+"${PULL_ARGS[@]}"}

wait_health() {
    local body
    for _ in $(seq 1 30); do
        body="$(curl -fsS --max-time 3 http://127.0.0.1/health 2>/dev/null || true)"
        if printf '%s' "$body" | grep -q '"status":"healthy"'; then
            return 0
        fi
        sleep 2
    done
    return 1
}

# ------------------------------------------------------------------------------
# 4. CORS: ghi origin Vercel thật vào .env và restart backend
# ------------------------------------------------------------------------------
if [ -n "$VERCEL_ORIGIN" ]; then
    echo "==> Cập nhật CORS_ALLOW_ORIGINS=$VERCEL_ORIGIN"
    tmp_env="$(mktemp)"
    awk -v origin="$VERCEL_ORIGIN" '
        BEGIN { found = 0 }
        /^[[:space:]]*CORS_ALLOW_ORIGINS=/ {
            printf "CORS_ALLOW_ORIGINS=\"%s\"\n", origin
            found = 1
            next
        }
        { print }
        END { if (!found) printf "CORS_ALLOW_ORIGINS=\"%s\"\n", origin }
    ' "$ENV_FILE" > "$tmp_env"
    cat "$tmp_env" > "$ENV_FILE"
    rm -f "$tmp_env"
    "${COMPOSE[@]}" up -d backend
    wait_health || die "Backend không healthy sau khi cập nhật CORS"
else
    echo "CẢNH BÁO: chưa truyền --vercel-origin; CORS vẫn là placeholder trong .env."
fi

# ------------------------------------------------------------------------------
# 5. TLS (chỉ khi có domain + email và DNS đã trỏ đúng IP)
# ------------------------------------------------------------------------------
TLS_DONE=0
if [ "$SKIP_TLS" -eq 0 ] && [ -n "$DOMAIN" ] && [ -n "$EMAIL" ]; then
    public_ip="$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)"
    dns_ok=0
    for _ in $(seq 1 24); do
        resolved_ip="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1 {print $1}' || true)"
        if [ -n "$public_ip" ] && [ "$resolved_ip" = "$public_ip" ]; then
            dns_ok=1
            break
        fi
        sleep 5
    done

    if [ "$dns_ok" -eq 1 ]; then
        echo "==> Cấp TLS cho $DOMAIN"
        bash "${REPO_ROOT}/deploy/tls/issue_tls.sh" "$DOMAIN" "$EMAIL"
        curl -fsS "https://${DOMAIN}/health" >/dev/null
        TLS_DONE=1
    else
        echo "CẢNH BÁO: DNS của $DOMAIN chưa trỏ về ${public_ip:-IP VPS}; bỏ qua TLS." >&2
        echo "Chạy lại sau: sudo bash $0 --domain $DOMAIN --email $EMAIL" >&2
    fi
elif [ "$SKIP_TLS" -eq 0 ]; then
    echo "CẢNH BÁO: thiếu --domain/--email; chỉ deploy HTTP." >&2
fi

# ------------------------------------------------------------------------------
# 6. Tổng kết
# ------------------------------------------------------------------------------
echo
echo "==================== KẾT QUẢ ===================="
"${COMPOSE[@]}" ps
echo
curl -fsS http://127.0.0.1/health || true
echo
if [ "$TLS_DONE" -eq 1 ]; then
    curl -fsS "https://${DOMAIN}/health"
    echo
fi
echo "=================================================="
echo "Vercel cần cấu hình:"
echo "  VITE_API_BASE_URL=https://${DOMAIN:-<backend-host>}"
echo "  CSP connect-src https://${DOMAIN:-<backend-host>} (frontend/vercel.json)"
if [ -z "$VERCEL_ORIGIN" ]; then
    echo "  CORS_ALLOW_ORIGINS=<url Vercel thật> trong .env, rồi restart backend"
fi
if [ "$TLS_DONE" -ne 1 ]; then
    echo "TLS chưa hoàn tất — chạy lại script với --domain/--email sau khi DNS đúng."
fi
