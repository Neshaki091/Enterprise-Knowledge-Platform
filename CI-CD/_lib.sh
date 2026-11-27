#!/usr/bin/env bash
# ==============================================================================
# Thư viện dùng chung cho bộ script CI/CD trong thư mục CI-CD/.
# Không chạy trực tiếp; các script 00/01/05/06/07 source file này.
# ==============================================================================
set -Eeuo pipefail

CI_CD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${CI_CD_DIR}/.." && pwd)"
CONFIG_FILE="${CONFIG_FILE:-${CI_CD_DIR}/config.sh}"

say()  { printf '==> %s\n' "$*"; }
warn() { printf '[CẢNH BÁO] %s\n' "$*" >&2; }
die()  { printf '[LỖI] %s\n' "$*" >&2; exit 1; }

if [ ! -f "$CONFIG_FILE" ]; then
    die "Chưa có $CONFIG_FILE. Chạy: cp '$CI_CD_DIR/config.example.sh' '$CONFIG_FILE' rồi sửa giá trị."
fi

# shellcheck source=/dev/null
. "$CONFIG_FILE"

is_placeholder() {
    case "${1:-}" in
        ''|*CHANGE_ME*) return 0 ;;
        *) return 1 ;;
    esac
}

require_vps_target() {
    [ -n "${VPS_HOST:-}" ] || die "Thiếu VPS_HOST trong $CONFIG_FILE"
    [ -n "${VPS_USER:-}" ] || die "Thiếu VPS_USER trong $CONFIG_FILE"
    [ -n "${VPS_APP_DIR:-}" ] || die "Thiếu VPS_APP_DIR trong $CONFIG_FILE"
    if is_placeholder "$VPS_HOST"; then
        die "VPS_HOST chưa được sửa trong $CONFIG_FILE"
    fi
    if is_placeholder "$VPS_USER"; then
        die "VPS_USER chưa được sửa trong $CONFIG_FILE"
    fi
    if is_placeholder "$VPS_APP_DIR"; then
        die "VPS_APP_DIR chưa được sửa trong $CONFIG_FILE"
    fi
    case "$VPS_HOST" in
        *://*|*/*|*@*|*' '*)
            die "VPS_HOST phải là hostname/IP, không kèm scheme hay path (nhận: $VPS_HOST)" ;;
        *[!A-Za-z0-9.-]*)
            die "VPS_HOST chứa ký tự không hợp lệ: $VPS_HOST" ;;
    esac
    case "$VPS_USER" in
        *@*|*/*|*' '*)
            die "VPS_USER phải là username SSH, không chứa @ hay '/', khoảng trắng (nhận: $VPS_USER)" ;;
        *[!A-Za-z0-9._-]*)
            die "VPS_USER chứa ký tự không hợp lệ: $VPS_USER" ;;
    esac
    case "$VPS_APP_DIR" in
        /*) ;;
        *) die "VPS_APP_DIR phải là đường dẫn tuyệt đối trên VPS (vd /home/user/SaaS_Chatbot_platform)" ;;
    esac
    case "$VPS_APP_DIR" in
        *[!A-Za-z0-9/._-]*) die "VPS_APP_DIR chứa ký tự không hợp lệ: $VPS_APP_DIR" ;;
    esac
}

require_vps_config() {
    require_vps_target
    [ -n "${VPS_SSH_KEY:-}" ] || die "Thiếu VPS_SSH_KEY trong $CONFIG_FILE"
    [ -n "${VPS_KNOWN_HOSTS:-}" ] || die "Thiếu VPS_KNOWN_HOSTS trong $CONFIG_FILE"
    [ -f "$VPS_SSH_KEY" ] || die "Không thấy private key: $VPS_SSH_KEY (chạy 01_setup_ssh.sh)"
    [ -f "$VPS_KNOWN_HOSTS" ] || die "Không thấy known_hosts: $VPS_KNOWN_HOSTS (chạy 01_setup_ssh.sh)"
}

vps_ssh() {
    ssh -i "$VPS_SSH_KEY" \
        -o IdentitiesOnly=yes \
        -o BatchMode=yes \
        -o StrictHostKeyChecking=yes \
        -o UserKnownHostsFile="$VPS_KNOWN_HOSTS" \
        -o ConnectTimeout=15 \
        "$VPS_USER@$VPS_HOST" "$@"
}

# Dùng trước khi có deploy key (bootstrap): dựa vào SSH key/agent hiện có của bạn.
vps_ssh_bootstrap() {
    ssh -o ConnectTimeout=15 "$VPS_USER@$VPS_HOST" "$@"
}
