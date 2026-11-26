#!/usr/bin/env bash
# ==============================================================================
# Cài DuckDNS updater một lần trên GCP e2-micro (Ubuntu 22.04).
#
#   sudo bash deploy/duckdns/install_duckdns.sh
#
# - Tạo /etc/duckdns/duckdns.env (chmod 600) chứa subdomain + token.
# - Cài /usr/local/bin/duckdns-update.sh + systemd service/timer (5 phút/lần).
# - Chạy thử ngay và kiểm tra DNS trỏ về IP công khai của VM.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="/etc/duckdns"
ENV_FILE="${ENV_DIR}/duckdns.env"
UPDATER="/usr/local/bin/duckdns-update.sh"

if [ "${EUID}" -ne 0 ]; then
    echo "Vui lòng chạy với quyền root: sudo bash $0" >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "Cài curl..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl
fi

# ------------------------------------------------------------------------------
# 1. Cấu hình subdomain + token (ưu tiên biến môi trường, sau đó hỏi tương tác).
# ------------------------------------------------------------------------------
install -d -m 0750 "$ENV_DIR"

if [ ! -f "$ENV_FILE" ]; then
    domain="${DUCKDNS_DOMAIN:-}"
    token="${DUCKDNS_TOKEN:-}"

    if [ -z "$domain" ]; then
        read -rp "DuckDNS subdomain (không gồm .duckdns.org): " domain
    fi
    if [ -z "$token" ]; then
        read -rsp "DuckDNS token (xem trên duckdns.org): " token
        echo
    fi

    if [ -z "$domain" ] || [ -z "$token" ]; then
        echo "Thiếu subdomain hoặc token." >&2
        exit 1
    fi

    umask 077
    {
        printf 'DUCKDNS_DOMAIN=%s\n' "$domain"
        printf 'DUCKDNS_TOKEN=%s\n' "$token"
    } > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Đã ghi cấu hình vào $ENV_FILE (chmod 600)."
else
    echo "Đã có $ENV_FILE, giữ nguyên cấu hình hiện tại."
fi

# shellcheck source=/dev/null
. "$ENV_FILE"

# ------------------------------------------------------------------------------
# 2. Cài updater + systemd units.
# ------------------------------------------------------------------------------
install -m 0755 "${SCRIPT_DIR}/duckdns-update.sh" "$UPDATER"

cat > /etc/systemd/system/duckdns-update.service <<'EOF'
[Unit]
Description=DuckDNS dynamic DNS updater (Enterprise SaaS backend)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/duckdns-update.sh
Nice=10
EOF

cat > /etc/systemd/system/duckdns-update.timer <<'EOF'
[Unit]
Description=Cập nhật DuckDNS mỗi 5 phút

[Timer]
OnBootSec=30s
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now duckdns-update.timer

# ------------------------------------------------------------------------------
# 3. Chạy thử + kiểm tra DNS.
# ------------------------------------------------------------------------------
echo "Chạy cập nhật DuckDNS lần đầu..."
if ! systemctl start duckdns-update.service; then
    echo "Cảnh báo: lần cập nhật đầu thất bại (mạng/DNS?). Timer sẽ tự thử lại sau 5 phút." >&2
fi

public_ip="$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)"
resolved_ip="$(getent ahostsv4 "${DUCKDNS_DOMAIN}.duckdns.org" | awk 'NR==1 {print $1}' || true)"

echo "------------------------------------------------------------------"
echo "DuckDNS host : ${DUCKDNS_DOMAIN}.duckdns.org"
echo "IP công khai : ${public_ip:-không xác định}"
echo "DNS phân giải: ${resolved_ip:-chưa phân giải}"
if [ -n "$public_ip" ] && [ "$public_ip" = "$resolved_ip" ]; then
    echo "Trạng thái   : OK — DNS đã trỏ đúng về VM."
else
    echo "Trạng thái   : CHỜ — DNS có thể cần 1–2 phút để cập nhật; kiểm tra lại sau."
fi
echo "Timer        : $(systemctl is-active duckdns-update.timer)"
echo "------------------------------------------------------------------"
echo "Lưu ý: KHÔNG commit hoặc chia sẻ nội dung $ENV_FILE."
