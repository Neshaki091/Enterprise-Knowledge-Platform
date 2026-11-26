# SPRINT 2: Authentication, JWT Rotation & Tenant Context Middleware

**Mục tiêu:** Xây dựng hệ thống xác thực người dùng, bảo vệ mật khẩu (bcrypt), cấp phát cặp Access/Refresh Token với cơ chế xoay vòng (Rotation) và Middleware trích xuất Tenant Context an toàn.  
**Ước tính thời gian:** 4 - 6 ngày  
**Trạng thái:** ✅ Đã hoàn thành Implementation - Chờ Developer Review & Defense  

---

## 1. PHÂN CHIA NHIỆM VỤ ĐÃ THỰC HIỆN

### AI Agent đã hoàn thành (Implementer):
1. **Module Bảo mật & Mật mã ([backend/app/core/security.py](backend/app/core/security.py)):**
   - Triển khai Bcrypt password hashing (`hash_password`, `verify_password`).
   - Triển khai PyJWT generation & decode với JTI (UUID duy nhất) cho cả Access Token (15 phút) và Refresh Token (7 ngày).
2. **Quản lý Thu hồi & Model Token ([backend/app/models/token.py](backend/app/models/token.py)):**
   - Bảng `revoked_tokens` lưu `jti`, `token_type`, `user_id`, `expires_at`, `reason`.
   - Migration Alembic: `0002_add_revoked_tokens.py`.
3. **Tenant Context & Phân quyền ([backend/app/core/context.py](backend/app/core/context.py)):**
   - Dataclass bất biến `TenantContext(user_id, tenant_id, role, department_id, email, is_superuser)`.
   - FastAPI Dependency `get_current_tenant_context` đối soát nghiêm ngặt `Membership` trong DB để chặn tấn công BOLA/IDOR (SEC-004).
   - Dependency factory `require_roles(allowed_roles)` kiểm soát RBAC.
4. **Service & Endpoints ([backend/app/services/auth_service.py](backend/app/services/auth_service.py) & [backend/app/api/v1/endpoints/auth.py](backend/app/api/v1/endpoints/auth.py)):**
   - `/api/v1/auth/register`: Đăng ký tài khoản + tạo Tenant ban đầu + cấp quyền OWNER.
   - `/api/v1/auth/login`: Xác thực mật khẩu + cấp Token pair.
   - `/api/v1/auth/refresh`: Cơ chế Xoay vòng (Token Rotation) & Phát hiện Tái sử dụng (Reuse Detection).
   - `/api/v1/auth/logout`: Đưa JTI vào blacklist thu hồi.
   - `/api/v1/auth/me`: Trả về Tenant Context hiện tại.
5. **Bộ kiểm thử tự động toàn diện ([backend/tests/test_auth_api.py](backend/tests/test_auth_api.py)):**
   - 7 test cases kiểm thử an ninh chuyên sâu (16/16 tests toàn dự án đều PASSED).

---

## 2. CHECKLIST NGHIỆM THU SPRINT 2
- [x] Đăng nhập thành công trả về access token và refresh token.
- [x] Kiểm thử refresh token reuse: Sử dụng lại refresh token cũ lập tức bị phát hiện và chặn với lỗi 401.
- [x] Thử nghiệm gửi token giả mạo bị từ chối 401/403.
- [x] Tấn công BOLA/IDOR giả mạo `X-Tenant-ID` bị chặn đứng với lỗi 403 Forbidden.
- [x] Đăng xuất thu hồi token thành công: các request tiếp theo bị từ chối 401.

---

## 3. NHIỆM VỤ CỦA DEVELOPER (REVIEW & DEFENSE)
1. **Code Review:** Rà soát toàn bộ vertical slice Auth & Tenant Context.
2. **Thẩm định & Trả lời chất vấn:** Trả lời 3 câu hỏi bảo vệ của Giảng viên & Tech Lead:
   - *Câu 1:* Tại sao không bao giờ được tin tưởng header `X-Tenant-ID` do client gửi lên mà bắt buộc phải truy vấn đối soát bảng `memberships`?
   - *Câu 2:* Cơ chế Token Rotation hoạt động như thế nào, và hệ thống sẽ xử lý ra sao khi phát hiện một Refresh Token cũ đã bị xoay vòng đột nhiên được gửi lại (Reuse Detection)?
   - *Câu 3:* Tại sao bảng `revoked_tokens` lại cần cột `expires_at` và cơ chế dọn dẹp định kỳ (Pruning) hoạt động ra sao để tránh phình to CSDL?
