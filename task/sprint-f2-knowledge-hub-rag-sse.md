# SPRINT F2 (PHASE 2): Knowledge Hub, Hybrid Search & RAG Chat SSE

**Mục tiêu:** Xây dựng toàn bộ trải nghiệm tri thức doanh nghiệp: Tải tài liệu (`.txt`, `.md`, `.pdf`, `.docx`, `.xlsx`), xem phân cấp Parent-Child Chunks, thử nghiệm Hybrid Search RRF và giao diện Chat AI streaming theo thời gian thực.  
**Ước tính thời gian:** 1 - 1.5 tuần  
**Trạng thái:** ✅ Hoàn thành

---

## 1. PHÂN CHIA NHIỆM VỤ

1. **Knowledge Base Management (`/documents`):**
   - Upload đa định dạng (`POST /api/v1/documents/upload`) kèm chọn `department_id`, hiển thị lỗi rõ ràng cho `400` (file hỏng/sai định dạng) và `409` (trùng SHA-256).
   - Danh sách tài liệu + Modal xem chi tiết phân cấp Parent/Child chunks (`GET /api/v1/documents/{id}`) và xóa đồng bộ vector (`DELETE /api/v1/documents/{id}`).
2. **Hybrid Search Explorer (`/search`):**
   - Giao diện tra cứu (`POST /api/v1/search/hybrid`) cho phép chỉnh `top_k`, lọc `department_id`, hiển thị điểm `rrf_score`, `dense_score` (Gemini 786d), `sparse_score` (FTS).
3. **RAG Streaming Chat UI (`/chat`):**
   - Dùng `fetch()` + `ReadableStream` đọc `POST /api/v1/chat/stream`.
   - **Hiển thị Chunks trước khi sinh câu trả lời:** Render ngay khối **Retrieved Chunks** (`event: chunks`) & **Citations** (`event: citations`) trước khi từng `token` của Gemma 4 26B hiện ra.
   - Hiển thị thống kê nén token (`compression_stats`: `original_tokens` vs `compressed_tokens`, `% tiết kiệm` dưới trần 16K TPM), nút chọn `mode` (`hybrid` mặc định / `real` / `retrieval_only`), xử lý đồng hồ đếm ngược khi dính `429 Retry-After`, và nút đánh giá `THUMBS_UP` / `THUMBS_DOWN` (`POST /api/v1/chat/feedback`).

---

## 2. CHECKLIST NGHIỆM THU SPRINT F2
- [ ] Upload PDF/DOCX/TXT/XLSX thành công và xem được cấu trúc Parent-Child Chunks.
- [ ] Giao diện Chat SSE hiển thị `retrieved_chunks` + `citations` ngay lập tức trước luồng `token`.
- [ ] Render Markdown an toàn (XSS sanitized) và trích dẫn nguồn bấm vào mở đúng đoạn tài liệu.
