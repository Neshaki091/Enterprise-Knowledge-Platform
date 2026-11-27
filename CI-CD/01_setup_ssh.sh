#!/usr/bin/env bash
# ==============================================================================
# 01 — Tạo deploy key riêng cho GitHub Actions, cài lên VPS, pin host key.
# Chạy: bash CI-CD/01_setup_ssh.sh
#
# Kết quả:
#   ~/.ssh/ekp_deploy        private key (dùng cho secret VPS_SSH_KEY)
#   ~/.ssh/ekp_deploy.pub    public key (đã cài vào authorized_keys trên VPS)
#   ~/.ssh/ekp_known_hosts   host key đã pin (secret VPS_KNOWN_HOSTS)
# ==============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

require_vps_target

mkdir -p "$(dirname "$VPS_SSH_KEY")"
chmod 700 "$(dirname "$VPS_SSH_KEY")" 2>/dev/null || true

if [ ! -f "$VPS_SSH_KEY" ]; then
    say "Tạo key mới: $VPS_SSH_KEY"
    ssh-keygen -t ed25519 -C "github-actions-ekp" -f "$VPS_SSH_KEY" -N ""
else
    say "Giữ key đã có: $VPS_SSH_KEY"
fi
chmod 600 "$VPS_SSH_KEY" 2>/dev/null || true
chmod 644 "$VPS_SSH_KEY.pub" 2>/dev/null || true

say "Cài public key lên VPS (thử bằng key/agent SSH hiện có của bạn)"
if ! cat "$VPS_SSH_KEY.pub" | ssh -o BatchMode=yes -o ConnectTimeout=15 "$VPS_USER@$VPS_HOST" \
    "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"; then
    if command -v ssh-copy-id >/dev/null 2>&1; then
        warn "Không cài tự động được; thử ssh-copy-id (sẽ hỏi mật khẩu VPS)."
        ssh-copy-id -i "$VPS_SSH_KEY.pub" "$VPS_USER@$VPS_HOST"
    else
        die "Không cài được public key. Thêm thủ công nội dung $VPS_SSH_KEY.pub vào ~/.ssh/authorized_keys trên VPS."
    fi
fi

say "Quét host key vào $VPS_KNOWN_HOSTS"
mkdir -p "$(dirname "$VPS_KNOWN_HOSTS")"
ssh-keyscan -H "$VPS_HOST" > "$VPS_KNOWN_HOSTS" 2>/dev/null
[ -s "$VPS_KNOWN_HOSTS" ] || die "ssh-keyscan không trả về host key cho $VPS_HOST (port 22 bị chặn?)"
chmod 600 "$VPS_KNOWN_HOSTS"

say "Fingerprint đang pin — so khớp trên VPS bằng: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
ssh-keygen -lf "$VPS_KNOWN_HOSTS" || true

say "Test SSH pin + sudo không mật khẩu"
if ! vps_ssh "echo KEY_OK && sudo -n true && echo SUDO_OK"; then
    die "Test thất bại. 'Host key verification failed' → so lại fingerprint; 'sudo' lỗi → cấu hình NOPASSWD."
fi

say "Hoàn tất."
say "  private key : $VPS_SSH_KEY"
say "  known_hosts : $VPS_KNOWN_HOSTS"
