#!/usr/bin/env bash
# ==============================================================================
# MẪU cấu hình. Sao chép thành config.sh rồi sửa các giá trị CHANGE_ME.
# config.sh đã được .gitignore (không commit thông tin hạ tầng cá nhân).
# ==============================================================================

# SSH tới VPS
# VPS_HOST CHỈ hostname/IP: không kèm https://, không kèm /health.
VPS_HOST="CHANGE_ME.duckdns.org"
VPS_USER="CHANGE_ME"                       # whoami trên VPS
VPS_APP_DIR="/home/CHANGE_ME/Enterprise-Knowledge-Platform"   # pwd trong repo trên VPS

# File trên máy local (Git Bash)
VPS_SSH_KEY="$HOME/.ssh/ekp_deploy"
VPS_KNOWN_HOSTS="$HOME/.ssh/ekp_known_hosts"

# GitHub
REPO_SLUG="Neshaki091/Enterprise-Knowledge-Platform"
GITHUB_ENV_NAME="production"
DOCKERHUB_USERNAME="neshaki091"

# Health check công khai sau deploy (để trống nếu chưa có TLS)
PUBLIC_HEALTH_URL="https://CHANGE_ME.duckdns.org/health"
