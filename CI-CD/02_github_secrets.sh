#!/usr/bin/env bash
# ==============================================================================
# 02 — Đưa secrets/variables lên GitHub cho workflow Release & Deploy.
# Chạy: bash CI-CD/02_github_secrets.sh
#
# Yêu cầu:
#   - gh CLI đã đăng nhập (gh auth login)
#   - Environment "production" đã tạo trên GitHub, có Required reviewers
#   - Đã chạy 01_setup_ssh.sh (có key + known_hosts)
#
# Docker Hub token KHÔNG lưu vào file; script hỏi và đẩy thẳng lên GitHub.
# ==============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

require_gh
require_repo_config
require_vps_config

[ -n "${DOCKERHUB_USERNAME:-}" ] || die "Thiếu DOCKERHUB_USERNAME trong $CONFIG_FILE"
if is_placeholder "$DOCKERHUB_USERNAME"; then
    die "DOCKERHUB_USERNAME chưa được sửa trong $CONFIG_FILE"
fi

if ! gh api "repos/${REPO_SLUG}/environments/${GITHUB_ENV_NAME}" >/dev/null 2>&1; then
    cat >&2 <<EOF
[LỖI] Chưa có Environment '${GITHUB_ENV_NAME}' trên GitHub.

Tạo trước trên web:
  Repo → Settings → Environments → New environment → ${GITHUB_ENV_NAME}
  → Required reviewers: thêm bạn
  → TẮT "Prevent self-review" nếu bạn là người duyệt duy nhất
  → Save protection rules

Sau đó chạy lại script này.
EOF
    exit 2
fi

rules="$(gh api "repos/${REPO_SLUG}/environments/${GITHUB_ENV_NAME}" --jq '.protection_rules | length' 2>/dev/null || echo 0)"
if [ "${rules:-0}" -eq 0 ]; then
    warn "Environment '${GITHUB_ENV_NAME}' chưa có protection rules/required reviewers — deploy sẽ không chờ duyệt."
fi

say "Đặt repository secrets (Docker Hub)"
printf '%s' "$DOCKERHUB_USERNAME" | gh secret set DOCKERHUB_USERNAME --repo "$REPO_SLUG"

read -rsp "Docker Hub token (Read & Write; Enter để bỏ qua nếu đã set): " hub_token
echo
if [ -n "${hub_token:-}" ]; then
    printf '%s' "$hub_token" | gh secret set DOCKERHUB_TOKEN --repo "$REPO_SLUG"
    unset hub_token
else
    warn "Bỏ qua DOCKERHUB_TOKEN. Đảm bảo secret này đã tồn tại, nếu không job publish sẽ fail."
fi

say "Đặt environment secrets cho '${GITHUB_ENV_NAME}'"
printf '%s' "$VPS_HOST" | gh secret set VPS_HOST --repo "$REPO_SLUG" --env "$GITHUB_ENV_NAME"
printf '%s' "$VPS_USER" | gh secret set VPS_USER --repo "$REPO_SLUG" --env "$GITHUB_ENV_NAME"
gh secret set VPS_SSH_KEY --repo "$REPO_SLUG" --env "$GITHUB_ENV_NAME" < "$VPS_SSH_KEY"
gh secret set VPS_KNOWN_HOSTS --repo "$REPO_SLUG" --env "$GITHUB_ENV_NAME" < "$VPS_KNOWN_HOSTS"

say "Đặt environment variables"
gh variable set VPS_APP_DIR --repo "$REPO_SLUG" --env "$GITHUB_ENV_NAME" --body "$VPS_APP_DIR"
if [ -n "${PUBLIC_HEALTH_URL:-}" ] && ! is_placeholder "$PUBLIC_HEALTH_URL"; then
    gh variable set PUBLIC_HEALTH_URL --repo "$REPO_SLUG" --env "$GITHUB_ENV_NAME" --body "$PUBLIC_HEALTH_URL"
else
    warn "PUBLIC_HEALTH_URL trống/CHANGE_ME — bỏ qua; workflow sẽ không kiểm tra health từ Internet."
fi

say "Secrets hiện có"
gh secret list --repo "$REPO_SLUG" --env "$GITHUB_ENV_NAME" || true
say "Variables hiện có"
gh variable list --repo "$REPO_SLUG" --env "$GITHUB_ENV_NAME" || true

say "Hoàn tất cấu hình GitHub."
