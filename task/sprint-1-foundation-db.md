# SPRINT 1: Cấu trúc Dự án & PostgreSQL 16 Low-Memory

**Mục tiêu:** Thiết lập cấu trúc dự án Modular Monolith chuẩn công nghiệp, cấu hình kết nối CSDL PostgreSQL 16 chế độ siêu tiết kiệm RAM, và tạo migration bảng `Tenant`, `User`, `Department`, `Membership`.  
**Ước tính thời gian:** 3 - 5 ngày  
**Trạng thái:** ✅ Đã hoàn thành Implementation - Chờ Developer Review & Defense  

---

## 1. PHÂN CHIA NHIỆM VỤ ĐÃ THỰC HIỆN

### AI Agent đã hoàn thành (Implementer):
1. **Module `backend/app/core/database.py`:**
   - Cấu hình Async Engine tối ưu hóa RAM cho máy chủ 1.0GB (`pool_size=10`, `max_overflow=5`, `pool_pre_ping=True`).
   - Cấu hình `async_sessionmaker` với `expire_on_commit=False`.
   - Viết FastAPI Dependency `get_db()` với khối `async with` quản lý commit/rollback/close tự động.
2. **Hệ thống Models SQLAlchemy 2.0:**
   - `Tenant` ([backend/app/models/tenant.py](backend/app/models/tenant.py)): Root Entity cho multi-tenancy.
   - `User` ([backend/app/models/user.py](backend/app/models/user.py)): Identity model.
   - `Department` ([backend/app/models/department.py](backend/app/models/department.py)): Phân cấp phòng ban trong Tenant.
   - `Membership` ([backend/app/models/membership.py](backend/app/models/membership.py)): Quan hệ N-N giữa User và Tenant có gán Role và Department.
3. **Alembic Async Migrations:**
   - Cấu hình `backend/alembic.ini` và `backend/alembic/env.py` hỗ trợ asyncpg.
   - Tạo migration đầu tiên: `0001_initial` ([backend/alembic/versions/0001_initial.py](backend/alembic/versions/0001_initial.py)).
4. **Validation Schemas & Tests:**
   - Schemas Pydantic v2: `TenantCreate`, `TenantUpdate`, `TenantResponse`.
   - Unit tests: `backend/tests/test_models.py` kiểm tra cấu trúc quan hệ giữa các models.

---

## 2. NHIỆM VỤ CỦA DEVELOPER (REVIEW & DEFENSE)
1. **Code Review:** Rà soát toàn bộ mã nguồn của Sprint 1, kiểm tra các quyết định kiến trúc:
   - Tại sao `pool_size` lại đặt là 10 thay vì 100?
   - Tại sao `expire_on_commit` phải đặt là `False` trong môi trường async?
   - Tại sao bảng `memberships` lại có ràng buộc `UniqueConstraint('user_id', 'tenant_id')`?
2. **Thẩm định & Trả lời chất vấn:** Trả lời 3 câu hỏi bảo vệ của Giảng viên & Nhà tuyển dụng trước khi chuyển sang Sprint 2.
