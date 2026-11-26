# SPRINT 4: FAISS mmap Engine & PostgreSQL FTS Hybrid Search (RRF)

**Mục tiêu:** Xây dựng bộ máy tìm kiếm lai (Hybrid Search) kết hợp giữa tìm kiếm ngữ nghĩa siêu tiết kiệm RAM bằng FAISS mmap và tìm kiếm từ khóa chính xác bằng PostgreSQL Full-Text Search qua thuật toán Reciprocal Rank Fusion (RRF).  
**Ước tính thời gian:** 4 - 6 ngày  
**Trạng thái:** ✅ Đã hoàn thành Implementation - Chờ Developer Review & Defense  

---

## 1. PHÂN CHIA NHIỆM VỤ ĐÃ THỰC HIỆN

### AI Agent đã hoàn thành (Implementer):
1. **Embedding Service ([backend/app/services/embedding_service.py](backend/app/services/embedding_service.py)):**
   - Sinh vector 1024 chiều (BAAI/bge-m3) chuẩn hóa L2.
   - Hỗ trợ deterministic offline mock tiết kiệm 0MB Heap RAM và gọi Inference API bên ngoài.
2. **FAISS mmap Storage Manager ([backend/app/services/faiss_service.py](backend/app/services/faiss_service.py)):**
   - Quản lý index riêng cho từng tenant: `backend/storage/indexes/{tenant_id}.index`.
   - Tìm kiếm với cờ `faiss.IO_FLAG_MMAP` giúp giải phóng RAM vào OS page cache.
   - Chạy trên `asyncio.to_thread` không block event loop.
3. **Reciprocal Rank Fusion (RRF) Algorithm ([backend/app/services/rrf_service.py](backend/app/services/rrf_service.py)):**
   - Công thức chuẩn hóa $k=60$, hợp nhất Dense Ranking và Sparse Ranking.
4. **Hybrid Search Service & API ([backend/app/services/search_service.py](backend/app/services/search_service.py) & [backend/app/api/v1/endpoints/search.py](backend/app/api/v1/endpoints/search.py)):**
   - Endpoint `POST /api/v1/search/hybrid` kết hợp FAISS + FTS + RRF.
   - Truy xuất phân cấp (Hierarchical Retrieval): Tìm theo Child Chunk nhưng trả về Parent Chunk giàu ngữ cảnh.
5. **Kiểm thử tự động ([backend/tests/test_search_api.py](backend/tests/test_search_api.py)):**
   - Đạt **24/24 Tests PASSED** trên toàn bộ hệ thống!

---

## 2. CHECKLIST NGHIỆM THU SPRINT 4
- [x] Tìm kiếm từ ngữ đồng nghĩa (ngữ nghĩa) trả về đúng tài liệu nhờ FAISS mmap.
- [x] Tìm kiếm số hiệu văn bản chính xác (ví dụ: `QD-8888/QD-AI`) trả về đúng tài liệu nhờ FTS.
- [x] Thuật toán RRF hợp nhất 2 nguồn mượt mà; mức chiếm dụng RAM của tiến trình Python không tăng.
- [x] Cách ly tuyệt đối giữa các tenant: Tenant B không thể tra cứu dữ liệu của Tenant A.
