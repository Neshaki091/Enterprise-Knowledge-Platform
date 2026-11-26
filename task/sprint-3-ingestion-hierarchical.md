# SPRINT 3: Zero-Storage Ingestion, SHA-256 Hashing & Hierarchical Chunking

**Mục tiêu:** Xây dựng pipeline tải lên tài liệu theo mô hình không lưu file gốc (Zero Raw Storage), tính mã băm SHA-256 chống trùng lặp, và chia nhỏ tài liệu theo cấu trúc phân cấp Parent-Child (Hierarchical Chunking).  
**Ước tính thời gian:** 4 - 6 ngày  
**Trạng thái:** ✅ Đã hoàn thành Implementation - Chờ Developer Review & Defense  

---

## 1. PHÂN CHIA NHIỆM VỤ ĐÃ THỰC HIỆN

### AI Agent đã hoàn thành (Implementer):
1. **Model Thực thể CSDL ([backend/app/models/document.py](backend/app/models/document.py) & [backend/app/models/chunk.py](backend/app/models/chunk.py)):**
   - Bảng `documents`: lưu `tenant_id`, `file_hash`, `raw_text`, `token_count`, metadata. Ràng buộc `UniqueConstraint("tenant_id", "file_hash")`.
   - Bảng `document_parent_chunks`: lưu các đoạn văn lớn (~1000 tokens) kèm tiêu đề `heading`.
   - Bảng `document_child_chunks`: lưu các đoạn văn con (~200 tokens) có `parent_chunk_id` và trường `faiss_id`.
   - Migration: [0003_add_documents_and_chunks.py](backend/alembic/versions/0003_add_documents_and_chunks.py).
2. **Ephemeral Document Parsers ([backend/app/services/parsers.py](backend/app/services/parsers.py)):**
   - Tính mã băm SHA-256 trong bộ nhớ.
   - Bóc tách văn bản trong RAM với BytesIO (PDF, Word, Excel, Plain Text).
   - Chuẩn hóa văn bản: Loại bỏ ký tự NUL (`\x00`), dọn dẹp khoảng trắng, giải phóng buffer nhị phân ngay lập tức.
3. **Hierarchical Chunking Engine ([backend/app/services/chunking_service.py](backend/app/services/chunking_service.py)):**
   - Thuật toán phân đoạn dựa trên Heading / Trang / Đoạn văn (Parent ~1000 tokens).
   - Thuật toán cửa sổ trượt phân tách Child (~200 tokens) kèm gối đầu (overlap ~40 tokens).
   - Ước tính token siêu nhẹ không nạp mô hình tokenizer vào RAM.
4. **Service & API Endpoints ([backend/app/services/document_service.py](backend/app/services/document_service.py) & [backend/app/api/v1/endpoints/documents.py](backend/app/api/v1/endpoints/documents.py)):**
   - `POST /api/v1/documents/upload`: Tải file, băm hash, parse, chunk và lưu trữ.
   - `GET /api/v1/documents`: Lấy danh sách tài liệu trong phạm vi Tenant.
   - `GET /api/v1/documents/{id}`: Xem chi tiết tài liệu kèm cây phân đoạn Parent-Child.
   - `DELETE /api/v1/documents/{id}`: Xóa tài liệu và cascade toàn bộ phân đoạn.
5. **Kiểm thử tự động ([backend/tests/test_document_api.py](backend/tests/test_document_api.py)):**
   - 6 test cases toàn diện, nâng tổng số test suite lên **22/22 PASSED**!

---

## 2. CHECKLIST NGHIỆM THU SPRINT 3
- [x] Tải lên tài liệu văn bản: Văn bản được trích xuất đầy đủ, ổ đĩa không tăng dung lượng file nhị phân (Zero Raw Storage).
- [x] Thử tải lại đúng file đó: Hệ thống báo lỗi trùng lặp `409 Conflict` nhờ mã băm SHA-256.
- [x] Trong CSDL có quan hệ rõ ràng giữa bảng `document_parent_chunks` và `document_child_chunks`.
- [x] Xóa tài liệu tự động cascade xóa sạch các chunks liên kết.
- [x] Ngăn chặn BOLA: Tenant khác không thể đọc hoặc xóa tài liệu trái phép.

---

## 3. NHIỆM VỤ CỦA DEVELOPER (REVIEW & DEFENSE)
1. Rà soát file [query_for_app_owner.md](query_for_app_owner.md) để nắm được Bug 05 (MissingGreenlet) và bài toán tối ưu Eager Loading.
2. Trả lời câu hỏi bảo vệ Sprint 3: *Tại sao Hierarchical Chunking giải quyết được nghịch lý giữa độ chính xác của vector search và ngữ cảnh của LLM?*
