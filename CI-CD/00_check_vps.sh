#!/usr/bin/env bash
# ==============================================================================
# 00 — Kiểm tra VPS trước khi bật CI/CD.
# Chạy: bash CI-CD/00_check_vps.sh
#
# Kiểm tra (không phá gì):
#   - repo sạch, đúng nhánh main, git pull được
#   - sudo không cần mật khẩu (bắt buộc cho SSH deploy)
#   - Docker + Compose chạy được, stack hiện tại
#   - UFW (nếu có) — cần mở SSH 22 cho GitHub runner
# ==============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

require_vps_target

SSH_MODE="bootstrap"
if [ -f "${VPS_SSH_KEY:-}" ] && [ -f "${VPS_KNOWN_HOSTS:-}" ]; then
    if vps_ssh "echo KEY_OK" >/dev/null 2>&1; then
        SSH_MODE="pinned"
    fi
fi

remote_ssh() {
    if [ "$SSH_MODE" = "pinned" ]; then
        vps_ssh "$@"
    else
        vps_ssh_bootstrap "$@"
    fi
}

say "Kiểm tra VPS ${VPS_USER}@${VPS_HOST}:${VPS_APP_DIR} (SSH ${SSH_MODE})"
remote_ssh "VPS_APP_DIR='${VPS_APP_DIR}' bash -s" <<'REMOTE'
set -Eeuo pipefail
export GIT_TERMINAL_PROMPT=0
cd "$VPS_APP_DIR"

printf 'Thư mục : %s\n' "$(pwd)"
printf 'Nhánh   : %s\n' "$(git rev-parse --abbrev-ref HEAD)"
printf 'Commit  : %s\n' "$(git log -1 --oneline)"

dirty="$(git status --porcelain)"
if [ -n "$dirty" ]; then
    printf '%s\n' "$dirty"
    echo "LỖI: có thay đổi tracked trên VPS. Commit/stash trước khi CI/CD deploy." >&2
    exit 2
fi

echo "==> git pull --ff-only (không tương tác, giống CI)"
git pull --ff-only

if ! sudo -n true; then
    echo "LỖI: sudo cần mật khẩu. Cấu hình NOPASSWD cho user deploy (xem hướng dẫn)." >&2
    exit 3
fi
echo "SUDO_OK"

command -v docker >/dev/null 2>&1 || { echo "LỖI: chưa có docker" >&2; exit 4; }
[ -f .env ] || { echo "LỖI: thiếu .env trong $VPS_APP_DIR" >&2; exit 5; }
echo "==> IMAGE_TAG hiện tại"
sudo grep '^IMAGE_TAG=' .env || true

echo "==> docker compose config -q"
if ! config_err="$(sudo docker compose --env-file .env -f docker-compose.registry.yml config -q 2>&1)"; then
    printf '%s\n' "$config_err"
    echo "LỖI: compose/.env thiếu biến bắt buộc." >&2
    exit 6
fi
echo "COMPOSE_CONFIG_OK"

sudo docker compose --env-file .env -f docker-compose.registry.yml ps || true

if command -v ufw >/dev/null 2>&1; then
    echo "==> UFW"
    sudo ufw status | head -n 8 || true
fi

echo "CHECK_OK"
REMOTE
