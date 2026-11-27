#!/usr/bin/env bash
# ==============================================================================
# 04 — Phát hành phiên bản: đọc APP_VERSION, tạo tag vX.Y.Z và push.
# Chạy: bash CI-CD/04_release.sh             (hỏi xác nhận)
#       bash CI-CD/04_release.sh --yes       (không hỏi)
#       bash CI-CD/04_release.sh --dry-run   (chỉ kiểm tra, không push/tag)
#
# Tag push sẽ kích hoạt workflow Release & Deploy; job deploy chờ duyệt.
# ==============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./_lib.sh
. "${SCRIPT_DIR}/_lib.sh"

DRY_RUN=0
ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) die "Tham số không hợp lệ: $arg" ;;
    esac
done

cd "$REPO_ROOT"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Không phải git repo: $REPO_ROOT"

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || die "Phải ở nhánh main (đang ở: $BRANCH)"

VERSION_FILE="backend/app/core/version.py"
[ -f "$VERSION_FILE" ] || die "Không thấy $VERSION_FILE"
APP_VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"[[:space:]]*$/\1/p' "$VERSION_FILE" | tail -n 1)"
[ -n "$APP_VERSION" ] || die "Không đọc được APP_VERSION trong $VERSION_FILE"
case "$APP_VERSION" in
    *[!A-Za-z0-9_.-]*) die "APP_VERSION chứa ký tự không hợp lệ: $APP_VERSION" ;;
esac
TAG="v${APP_VERSION}"

DIRTY="$(git status --porcelain --untracked-files=no)"
if [ -n "$DIRTY" ]; then
    printf '%s\n' "$DIRTY"
    die "Có thay đổi tracked chưa commit. Commit/stash trước khi phát hành."
fi

say "Fetch origin"
git fetch --tags --prune origin

if git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
    die "Tag ${TAG} đã tồn tại ở local. Bump APP_VERSION rồi commit trước khi phát hành bản mới."
fi
if [ -n "$(git ls-remote --tags origin "refs/tags/${TAG}")" ]; then
    die "Tag ${TAG} đã tồn tại trên origin. Không di chuyển tag đã phát hành — bump APP_VERSION."
fi

BEHIND="$(git rev-list --count HEAD..origin/main)"
[ "$BEHIND" -eq 0 ] || die "main đang sau origin/main ${BEHIND} commit. Chạy 'git pull --ff-only' trước."
AHEAD="$(git rev-list --count origin/main..HEAD)"

say "Sẽ phát hành ${TAG} từ commit $(git rev-parse --short HEAD)"
if [ "$AHEAD" -gt 0 ]; then
    say "Commit local sẽ được push lên main trước khi tạo tag"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    say "DRY-RUN: kiểm tra xong, không push/tag."
    exit 0
fi

if [ "$ASSUME_YES" -eq 0 ]; then
    read -r -p "Gõ RELEASE để tiếp tục: " answer
    [ "$answer" = "RELEASE" ] || die "Đã huỷ."
fi

if [ "$AHEAD" -gt 0 ]; then
    git push origin main
fi
git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"

repo_slug="${REPO_SLUG:-}"
if is_placeholder "$repo_slug"; then
    repo_slug="$(git remote get-url origin | sed -E 's#^.*github\.com[:/]([^/]+/[^/.]+)(\.git)?$#\1#')"
fi
say "Đã push tag ${TAG}."
say "Theo dõi: https://github.com/${repo_slug}/actions/workflows/release.yml"
say "Job deploy sẽ dừng chờ approve Environment '${GITHUB_ENV_NAME:-production}'."
