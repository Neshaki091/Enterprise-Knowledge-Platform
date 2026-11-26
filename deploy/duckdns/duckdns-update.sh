#!/usr/bin/env bash
# ==============================================================================
# Cập nhật bản ghi DuckDNS về IP công khai hiện tại của VM.
# Được cài vào /usr/local/bin và chạy bởi duckdns-update.timer mỗi 5 phút.
# - Giữ subdomain không bị DuckDNS xóa (30 ngày không update).
# - Tự sửa DNS khi GCP cấp IP ephemeral mới sau reboot.
# KHÔNG bật `set -x`: token không được lộ ra log.
# ==============================================================================
set -euo pipefail

ENV_FILE="${DUCKDNS_ENV_FILE:-/etc/duckdns/duckdns.env}"

if [ ! -r "$ENV_FILE" ]; then
    echo "Không đọc được $ENV_FILE (cần chạy install_duckdns.sh trước)." >&2
    exit 1
fi

# shellcheck source=/dev/null
. "$ENV_FILE"

: "${DUCKDNS_DOMAIN:?DUCKDNS_DOMAIN chưa được cấu hình trong $ENV_FILE}"
: "${DUCKDNS_TOKEN:?DUCKDNS_TOKEN chưa được cấu hình trong $ENV_FILE}"

# ip= rỗng: DuckDNS tự lấy IP công khai của request hiện tại.
response="$(curl -fsS --retry 3 --retry-delay 5 --max-time 20 \
    "https://www.duckdns.org/update?domains=${DUCKDNS_DOMAIN}&token=${DUCKDNS_TOKEN}&ip=")"

if [ "$response" != "OK" ]; then
    echo "DuckDNS trả về không thành công: '$response'" >&2
    logger -t duckdns "Cập nhật thất bại cho ${DUCKDNS_DOMAIN}: ${response}" || true
    exit 1
fi

logger -t duckdns "Cập nhật thành công ${DUCKDNS_DOMAIN}.duckdns.org" || true
