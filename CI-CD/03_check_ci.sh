#!/usr/bin/env bash
# ==============================================================================
# 03 — Xem trạng thái CI trên nhánh main.
# Chạy: bash CI-CD/03_check_ci.sh            (liệt kê 5 run gần nhất)
#       bash CI-CD/03_check_ci.sh --watch    (theo dõi run mới nhất tới khi xong)
# ==============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

WATCH=0
case "${1:-}" in
    --watch) WATCH=1 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    '') ;;
    *) die "Tham số không hợp lệ: $1" ;;
esac

require_gh
require_repo_config

say "Các run CI gần nhất trên main (${REPO_SLUG})"
gh run list --repo "$REPO_SLUG" --workflow ci.yml --branch main --limit 5

if [ "$WATCH" -eq 1 ]; then
    run_id="$(gh run list --repo "$REPO_SLUG" --workflow ci.yml --branch main \
        --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null || true)"
    [ -n "$run_id" ] || die "Không tìm thấy run nào của ci.yml trên main"
    say "Theo dõi run $run_id (Ctrl+C để dừng)"
    gh run watch "$run_id" --repo "$REPO_SLUG" --exit-status
fi
