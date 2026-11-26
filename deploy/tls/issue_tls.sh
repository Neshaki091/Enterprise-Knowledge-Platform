#!/usr/bin/env bash
# ==============================================================================
# Cấp chứng chỉ Let's Encrypt (HTTP-01 webroot) và bật server TLS 443.
#
#   sudo bash deploy/tls/issue_tls.sh <ten>.duckdns.org [email]
#
# Điều kiện trước khi chạy:
#   1) DuckDNS đã trỏ đúng IP công khai của VM (install_duckdns.sh).
#   2) Stack đang chạy bằng compose production:
#      docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
#      (Nginx bootstrap chỉ mở :80 để phục vụ ACME + health + redirect).
#
# Script làm gì:
#   - Tự cài certbot nếu thiếu; kiểm tra DNS khớp IP công khai.
#   - Tự kiểm tra webroot ACME qua Nginx trước khi xin cert (tránh đốt rate limit).
#   - Xin cert với --cert-name cố định "saas-backend".
#   - Copy nginx/prod/tls-server.conf -> nginx/runtime/10-tls.conf, test + reload.
#   - Cài deploy hook để mỗi lần renew tự reload Nginx container.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DOMAIN="${1:-${TLS_DOMAIN:-}}"
EMAIL="${2:-${TLS_EMAIL:-}}"
CONTAINER_NAME="saas_nginx_proxy"
CERT_NAME="saas-backend"
WEBROOT="${REPO_ROOT}/certbot/www"

if [ "${EUID}" -ne 0 ]; then
    echo "Vui lòng chạy với quyền root: sudo bash $0 <domain> [email]" >&2
    exit 1
fi

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Thiếu tham số. Dùng: sudo bash $0 <ten>.duckdns.org <email>" >&2
    exit 1
fi

if [ ! -f "${REPO_ROOT}/docker-compose.prod.yml" ]; then
    echo "Không tìm thấy docker-compose.prod.yml trong ${REPO_ROOT}." >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# 1. Công cụ cần thiết.
# ------------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    echo "Docker chưa cài. Cài Docker trước khi chạy script này." >&2
    exit 1
fi

if ! command -v certbot >/dev/null 2>&1; then
    echo "Cài certbot..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot
fi

# ------------------------------------------------------------------------------
# 2. Nginx production phải đang chạy (bootstrap :80).
# ------------------------------------------------------------------------------
if ! docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -qx true; then
    echo "Container $CONTAINER_NAME chưa chạy. Khởi động stack production trước:" >&2
    echo "  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build" >&2
    exit 1
fi

mkdir -p "${WEBROOT}/.well-known/acme-challenge"

# Tự kiểm tra webroot: certbot sẽ thất bại nếu Nginx chưa mount ./certbot/www.
probe="selftest-$(date +%s)"
printf '%s' "$probe" > "${WEBROOT}/.well-known/acme-challenge/${probe}"
probe_body="$(curl -fsS --max-time 10 "http://127.0.0.1/.well-known/acme-challenge/${probe}" || true)"
rm -f "${WEBROOT}/.well-known/acme-challenge/${probe}"

if [ "$probe_body" != "$probe" ]; then
    echo "Webroot ACME chưa hoạt động qua Nginx." >&2
    echo "Kiểm tra: compose có override docker-compose.prod.yml và mount ./certbot/www?" >&2
    echo "Nhận được: '${probe_body}'" >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# 3. DNS phải trỏ về IP công khai của VM (tránh LE thất bại và đốt rate limit).
# ------------------------------------------------------------------------------
public_ip="$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)"
resolved_ip="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1 {print $1}' || true)"

echo "IP công khai : ${public_ip:-không xác định}"
echo "DNS hiện tại : ${resolved_ip:-chưa phân giải}"

if [ "${SKIP_DNS_CHECK:-0}" != "1" ] && { [ -z "$public_ip" ] || [ "$public_ip" != "$resolved_ip" ]; }; then
    echo "DNS chưa trỏ đúng về VM này." >&2
    echo "Đợi 1–2 phút rồi chạy lại, hoặc đặt SKIP_DNS_CHECK=1 nếu bạn chắc chắn." >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# 4. Xin chứng chỉ (idempotent: cert còn hạn thì giữ nguyên).
# ------------------------------------------------------------------------------
certbot certonly \
    --webroot -w "$WEBROOT" \
    --cert-name "$CERT_NAME" \
    -d "$DOMAIN" \
    --email "$EMAIL" \
    --agree-tos --non-interactive \
    --keep-until-expiring

# ------------------------------------------------------------------------------
# 5. Deploy hook: reload Nginx container sau mỗi lần renew.
# ------------------------------------------------------------------------------
install -d -m 0755 /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/10-reload-saas-nginx.sh <<'HOOK'
#!/usr/bin/env bash
# Reload Nginx container sau khi Let's Encrypt gia hạn cert thành công.
set -euo pipefail
if docker inspect -f '{{.State.Running}}' saas_nginx_proxy 2>/dev/null | grep -qx true; then
    docker exec saas_nginx_proxy nginx -s reload
else
    # Khi container khởi động lại, Nginx sẽ tự đọc cert mới — không cần reload.
    echo "saas_nginx_proxy không chạy; bỏ qua reload." >&2
fi
HOOK
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/10-reload-saas-nginx.sh

if systemctl list-unit-files --no-legend certbot.timer 2>/dev/null | grep -q '^certbot.timer'; then
    systemctl enable --now certbot.timer
else
    echo "Cảnh báo: không thấy certbot.timer; kiểm tra cơ chế gia hạn của bản certbot đang cài." >&2
fi

# ------------------------------------------------------------------------------
# 6. Bật server 443: copy cấu hình TLS vào runtime và reload.
# ------------------------------------------------------------------------------
install -m 0644 "${REPO_ROOT}/nginx/prod/tls-server.conf" "${REPO_ROOT}/nginx/runtime/10-tls.conf"

docker exec "$CONTAINER_NAME" nginx -t
docker exec "$CONTAINER_NAME" nginx -s reload

# ------------------------------------------------------------------------------
# 7. Xác minh TLS thật (SNI + chuỗi cert) qua loopback, không phụ thuộc DNS cache.
# ------------------------------------------------------------------------------
echo "Kiểm tra https://${DOMAIN}/health ..."
curl -fsS --max-time 15 --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/health"
echo

echo "------------------------------------------------------------------"
echo "Cert hết hạn: $(openssl x509 -enddate -noout -in /etc/letsencrypt/live/${CERT_NAME}/fullchain.pem)"
echo "Timer certbot: $(systemctl is-active certbot.timer 2>/dev/null || echo 'không có')"
echo "Chạy thử gia hạn (không đốt quota): sudo certbot renew --dry-run"
echo "------------------------------------------------------------------"
