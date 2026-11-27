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

say "Kiểm tra VPS ${VPS_USER}@${VPS_HOST}:${VPS_APP_DIR} (dùng SSH hiện có của bạn)"
vps_ssh_bootstrap "VPS_APP_DIR='${VPS_APP_DIR}' bash -s" <<'REMOTE'
set -Eeuo pipefail
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

echo "==> git pull --ff-only"
git pull --ff-only

if ! sudo -n true; then
    echo "LỖI: sudo cần mật khẩu. Cấu hình NOPASSWD cho user deploy (xem hướng dẫn)." >&2
    exit 3
fi
echo "SUDO_OK"

command -v docker >/dev/null 2>&1 || { echo "LỖI: chưa có docker" >&2; exit 4; }
sudo docker compose --env-file .env -f docker-compose.registry.yml ps || true

if command -v ufw >/dev/null 2>&1; then
    echo "==> UFW"
    sudo ufw status | head -n 8 || true
fi

echo "CHECK_OK"
REMOTE
