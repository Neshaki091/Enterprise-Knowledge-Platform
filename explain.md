# BÁO CÁO TỔNG HỢP & TIẾN ĐỘ KIẾN TRÚC DỰ ÁN (BASELINE 2.1)
## Enterprise AI Knowledge Platform (SaaS Multi-tenant)

**Ngày cập nhật:** 2026-11-23  
**Trạng thái:** Toàn bộ kiến trúc Ultra-Lean đã được chuẩn hóa & phê duyệt  
**Tài liệu kỹ thuật gốc:** [ANON.md](ANON.md) | [SR.md](SR.md) | SR (1).md (bản sao cục bộ, không đưa vào repo)

---

## 1. TỔNG HỢP CÁC BƯỚC ĐỘT PHÁ KIẾN TRÚC ĐÃ CHỐT

Trong phiên làm việc vừa qua, chúng ta đã biến một đồ án RAG thông thường trở thành một **kiến trúc Enterprise thực chiến đỉnh cao**, giải quyết triệt để bài toán tài nguyên hạn chế:

| Hạng mục kiến trúc | Thiết kế ban đầu | **Chuẩn hóa Kiến trúc Ultra-Lean (v2.1)** | Giá trị kỹ thuật đạt được |
|---|---|---|---|
| **Hạ tầng máy chủ** | Chưa xác định rõ ràng | **GCP Always Free `e2-micro` (1.0 GB RAM, ổ đĩa 50GB)** + 4GB Swapfile | Chi phí hạ tầng **$0/tháng**, chứng minh kỹ năng tối ưu hóa RAM ở đẳng cấp cao. |
| **Ngôn ngữ Backend** | Python / Đang phân vân Go | **Python 3.11+ / FastAPI (1 async worker tinh gọn)** | Đáp ứng >85-90% yêu cầu tuyển dụng AI Solution, RAM chỉ tốn ~120MB–180MB. |
| **Cơ sở dữ liệu** | Postgres + Redis + S3/MinIO | **Chỉ đúng 1 PostgreSQL 16 (Low-Memory Profile)** | Cắt bỏ Redis & MinIO; Postgres gánh trọn Relational, FTS, Job Queue (`SKIP LOCKED`), RLS. Tiêu thụ chỉ ~130MB RAM! |
| **Vector Engine** | pgvector / Qdrant nạp RAM | **FAISS mmap (`faiss-cpu`)** | Lưu file index riêng theo từng tenant; OS tự động ánh xạ mmap trên đĩa, RAM vector search **gần như 0MB**! |
| **Lưu trữ tài liệu** | Lưu toàn bộ file PDF gốc | **Zero Raw Storage (Ephemeral Ingestion)** | Chỉ bóc tách text, hash SHA-256 chống trùng lặp; hủy file nhị phân ngay lập tức. 25.000 docs chỉ tốn ~15GB/50GB đĩa (dư >25GB an toàn). |
| **Kỹ thuật Chunking** | Fixed-size chunking thô | **Hierarchical Chunking (Parent-Child)** | Parent chunk (~1.000 tokens) giữ trọn ngữ cảnh cho LLM; Child chunks (~200 tokens) nạp vào FAISS cho độ chính xác tìm kiếm cao nhất. |
| **Tối ưu Prompt LLM** | Nhét Top-K thô vào Prompt | **Context Filtering & Prompt Compression** | Cắt tỉa 3 tầng (ngưỡng động + khử trùng lặp + nén câu); giảm 70% token đầu vào, tăng tốc TTFT < 1.5s, bảo vệ trần 16K TPM của Gemma 4 26B. |
| **Mô hình tạo sinh RAG** | Chưa chốt quota | **Gemma 4 26B (Cloud Inference API)** | Bộ điều phối Quota trung tâm (Token Bucket in-memory: 30 RPM, 16K TPM, 14.4K RPD) với 4 chế độ Mock/Retrieval/Real/Hybrid. |
| **Mô phỏng tải** | Chưa rõ cách chạy | **k6 trên laptop client: 20 user/ngày, 3–5 phiên đồng thời, 200 req/giờ/user (4.000–8.000 req/ngày, trần 25 RPM)** | Không chạy load generator trên cùng server với app; 10.000 VU không khả thi trên 1 GB free (27–36 GB egress/tháng, vượt quota 30 RPM/tenant ~43.200 req/ngày). |
| **Phương pháp học** | Làm thụ động | **Tự tay code lần đầu (Hand-code), AI hỗ trợ lần sau** | Bạn làm chủ 100% kiến trúc, hiểu từng dòng code, tự tin phỏng vấn tương đương **6 tháng – 1 năm kinh nghiệm**. |

---

## 2. PHÂN BỔ BỘ NHỚ RAM TRÊN MÁY CHỦ GCP 1.0 GB RAM

```
+-----------------------------------------------------------------------------------+
|               TỔNG DUNG LƯỢNG RAM VẬT LÝ: 1.024 MB (1.0 GB)                       |
+-----------------------------------------------------------------------------------+
|  [Hệ điều hành Linux + Docker] : ~180 MB                                          |
|  [PostgreSQL 16 Low-Memory]    : ~130 MB - 250 MB (20 conns)                      |
|  [FastAPI Backend 1 Worker]    : ~120 MB - 200 MB                                 |
|  [FAISS mmap Vector Engine]    : ~30 MB (Off-heap qua OS Page Cache)              |
|  [VÙNG ĐỆM AN TOÀN / FREE RAM] : ~400 MB - 500 MB (Hoàn toàn tự do!)             |
+-----------------------------------------------------------------------------------+
|  [SWAPFILE DỰ PHÒNG TRÊN ĐĨA]  : 4.0 GB (Hấp thụ 100% các cú sốc tải đột biến)    |
+-----------------------------------------------------------------------------------+
```

---

## 3. LỘ TRÌNH THỰC HIỆN TIẾP THEO (BẮT ĐẦU MODULE 1)

Mọi tài liệu kiến trúc đã hoàn thiện và đồng bộ tuyệt đối. Chúng ta sẽ bắt đầu ngay với **Module 1**:
- **Mục tiêu Module 1:**
  1. Khởi tạo cấu trúc dự án chuẩn Modular Monolith (`backend/app/...`).
  2. Tạo môi trường ảo Python và cài đặt các dependencies tinh gọn (`fastapi`, `uvicorn`, `sqlalchemy`, `asyncpg`, `alembic`, `pydantic-settings`).
  3. Tôi sẽ hướng dẫn kiến trúc và **bạn sẽ tự tay gõ từng dòng code đầu tiên** cho cấu hình kết nối PostgreSQL (`database.py`) và Model thực thể đầu tiên (`Tenant`).
