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

say "Deploy key fingerprint (local)"
ssh-keygen -lf "$VPS_SSH_KEY.pub" || true

say "Quét host key vào $VPS_KNOWN_HOSTS"
mkdir -p "$(dirname "$VPS_KNOWN_HOSTS")"
ssh-keyscan -H "$VPS_HOST" > "$VPS_KNOWN_HOSTS" 2>/dev/null
[ -s "$VPS_KNOWN_HOSTS" ] || die "ssh-keyscan không trả về host key cho $VPS_HOST (port 22 bị chặn?)"
chmod 600 "$VPS_KNOWN_HOSTS"

say "Fingerprint đang pin — so khớp trên VPS bằng: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"
ssh-keygen -lf "$VPS_KNOWN_HOSTS" || true

key_ok() {
    vps_ssh "echo KEY_OK" >/dev/null 2>&1
}

if ! key_ok; then
    say "Deploy key chưa được cài; thử cài tự động bằng SSH hiện có"
    if ! cat "$VPS_SSH_KEY.pub" | ssh -o BatchMode=yes -o ConnectTimeout=15 "$VPS_USER@$VPS_HOST" \
        "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"; then
        if command -v ssh-copy-id >/dev/null 2>&1; then
            warn "Thử ssh-copy-id (có thể hỏi mật khẩu VPS)."
            ssh-copy-id -i "$VPS_SSH_KEY.pub" "$VPS_USER@$VPS_HOST" || true
        fi
    fi
fi

if ! key_ok; then
    cat >&2 <<EOF

[LỖI] Chưa đăng nhập được bằng deploy key.

Máy local không có SSH key để tự cài (ví dụ bạn chỉ vào VPS bằng GCP Console
browser SSH). Mở terminal browser tới VPS, dán 3 lệnh sau:

  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  echo '$(cat "$VPS_SSH_KEY.pub")' >> ~/.ssh/authorized_keys
  chmod 600 ~/.ssh/authorized_keys

Sau đó chạy lại: bash CI-CD/01_setup_ssh.sh

Kiểm tra trên VPS (phải thấy fingerprint của deploy key):
  whoami
  grep -c github-actions-ekp ~/.ssh/authorized_keys
  ssh-keygen -lf ~/.ssh/authorized_keys

Fingerprint cần thấy: $(ssh-keygen -lf "$VPS_SSH_KEY.pub" 2>/dev/null | awk '{print $2}')

Nếu VPS dùng OS Login hoặc guest agent tự ghi lại authorized_keys, key thêm
thủ công có thể bị bỏ qua/biến mất; khi đó thêm public key qua GCP metadata
(SSH Keys của VM) hoặc OS Login rồi chạy lại script.
EOF
    exit 1
fi

say "Test sudo không mật khẩu"
if ! vps_ssh "sudo -n true"; then
    die "Key đăng nhập OK nhưng sudo cần mật khẩu. Cấu hình NOPASSWD cho $VPS_USER."
fi
echo "KEY_OK / SUDO_OK"

say "Hoàn tất."
say "  private key : $VPS_SSH_KEY"
say "  known_hosts : $VPS_KNOWN_HOSTS"
