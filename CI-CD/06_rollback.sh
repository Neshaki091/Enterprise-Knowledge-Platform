#!/usr/bin/env bash
# ==============================================================================
# 06 — Rollback thủ công về image ngay trước (tag 'previous').
# Chạy: bash CI-CD/06_rollback.sh          (hỏi xác nhận)
#       bash CI-CD/06_rollback.sh --yes    (không hỏi)
#
# Lưu ý: rollback image KHÔNG rollback migration CSDL.
# ==============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

ASSUME_YES=0
case "${1:-}" in
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    '') ;;
    *) die "Tham số không hợp lệ: $1" ;;
esac

require_vps_config

if [ "$ASSUME_YES" -eq 0 ]; then
    read -r -p "Rollback VPS về image 'previous'? Gõ ROLLBACK để tiếp tục: " answer
    [ "$answer" = "ROLLBACK" ] || die "Đã huỷ."
fi

say "Rollback ${VPS_USER}@${VPS_HOST} về tag 'previous'"
vps_ssh "VPS_APP_DIR='${VPS_APP_DIR}' bash -s" <<'REMOTE'
set -Eeuo pipefail
cd "$VPS_APP_DIR"

OLD_TAG="$(sudo grep -E '^IMAGE_TAG=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
restore() {
    rc=$?
    trap - EXIT
    if [ "$rc" -ne 0 ] && [ -n "$OLD_TAG" ]; then
        sudo sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=${OLD_TAG}/" .env || true
        echo "Rollback lỗi: đã khôi phục IMAGE_TAG=${OLD_TAG} trong .env" >&2
    fi
    exit "$rc"
}
trap restore EXIT

echo "Tag hiện tại: $(grep '^IMAGE_TAG=' .env || echo '?')"
sudo sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=previous/' .env
sudo chmod 600 .env
sudo bash deploy/registry/deploy_vps.sh --no-pull --no-prune

echo "Tag sau rollback: $(grep '^IMAGE_TAG=' .env || echo '?')"
REMOTE

say "Rollback hoàn tất. Kiểm tra tiếp: bash CI-CD/05_verify.sh"
