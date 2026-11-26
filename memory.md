# memory.md --- Trạng thái Bộ nhớ Dự án & Nhật ký Quyết định Kiến trúc (ADRs)

**Dự án:** Enterprise AI Knowledge Platform (SaaS Multi-tenant)  
**Phiên bản Hiện tại:** 2.1 (Baseline Approved)  
**Cập nhật lần cuối:** 2026-11-26  
**Mục tiêu:** Backend Core hoàn chỉnh (Phase 1) ➔ Enterprise Frontend & Realtime Admin Console (Phase 2, đã hoàn thành) ➔ Synthetic Workload theo giờ doanh nghiệp, Pentest & Vận hành thật (Phase 3).  
**Hạ tầng mục tiêu:** GCP Always Free `e2-micro` (1.0 GB RAM, ổ đĩa 50GB) + Swapfile 4GB.  

---

## 1. NHẬT KÝ QUYẾT ĐỊNH KIẾN TRÚC (ARCHITECTURE DECISION RECORDS - ADRs)

### ADR-001: Chọn Python 3.11+ / FastAPI làm Ngôn ngữ Backend chính
- **Bối cảnh:** Cân nhắc giữa Go, Rust và Python để cân đối giữa mức tiêu thụ RAM và cơ hội việc làm.
- **Quyết định:** Chọn **Python 3.11+ / FastAPI (chạy 1 Uvicorn worker)**.
- **Lý do & Trade-off:** Hơn 85-90% doanh nghiệp giải pháp AI tuyển Python. Toàn bộ hệ sinh thái SDK, parser, và FAISS hoạt động native. Mức tiêu thụ RAM của 1 worker FastAPI chỉ ~120MB–180MB, hoàn toàn nằm trong ngân sách 1.0GB của GCP e2-micro khi có Swap.

### ADR-002: Gom toàn bộ Cơ sở Dữ liệu về 1 PostgreSQL 16 duy nhất (Không dùng Redis/Celery/MinIO)
- **Bối cảnh:** Cần tiết kiệm RAM tối đa trên máy chủ 1GB.
- **Quyết định:** Chỉ chạy duy nhất **PostgreSQL 16 Low-Memory Profile** (`shared_buffers = 128MB`, `max_connections = 20`).
- **Lý do & Trade-off:** PostgreSQL gánh trọn 5 vai trò (Relational, FTS Sparse Search, Job Queue qua `SKIP LOCKED`, Token Revocation qua bảng `UNLOGGED`, và Text Storage). Tiết kiệm ~400MB RAM lẽ ra phải cấp cho Redis, Celery và MinIO.

### ADR-003: Áp dụng Mô hình Zero Raw Storage (Ephemeral Ingestion)
- **Bối cảnh:** Giới hạn đĩa cứng 50GB cho 25.000 tài liệu.
- **Quyết định:** Không lưu file nhị phân PDF/Word gốc. Chỉ tính mã băm SHA-256 (`file_hash` chống trùng lặp), trích xuất văn bản sạch (`raw_text`) lưu vào PostgreSQL, rồi hủy ngay file nhị phân.
- **Lý do & Trade-off:** Giải phóng 50% dung lượng đĩa (toàn bộ dữ liệu chỉ tốn ~15GB/50GB, dư >25GB an toàn); triệt tiêu hoàn toàn rủi ro rò rỉ file gốc và presigned URLs.

### ADR-004: Sử dụng FAISS với cờ `mmap` (`IO_FLAG_MMAP`) cho Vector Engine
- **Bối cảnh:** Vector index của 500.000 chunks thường ngốn 3GB–5GB RAM nếu nạp vào bộ nhớ.
- **Quyết định:** Dùng **FAISS mmap**, lưu file index tách biệt theo từng tenant: `storage/indexes/{tenant_id}.index`.
- **Lý do & Trade-off:** File index nằm trên đĩa SSD, OS tự động ánh xạ bộ nhớ ảo. Mức chiếm RAM Heap của Python gần như 0MB! Đảm bảo cách ly vật lý tuyệt đối giữa các tenant.

### ADR-005: Áp dụng Hierarchical Chunking (Parent-Child) & Semantic Distance Chunking
- **Bối cảnh:** Chunk nhỏ thiếu ngữ cảnh; chunk to làm loãng vector; dùng LLM chunking thì dính trần 30 RPM của Gemma 4 26B.
- **Quyết định:** Chia Parent Chunks (~1.000 tokens) lưu trong CSDL và Child Chunks (~200 tokens) nạp vào FAISS mmap. Cắt đoạn dựa trên độ sụt giảm khoảng cách Cosine giữa các câu và cấu trúc `# Heading` (không dùng LLM để chunking).

### ADR-006: Xây dựng Context Filtering & Prompt Compression Pipeline
- **Bối cảnh:** Giới hạn 16K TPM của Gemma 4 26B khiến RAG truyền thống dễ bị lỗi HTTP 429 sau vài câu hỏi.
- **Quyết định:** Lọc ngữ cảnh 3 tầng (Dynamic Threshold > 0.65 ➔ Cross-chunk Deduplication ➔ Sentence-level Compression) trước khi gửi prompt tới LLM.
- **Lý do & Trade-off:** Giảm 70% prompt tokens (từ ~3.500 xuống < 1.200 tokens), tăng tốc phản hồi TTFT < 1.5s, phục vụ được đồng thời 12–15 queries/phút.

### ADR-007: Tự tay viết Custom MCP Server (Hand-coded MCP Tools)
- **Bối cảnh:** Cần kết nối hệ thống tri thức với các AI Agent bên ngoài (Claude Desktop, Antigravity, Custom Agents) theo chuẩn Model Context Protocol (MCP).
- **Quyết định:** Tự tay viết MCP Server qua giao thức JSON-RPC 2.0 (hỗ trợ Stdio và SSE transport), kiểm soát chặt chẽ xác thực Tenant Context và phân quyền trước khi thực thi Tool (`search_knowledge_base`, `get_document_context`).

### ADR-008 (điều chỉnh 2026-11-26): Synthetic Business-Hours Workload thay vì 10.000 VU
- **Bối cảnh:** Kế hoạch ban đầu mô phỏng 10.000 VU không khả thi trên GCP Always Free. Số đo thực tế: 10.000 user/ngày × 300–400 request ≈ 3–4 triệu request/ngày; riêng HTTP header (~300 B/response) đã tốn 27–36 GB/tháng, vượt 1 GB egress 27–36 lần. Quota 30 RPM/tenant chỉ chịu ~43.200 request/ngày, cần 70–93 tenant để đạt mức đó.
- **Quyết định:** Chuyển sang **20 user/ngày, 3–5 phiên đồng thời, mỗi user 200 request/giờ** (1–2 giờ/phiên ➔ 4.000–8.000 request/ngày), chạy 8h–17h, trần 25 RPM, chat ≤ 8 lượt/phút; **soak gộp 5 ngày làm việc với chính workload này**, không chạy 24/7 riêng. Egress mục tiêu 0,6–0,8 GB/tháng (chat ≤ 300 lượt/ngày, admin realtime ≤ 1 tab-hour/ngày). Load generator k6 chạy trên laptop client, bắn qua Internet.
- **Lý do & Trade-off:** Tải mô phỏng vừa sức với hạ tầng 1 GB, đo được điểm bão hòa thật (30 RPM và 16K TPM) mà không đốt egress. Con số 10.000 VU chỉ giữ lại làm bài toán lý thuyết/phản biện về Configured VUs vs Concurrent Sessions vs RPS vs lượt gọi LLM.

---

## 2. TRẠNG THÁI HIỆN TẠI CỦA DỰ ÁN

| Hạng mục | Trạng thái | Ghi chú |
|---|---|---|
| **Tài liệu Yêu cầu (`SR.md`, `SR (1).md`)** | ✅ Đã hoàn thành | Chuẩn hóa v2.1 với đầy đủ tiêu chí nghiệm thu |
| **Cẩm nang Kỹ thuật (`ANON.md`)** | ✅ Đã hoàn thành | Chuẩn hóa kiến trúc Ultra-Lean & Security Catalog |
| **Quy chuẩn Agent (`AGENTS.md`)** | ✅ Đã hoàn thành | Thiết lập phương pháp "Tự code lần đầu, hỗ trợ lần sau" |
| **Kế hoạch Bảo vệ (`DEFENSE_PLAN.md`)** | ✅ Đã hoàn thành | 25 câu hỏi phản biện 3 vai trò (Giảng viên, Tuyển dụng, Khách hàng) |
| **Phân rã Sprint Tasks (`task/`)** | ✅ Đã hoàn thành | 8 Sprints Phase 1 + 4 Sprints Frontend F1–F4 (Phase 2) + 2 Sprints Load Test/Pentest 9–10 (Phase 3) |
| **Sprint 1 (Foundation DB & Tenant Slice)** | ✅ Đã hoàn thành | Full Vertical Slice (DB -> Service -> API -> 9/9 Tests PASSED) |
| **Sprint 2 (Auth & Tenant Context)** | ✅ Đã hoàn thành | Full Vertical Slice (Security -> Service -> API -> 16/16 Tests PASSED) |
| **Sprint 3 (Ingestion & Hierarchical Chunking)** | ✅ Đã hoàn thành | Full Vertical Slice (Parsers -> Chunking -> API -> 22/22 Tests PASSED) |
| **Sprint 4 (FAISS mmap & Hybrid Search RRF)** | ✅ Đã hoàn thành | Full Vertical Slice (Embedding -> FAISS mmap -> FTS -> RRF -> 24/24 Tests PASSED) |
| **Sprint 5 (Custom Hand-Coded MCP Server)** | ✅ Đã hoàn thành | Full Vertical Slice (JSON-RPC 2.0 -> Tools Registry -> Stdio/SSE -> 28/28 Tests PASSED) |
| **Sprint 6 (Context Filtering & Gemma RAG)** | ✅ Đã hoàn thành | Full Vertical Slice (Token Bucket 30 RPM -> 3-Tier Filter -> Grounded RAG -> 31/31 Tests PASSED) |
| **Sprint 7 (3-Tier Dashboards & Streaming SSE)** | ✅ Đã hoàn thành | Full Vertical Slice (SSE Stream -> Feedbacks -> 3 Dashboards -> 34/34 Tests PASSED) |
| **Sprint 8 (Docker, GCP 1GB Setup & Staging)** | ✅ Đã hoàn thành | Multi-stage Dockerfile, Docker Compose, Nginx, Swap 4GB Script, CI/CD |
| **PHASE 1 (CORE SAAS BUILD)** | 🏆 HOÀN TẤT 100% | Toàn bộ 8 Sprints nền tảng hoàn thành; bộ pytest hiện tại 43/43 PASSED |
| **PHASE 2 (ENTERPRISE FRONTEND F1–F4)** | ✅ HOÀN THÀNH | React SPA, Knowledge Hub/RAG SSE, 3-Tier Dashboards, Docker/Nginx; Playwright E2E 2/2 PASS, Nginx healthy (2026-11-26) |
| **PHASE 3 (SYNTHETIC WORKLOAD, PENTEST & OPS)** | 🟢 SẴN SÀNG TRIỂN KHAI | Sprint 9 (20 user/ngày × 400 req, k6, saturation/soak) & Sprint 10 (Pentest, vận hành 4 tuần trên VPS thật, portfolio) |

---

## 3. THÔNG SỐ CẤU HÌNH HẠ TẦNG & CỔNG MẠNG (PORTS & PATHS)

- **GCP Server:** Always Free `e2-micro`, Ubuntu 22.04 LTS Minimal.
- **Phase 3 Ops Target:** Frontend SPA trên Vercel (`*.vercel.app`) + backend GCP `e2-micro` Always Free với Nginx TLS cho 4 tuần vận hành, giữ egress ≤ 0,8 GB/tháng; backend hostname dùng DuckDNS + Let's Encrypt (khuyến nghị) hoặc chứng chỉ IP `shortlived` (~6 ngày, cần IP tĩnh + ACME client hỗ trợ). SPA HTTPS không gọi được `http://ExIP` (mixed content); gọi thẳng ExIP:port chỉ hợp lệ từ trang HTTP, Postman/curl hoặc k6. Dự phòng VPS 1–2 GB (Singapore/Vietnam). Soak gộp trong 5 ngày làm việc/4 tuần vận hành, không chạy 24/7 riêng. Bộ triển khai một lần đã có trong repo: `deploy/README.md` + script DuckDNS/TLS; `docker-compose.prod.yml` (ACME/TLS) + `frontend/vercel.json` (SPA fallback/CSP).
- **Swapfile:** `/swapfile` (4.0 GB).
- **PostgreSQL 16:** Cổng `5432` nội bộ, database `saas_knowledge_db`.
- **FastAPI Backend:** Cổng `8000` (chạy qua Uvicorn async).
- **FAISS Storage Directory:** `backend/storage/indexes/{tenant_id}.index`.
- **Gemma 4 26B API:** Cloud Inference Endpoint (Quota: 30 RPM, 16K TPM, 14.4K RPD).
- **LLM Model:** một model tại một thời điểm qua `LLM_MODEL` (mặc định `gemma-4-26b-a4b-it`; có thể chuyển Gemini Flash Lite hoặc Gemma 4 32B nếu provider phục vụ đúng model ID). Chưa có routing chọn model theo request.
- **Embedding API:** `gemini-embedding-001` (786 dimensions, batch tối đa 10 chunks/call).
- **Endpoint Test Report:** `endpoint_test/endpoint_test_report_<YYYY-MM-DD>.md` (ngày local của máy chạy; header ghi thời gian UTC).
