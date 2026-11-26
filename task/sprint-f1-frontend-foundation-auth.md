# SPRINT F1 (PHASE 2): Frontend Foundation, Auth JWT Rotation & Tenant Switcher

**Mục tiêu:** Dựng khung ứng dụng `frontend/` (React + TypeScript + Vite + Tailwind + shadcn/ui), xây dựng tầng HTTP Client tự động Refresh Token Rotation và phân quyền UI theo `TenantContext` (`EMPLOYEE`, `ADMIN`/`OWNER`, `SUPER_ADMIN`).  
**Ước tính thời gian:** 1 tuần  
**Trạng thái:** ✅ Hoàn thành

---

## 1. PHÂN CHIA NHIỆM VỤ

1. **Khởi tạo dự án `frontend/`:**
   - Cấu hình TypeScript strict, Vite proxy `/api/v1` trỏ về `http://localhost:8000` khi dev, Tailwind CSS + shadcn/ui theme tối giản chuyên nghiệp cho Enterprise SaaS.
2. **API Client & Auth Interceptor:**
   - Gắn `Authorization: Bearer <access_token>` và `X-Tenant-ID` (khi chuyển workspace).
   - Cơ chế **Single-Flight Refresh Queue**: khi nhận `401`, tạm giữ các request song song, gọi `POST /api/v1/auth/refresh` đúng 1 lần rồi replay request; nếu refresh thất bại (reuse/revoked) thì xóa session và điều hướng về `/login`.
3. **Màn hình Nghiệp vụ & RBAC:**
   - `/login`, `/register` (`POST /api/v1/auth/login`, `/register` tạo doanh nghiệp mới).
   - App Shell + Sidebar phân quyền động theo vai trò từ `GET /api/v1/auth/me`.
   - Trang Quản lý Phòng ban & Nhân sự (`GET/POST /api/v1/departments`, `POST /api/v1/tenants/members`) cho `ADMIN`/`OWNER`.
   - Trang Quản trị Tenants (`GET /api/v1/tenants`, `suspend`, `activate`, `PATCH quota`) chỉ mở cho `is_superuser`.

---

## 2. CHECKLIST NGHIỆM THU SPRINT F1
- [ ] Đăng ký doanh nghiệp + đăng nhập + đăng xuất (`POST /auth/logout`) hoạt động thông suốt.
- [ ] Token hết hạn tự động xoay vòng qua `/auth/refresh` không làm rớt thao tác người dùng.
- [ ] User thường truy cập route Super-Admin hoặc Admin bị chặn ngay ở Route Guard và hiển thị `403 Forbidden` rõ ràng.
