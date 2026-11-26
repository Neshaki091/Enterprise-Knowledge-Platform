#!/usr/bin/env bash
# ==============================================================================
# Script tự động cấu hình Swapfile 4GB & Tinh chỉnh Linux Kernel trên GCP e2-micro
# Đảm bảo máy chủ 1.0 GB RAM không bao giờ bị OOM Killer bắn hạ khi tải cao.
# ==============================================================================

set -euo pipefail

echo "=========================================================="
echo "🚀 BẮT ĐẦU CẤU HÌNH HẠ TẦNG GCP ALWAYS FREE e2-micro"
echo "=========================================================="

# 1. Kiểm tra quyền root
if [ "$EUID" -ne 0 ]; then
  echo "❌ Vui lòng chạy script với quyền sudo: sudo bash $0"
  exit 1
fi

# 2. Tạo Swapfile 4.0 GB nếu chưa có
SWAPFILE="/swapfile"
if [ ! -f "$SWAPFILE" ]; then
    echo "📦 Đang tạo Swapfile 4.0 GB tại $SWAPFILE..."
    fallocate -l 4G "$SWAPFILE" || dd if=/dev/zero of="$SWAPFILE" bs=1M count=4096 status=progress
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE"
    swapon "$SWAPFILE"
    
    # Ghi vào /etc/fstab để tự động kích hoạt khi reboot
    if ! grep -q "$SWAPFILE" /etc/fstab; then
        echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
    fi
    echo "✅ Swapfile 4.0 GB đã được kích hoạt thành công!"
else
    echo "ℹ️  Swapfile đã tồn tại, bỏ qua bước tạo mới."
fi

# 3. Tinh chỉnh Kernel Parameters cho máy chủ 1.0 GB RAM
echo "⚙️  Đang tinh chỉnh các thông số Linux Kernel..."
cat <<EOF > /etc/sysctl.d/99-enterprise-low-memory.conf
# Giữ RAM cho tiến trình, chỉ swap ra đĩa khi RAM thực sự cấp thiết
vm.swappiness = 10

# Cân bằng áp lực cache VFS của hệ thống tệp
vm.vfs_cache_pressure = 50

# Giới hạn dirty memory buffer
vm.dirty_background_ratio = 5
vm.dirty_ratio = 10

# Tối ưu hóa kết nối socket cho k6 load testing
net.core.somaxconn = 4096
net.ipv4.tcp_max_syn_backlog = 4096
EOF

sysctl --system > /dev/null
echo "✅ Tinh chỉnh Kernel hoàn tất!"

# 4. Hiển thị thông số bộ nhớ hiện tại
echo "=========================================================="
echo "📊 BÁO CÁO THÔNG SỐ TÀI NGUYÊN MÁY CHỦ HIỆN TẠI:"
echo "=========================================================="
free -h
echo "=========================================================="
echo "🎉 HẠ TẦNG GCP e2-micro ĐÃ SẴN SÀNG CHO ENTERPRISE SAAS!"
echo "=========================================================="
