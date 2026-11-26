# SPRINT F4 (PHASE 2): Nginx Static Delivery, Docker Compose & Playwright E2E

**Mục tiêu:** Đóng gói Frontend thành static bundle siêu nhẹ phục vụ qua Nginx trong ngân sách 1.0 GB RAM của GCP e2-micro, tinh chỉnh CORS/CSP và kiểm thử E2E trình duyệt bằng Playwright.  
**Ước tính thời gian:** 0.5 - 1 tuần  
**Trạng thái:** ✅ Hoàn thành (2026-11-26)

---

## 1. PHÂN CHIA NHIỆM VỤ

1. **Docker & Nginx Integration:**
   - Tạo multi-stage `frontend/Dockerfile` (Node 20 Alpine build `dist/` $\rightarrow$ copy sang Nginx container `50M` RAM).
   - Cập nhật [nginx.conf](nginx/nginx.conf): phục vụ SPA (`try_files $uri /index.html`), gắn Cache-Control cho `/assets/*`, giữ `proxy_buffering off` cho `/api/v1/chat/stream` và `/api/v1/dashboards/*/realtime`, thêm Security Headers (`X-Content-Type-Options`, `X-Frame-Options`, CSP).
   - Cập nhật [docker-compose.yml](docker-compose.yml) truyền đủ biến `GOOGLE_API_KEY` & cấu hình AI + CORS domain thật.
2. **Playwright E2E & Kiểm thử giao diện:**
   - Kịch bản E2E đầy đủ: Đăng ký $\rightarrow$ Upload tài liệu $\rightarrow$ Search $\rightarrow$ Chat Stream thấy chunks trước token $\rightarrow$ Xem Tenant Analytics & Storage $\rightarrow$ Kiểm tra 403 RBAC.

---

## 2. CHECKLIST NGHIỆM THU SPRINT F4 (KẾT THÚC PHASE 2)
- [x] `docker compose up --build` chạy đủ PostgreSQL 16 + FastAPI + Nginx Frontend với tổng RAM sử dụng ổn định dưới ngưỡng e2-micro.
- [x] SSE Chat & SSE Analytics Realtime chạy mượt qua Nginx reverse proxy không bị buffer nghẽn.
- [x] Toàn bộ Playwright E2E tests pass 100% (2/2 PASS, 2026-11-26) trước khi chuyển sang **Phase 3 (Synthetic Business-Hours Workload & Pentest)**.
