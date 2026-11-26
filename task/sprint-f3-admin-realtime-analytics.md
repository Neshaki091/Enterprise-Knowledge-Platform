# SPRINT F3 (PHASE 2): 3-Tier Dashboards, Realtime Analytics & Storage/Quota Console

**Mục tiêu:** Xây dựng bảng điều khiển trực quan cho Employee, Business Tenant Admin và Platform Super-Admin, kết nối luồng SSE realtime giám sát lưu trữ DB vs FAISS, hạn mức API Key/Model và Audit Logs.  
**Ước tính thời gian:** 1 - 1.5 tuần  
**Trạng thái:** ✅ Hoàn thành

---

## 1. PHÂN CHIA NHIỆM VỤ

1. **Employee Workspace (`/workspace`):**
   - Kết nối `GET /api/v1/dashboards/workspace` hiển thị phòng ban, vai trò và số lượng tài liệu được phép tra cứu.
2. **Business Tenant Admin Console (`/admin/tenant`):**
   - Kết nối `GET /api/v1/dashboards/tenant`, `/tenant/analytics`, `/tenant/logs` và stream `GET /api/v1/dashboards/tenant/realtime`.
   - Biểu đồ tách bạch **Database Storage** (`uploaded_files_bytes`, `db_raw_text_bytes`, `db_chunks_text_bytes`) vs **FAISS `.index` trên đĩa** (`faiss_index_bytes`, `faiss_vectors_count`, `786`-dim).
   - Đồng hồ đo **Live Quota** (`30 RPM` / `16,000 TPM` còn lại), độ trễ trung bình/P95, tỷ lệ nén token và bảng lọc **Audit Logs** (`event_type`, `status_code`).
3. **Platform Super-Admin Console (`/admin/platform` — Tuân thủ SEC-026):**
   - Kết nối `GET /api/v1/dashboards/platform`, `/platform/analytics`, `/platform/logs`, `/platform/realtime` và `/api/v1/health`.
   - Bảng phân tích từng Tenant (dung lượng DB/FAISS, vectors, RPM/TPM còn lại, số event) — tuyệt đối không hiển thị văn bản tài liệu hay nội dung chat.
   - Panel **Model & API Key Health**: hiển thị key đã che (`AIza...xxxx`), limit của `gemma-4-26b-a4b-it` & `gemini-embedding-001`, bộ đếm `2xx / 429 / 5xx` theo thời gian thực.

---

## 2. CHECKLIST NGHIỆM THU SPRINT F3
- [ ] Dashboard tự cập nhật số liệu thời gian thực qua SSE không cần F5 trang.
- [ ] Hiển thị chính xác dung lượng DB vs FAISS từng Tenant và thanh tiến trình 30 RPM / 16K TPM.
- [ ] Đảm bảo Platform Super-Admin Console không lộ bất kỳ nội dung văn bản thô nào của doanh nghiệp (SEC-026).
