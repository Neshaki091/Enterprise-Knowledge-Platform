#!/usr/bin/env bash
# ==============================================================================
# 05 — Xác minh sau deploy: health HTTPS từ máy local + trạng thái trên VPS.
# Chạy: bash CI-CD/05_verify.sh
# ==============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

require_vps_config

if [ -n "${PUBLIC_HEALTH_URL:-}" ] && ! is_placeholder "$PUBLIC_HEALTH_URL"; then
    say "GET ${PUBLIC_HEALTH_URL}"
    body="$(curl -fsS --max-time 10 "$PUBLIC_HEALTH_URL")" \
        || die "Không gọi được ${PUBLIC_HEALTH_URL} (TLS/DNS/backend?)"
    printf '%s\n' "$body"
    printf '%s' "$body" | grep -q '"status"[[:space:]]*:[[:space:]]*"healthy"' \
        || die "Health không trả status healthy"
    say "Public health OK"
else
    warn "PUBLIC_HEALTH_URL trống/CHANGE_ME — bỏ qua kiểm tra health từ Internet."
fi

say "Trạng thái stack trên VPS"
vps_ssh "VPS_APP_DIR='${VPS_APP_DIR}' bash -s" <<'REMOTE'
set -Eeuo pipefail
cd "$VPS_APP_DIR"

echo "--- IMAGE_TAG trong .env ---"
sudo grep '^IMAGE_TAG=' .env || true

echo "--- Containers ---"
sudo docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'

echo "--- Image EKP ---"
sudo docker images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}' | grep 'ekp-' || true

echo "--- Health nội bộ qua Nginx ---"
curl -fsS --max-time 5 http://127.0.0.1/health || echo "(không gọi được /health qua Nginx)"
REMOTE
