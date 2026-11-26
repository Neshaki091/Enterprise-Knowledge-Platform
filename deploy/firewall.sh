#!/usr/bin/env bash
# ==============================================================================
# UFW trên VM: chỉ mở SSH + 80 (ACME/redirect) + 443 (API/SSE).
#   sudo bash deploy/firewall.sh
#
# Lưu ý: GCP còn một lớp firewall ở VPC. Nếu chưa có rule cho 80/443, tạo trong
# Console (VPC network > Firewall) hoặc bằng gcloud:
#   gcloud compute firewall-rules create allow-http-https \
#     --allow tcp:80,tcp:443 --source-ranges 0.0.0.0/0 --target-tags http-server
# và gắn network tag "http-server" cho VM.
# ==============================================================================
set -euo pipefail

if [ "${EUID}" -ne 0 ]; then
    echo "Vui lòng chạy với quyền root: sudo bash $0" >&2
    exit 1
fi

if ! command -v ufw >/dev/null 2>&1; then
    echo "Cài ufw..."
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ufw
fi

# Mở SSH trước để không tự khóa mình khỏi VM.
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "------------------------------------------------------------------"
ufw status verbose
echo "------------------------------------------------------------------"
echo "Nhắc lại: PostgreSQL (5432) và FastAPI (8000) KHÔNG được mở ra Internet."
echo "Kiểm tra thêm GCP VPC firewall cho tcp:80 và tcp:443."
