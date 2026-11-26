# SR.md --- Enterprise Stakeholder Requirements Specification (SRS/BRD Level 1)

**Sản phẩm:** Enterprise AI Knowledge Platform (SaaS Multi-tenant)  
**Khách hàng giả lập:** ABC Technology (Tập đoàn công nghệ quy mô 300 - 1.500 nhân viên)  
**Hạ tầng triển khai mục tiêu:** Google Cloud Platform (GCP) Always Free `e2-micro` (1.0 GB RAM, 2 vCPU shared, ổ đĩa 50GB) + Swapfile 4GB.  
**Phiên bản:** 2.1 (Ultra-Lean Architecture, FAISS mmap, Context Filtering & Dual-Phase Roadmap)  
**Ngày cập nhật:** 2026-11-23  
**Trạng thái:** Baseline Approved  
**Tác giả:** System Architect & Lead BA  

---

## 1. Bối cảnh và Mục tiêu Doanh nghiệp

### 1.1 Bối cảnh & Thách thức
ABC Technology có nhiều chi nhánh và phòng ban (HR, IT, Sales, Finance, Legal, Operations, R&D). Dữ liệu tri thức bị phân mảnh nghiêm trọng. Nhân viên tốn 20-30% thời gian tìm kiếm chính sách và tài liệu nội bộ; nguy cơ rò rỉ dữ liệu nhạy cảm giữa các phòng ban là rất lớn.

Khách hàng muốn sở hữu một **Enterprise AI Knowledge Platform** dạng SaaS đa người thuê (Multi-tenant) hoàn chỉnh với các yêu cầu kỹ thuật đặc biệt:
1. **Kiến trúc siêu nhẹ (Ultra-Lean):** Tối ưu hóa tuyệt đối để vận hành toàn bộ hệ thống sản phẩm (Backend API + Cơ sở dữ liệu + Vector Engine) trên cấu hình máy chủ **GCP Always Free `e2-micro` (1.0 GB RAM)** mà không bị lỗi tràn bộ nhớ (Out-Of-Memory).
2. **Hệ cơ sở dữ liệu duy nhất (Single PostgreSQL 16):** Loại bỏ hoàn toàn các phụ thuộc cồng kềnh (Redis, Celery, MinIO). PostgreSQL gánh trọn vẹn: Dữ liệu quan hệ, Row-Level Security (RLS), Tìm kiếm từ khóa toàn văn (Full-Text Search), và Hàng đợi tác vụ ngầm (`SKIP LOCKED`).
3. **Mô hình Không Lưu File Gốc (Zero Raw Storage / Ephemeral Ingestion):** Tuyệt đối không lưu file nhị phân PDF/Word để tiết kiệm đĩa cứng và tăng cường bảo mật; chỉ lưu mã băm SHA-256 (`file_hash` chống trùng lặp), metadata và văn bản thuần sạch (`raw_text`) trong PostgreSQL. Toàn bộ 25.000 tài liệu chỉ chiếm ~15GB – 25GB trên ổ 50GB (dư >25GB an toàn).
4. **Vector Search Engine với FAISS mmap:** Ánh xạ index từ file đĩa SSD (`IO_FLAG_MMAP`) theo từng tenant (`storage/indexes/{tenant_id}.index`), mức chiếm dụng RAM ứng dụng < 50MB.
5. **Hierarchical Ingestion (Parent-Child Chunking):** Lưu Parent Chunk (~1.000 tokens) trong CSDL để cung cấp ngữ cảnh trọn vẹn cho LLM; nạp Child Chunks (~200 tokens) vào FAISS mmap để đạt độ chính xác tìm kiếm cao nhất.
6. **Context Filtering & Prompt Compression Pipeline:** Cắt tỉa ngữ cảnh đa tầng (Dynamic Thresholding + Deduplication + Sentence-Level Compression) giúp giảm 60–70% token đầu vào, tăng tốc độ phản hồi TPS và giải quyết triệt để bài toán hạn mức **16K TPM** của **Gemma 4 26B**.
7. **Lộ trình 2 giai đoạn (Dual-Phase Roadmap):**
   - **Phase 1 (Full SaaS Build & Deploy):** Xây dựng hoàn chỉnh SaaS core, bảo mật RLS, Hybrid Search RRF, RAG pipeline, 3 dashboards và đóng gói triển khai lên GCP e2-micro.
   - **Phase 2 (Synthetic User Simulation 10.000 VU & Vận hành 2 tháng):** Viết kịch bản k6/Locust từ máy laptop client mô phỏng 10.000 người dùng ảo bắn tải vào GCP; kiểm thử chịu tải (Load, Stress, Soak test 8h), kiểm thử bảo mật (Pentest OWASP API Top 10), và 2 tháng vận hành thực tế.

   > **Điều chỉnh 2026-11-26 (xem `task/ROADMAP.md`):** Kế hoạch hiện hành dùng synthetic workload **20 user/ngày, 3–5 phiên đồng thời, 200 request/giờ/user (4.000–8.000 request/ngày)**, 8h–17h, trần 25 RPM, egress 0,6–0,8 GB/tháng, soak gộp 5 ngày làm việc với chính workload này và vận hành 4 tuần trên VPS thật. Lý do: 10.000 user/ngày cần 3–4 triệu request/ngày, tối thiểu 27–36 GB egress/tháng (vượt 1 GB free 27–36 lần) và vượt quota 30 RPM/tenant (~43.200 request/ngày). Kế hoạch gốc được giữ làm bài toán lý thuyết/phản biện.

---

### 1.2 Ràng buộc và Giả định Kinh doanh (Business Constraints)

| Mã | Hạng mục | Ràng buộc / Giả định chuẩn hóa |
|---|---|---|
| **BC-001** | Quy mô người dùng ban đầu | Pilot 300 - 1.500 người dùng thật; kiến trúc backend sẵn sàng chịu tải mô phỏng 10.000 Virtual Users ở Phase 2. |
| **BC-002** | Quy mô Tenant năm đầu | Thiết kế hỗ trợ 5 - 20 doanh nghiệp độc lập hoạt động trên cùng nền tảng. |
| **BC-003** | Dung lượng tài liệu | **25.000 tài liệu** trên toàn hệ thống (~500.000 chunks); áp dụng Zero Raw Storage để tổng dung lượng chỉ tốn ~15GB – 25GB đĩa. |
| **BC-004** | Ngân sách phần cứng VPS | **GCP Always Free `e2-micro` (1.0 GB RAM, 2 vCPU shared, ổ đĩa 50GB)**; tạo Swapfile 4GB chống OOM. |
| **BC-005** | Ngân sách suy luận AI | Sử dụng mô hình tạo sinh **Gemma 4 26B** (Cloud Inference API) với Centralized Quota Manager (30 RPM, 16K TPM, 14.4K RPD) có Token Bucket in-memory. |
| **BC-006** | Cách ly dữ liệu (Isolation) | Tuyệt đối không rò rỉ chéo tenant (Cross-tenant leakage = 0) ở mọi tầng: API, Database RLS, FAISS mmap file tách biệt, Cache, Logs. |
| **BC-007** | Định vị kinh nghiệm (Portfolio) | Dự án thiết kế có chiều sâu kỹ thuật thực tế tương đương **6 tháng – 1 năm kinh nghiệm làm việc thực chiến** tại doanh nghiệp. |

---

## 2. Stakeholder Requirements (SR)

> **Quy ước mức ưu tiên:**  
> - **P0:** Bắt buộc phải có trong Phase 1 (MVP Core SaaS & Deployment Baseline).  
> - **P1:** Hoàn thiện nâng cao trong Phase 1 (Enterprise Features).  
> - **P2:** Mục tiêu Phase 2 (Synthetic User Simulation 10.000 VU, Load/Stress Testing & 2-Month Operations).

---

### Phân hệ 1: Multi-Tenancy & Quản trị Doanh nghiệp

#### SR-001: Quản trị Vòng đời Doanh nghiệp (Tenant Lifecycle)
- **Priority:** P0 | **Stakeholder:** Platform Admin | **Status:** Approved
- **Nội dung:** Platform Admin tạo mới, kích hoạt (Active), tạm khóa (Suspended) tenant. Khi tenant bị Suspended, toàn bộ user thuộc tenant đó bị từ chối truy cập ngay lập tức tại middleware, trả về `403 Forbidden` (`TENANT_SUSPENDED`).
- **Nghiệm thu:** Request từ user của tenant Suspended bị chặn trong < 1 giây.

#### SR-002: Cách ly Dữ liệu Đa người thuê (Multi-Tenant Isolation)
- **Priority:** P0 | **Stakeholder:** Security Officer, Platform Admin | **Status:** Approved
- **Nội dung:**
  1. Dữ liệu được cách ly tuyệt đối: PostgreSQL (Row-Level Security + Tenant Context), Vector Store (FAISS mmap tách riêng file `indexes/{tenant_id}.index`), Context in-memory tách biệt.
  2. Tenant Context được xác minh server-side từ JWT payload; không tin cậy header `X-Tenant-ID` gửi tự do từ client.
  3. Phòng chống BOLA/IDOR: Truy vấn resource ngoài tenant trả về `404 Not Found`.
- **Nghiệm thu:** Bộ test tự động kiểm thử chéo Tenant A/B/C đạt 0 trường hợp rò rỉ dữ liệu.

---

### Phân hệ 2: Danh tính, Xác thực & Phân quyền (Auth & RBAC/ACL)

#### SR-003: Xác thực Người dùng & JWT Rotation
- **Priority:** P0 | **Stakeholder:** Company Admin, Employee | **Status:** Approved
- **Nội dung:** Đăng nhập email/mật khẩu bảo mật; cấp phát cặp Access Token (15 phút) và Refresh Token (7 ngày) áp dụng kỹ thuật Refresh Token Rotation. Thu hồi phiên ghi vào bảng `revoked_tokens` (`UNLOGGED` trong PostgreSQL để không tốn I/O đĩa).
- **Nghiệm thu:** Tái sử dụng Refresh Token cũ lập tức vô hiệu hóa toàn bộ session family.

#### SR-004: Quan hệ Thành viên Đa doanh nghiệp (Multi-Tenant Membership)
- **Priority:** P0 | **Stakeholder:** Employee, Company Admin | **Status:** Approved
- **Nội dung:** Một tài khoản người dùng có thể tham gia nhiều Tenant qua bảng `Membership`. Mỗi phiên làm việc gắn chặt với 1 tenant; chuyển đổi tenant phải cấp token mới.
- **Nghiệm thu:** User X đóng vai trò Admin ở Tenant A nhưng là Employee ở Tenant B; không thừa hưởng quyền chéo.

#### SR-005: Phân quyền Phân cấp (RBAC, ABAC & Document-Level ACL)
- **Priority:** P0 | **Stakeholder:** Company Admin, Knowledge Manager | **Status:** Approved
- **Nội dung:**
  1. **RBAC:** Định nghĩa các vai trò tiêu chuẩn (`PLATFORM_ADMIN`, `COMPANY_ADMIN`, `DEPT_ADMIN`, `KNOWLEDGE_MANAGER`, `EMPLOYEE`).
  2. **ABAC / Department Scoping:** Phân chia người dùng theo phòng ban; tài liệu thuộc phòng ban HR chỉ nhân sự HR đọc được.
  3. **Document-Level ACL:** Hỗ trợ cấu hình quyền trực tiếp trên tài liệu cụ thể: Owner, Read, Edit, Full Control.
  4. **Fail-Closed:** Quyết định cấp quyền mặc định là DENY. Thu hồi quyền có hiệu lực trong < 5 giây.
- **Nghiệm thu:** Nhân viên bị loại khỏi phòng ban lập tức mất quyền truy cập tài liệu phòng ban đó.

---

### Phân hệ 3: Quản lý Kho tri thức & Ingestion Tinh gọn

#### SR-006: Thực thể Kho Tri thức (Knowledge Base - KB)
- **Priority:** P0 | **Stakeholder:** Company Admin, Knowledge Manager | **Status:** Approved
- **Nội dung:** Knowledge Base (KB) là thực thể quản lý cấp 1 trong mỗi Tenant, gom nhóm tài liệu theo mục đích nghiệp vụ (Chính sách Nhân sự, Quy trình IT, Hợp đồng). Mỗi KB có phân quyền truy cập theo phòng ban hoặc nhóm người dùng.
- **Nghiệm thu:** Tạo KB, gán tài liệu và phân quyền hoạt động chính xác.

#### SR-007: Ingestion Pipeline Không Lưu File Gốc & Mã băm SHA-256
- **Priority:** P0 | **Stakeholder:** Knowledge Manager, IT Admin | **Status:** Approved
- **Nội dung:**
  1. Upload các định dạng PDF, DOCX, XLSX, TXT (tối đa 50MB/file). Đọc file trong bộ nhớ đệm tạm thời (Memory Stream).
  2. Tính mã băm `SHA-256` (`file_hash`); từ chối upload trùng lặp nếu `file_hash` đã tồn tại trong cùng Tenant.
  3. Trích xuất văn bản sạch (`clean_text`), số trang, bảng biểu; lưu `raw_text` và metadata vào PostgreSQL.
  4. Hủy ngay lập tức file nhị phân gốc khỏi bộ nhớ. Tuyệt đối không lưu file nhị phân trên đĩa hay S3/MinIO.
- **Nghiệm thu:** Upload 100 tài liệu hoàn tất, đĩa cứng chỉ tăng dung lượng văn bản thuần trong PostgreSQL; không có file nhị phân lưu trên ổ đĩa.

#### SR-008: Hierarchical Chunking (Parent-Child Architecture)
- **Priority:** P0 | **Stakeholder:** Knowledge Manager | **Status:** Approved
- **Nội dung:**
  1. Tách tài liệu thành các **Parent Chunks (~1.000 tokens)** lưu trong bảng `document_parent_chunks` trên PostgreSQL để giữ trọn vẹn ngữ cảnh đề mục.
  2. Chia nhỏ mỗi Parent Chunk thành các **Child Chunks (~200 tokens)**, sinh vector embedding (BAAI/bge-m3 qua API) và lưu trữ vào file FAISS mmap của tenant.
  3. Áp dụng kỹ thuật **Semantic Similarity Distance Chunking** và **Header-Aware** (tuyệt đối không dùng LLM để chunking).
- **Nghiệm thu:** Truy vấn vector tìm thấy child chunk chính xác; câu trả lời của LLM trích xuất được trọn vẹn ngữ cảnh từ parent chunk tương ứng.

---

### Phân hệ 4: Tìm kiếm Lai & AI RAG với Gemma 4 26B

#### SR-009: Tìm kiếm Lai Kết hợp (Hybrid Search with RRF)
- **Priority:** P0 | **Stakeholder:** Employee, Knowledge Manager | **Status:** Approved
- **Nội dung:**
  1. **Dense Retrieval:** FAISS mmap (`storage/indexes/{tenant_id}.index`) tìm kiếm tương đồng vector không ngốn RAM.
  2. **Sparse Retrieval:** PostgreSQL Full-Text Search (`tsvector` + GIN Index) tìm kiếm từ khóa chính xác và mã hiệu văn bản.
  3. **Hợp nhất bảng xếp hạng RRF:** Hợp nhất kết quả từ hai nguồn bằng thuật toán Reciprocal Rank Fusion ($k=60$).
  4. **Pre-filtering:** Lọc quyền `tenant_id` và `acl_tags` trước khi xếp hạng.
- **Nghiệm thu:** Đo lường Recall@10 đạt tối thiểu 85% trên tập dữ liệu kiểm thử.

#### SR-010: Context Filtering & Prompt Compression Pipeline
- **Priority:** P0 | **Stakeholder:** System Architect, DevOps | **Status:** Approved
- **Nội dung:**
  1. Trước khi nạp context vào prompt cho Gemma 4 26B, áp dụng bộ lọc 3 tầng:
     - *Dynamic Relevance Thresholding:* Chỉ giữ lại các chunk có score > 0.65.
     - *Cross-Chunk Deduplication:* Khử bỏ câu trùng lặp do chunk overlap.
     - *Sentence-Level Compression:* Trích xuất các câu trực tiếp giải đáp câu hỏi, gọt bỏ rác.
  2. Giảm 60–70% số token đầu vào, bảo vệ trần hạn ngạch **16K TPM** của Gemma 4 26B.
  3. Tốc độ phản hồi Time to First Token (TTFT) đạt < 1.5 giây.
- **Nghiệm thu:** Đo lường số lượng prompt tokens giảm từ ~3.500 xuống < 1.200 tokens; TTFT giảm trên 50%.

#### SR-011: Quản lý Quota & Gemma 4 26B Integration
- **Priority:** P0 | **Stakeholder:** IT/Operations, Platform Admin | **Status:** Approved
- **Nội dung:**
  1. Centralized Quota Manager bằng thuật toán **In-Memory Token Bucket** (30 RPM, 16K TPM, 14.4K RPD) phân bổ công bằng theo tenant.
  2. Cung cấp 4 chế độ vận hành: **Mock LLM** (chi phí $0 phục vụ test tải), **Retrieval-only**, **Real LLM**, và **Hybrid**.
  3. Câu trả lời có trích dẫn nguồn xác thực (Tên tài liệu, trang, đoạn trích); chủ động từ chối (Abstention) khi không đủ bằng chứng; hỗ trợ streaming câu trả lời qua Server-Sent Events (SSE).
- **Nghiệm thu:** Chạy test tải với chế độ Mock LLM không tốn chi phí và không bị lỗi 429; chuyển cờ sang Real LLM gọi Gemma 4 26B trả về câu trả lời có trích dẫn chuẩn xác.

---

### Phân hệ 5: Lịch sử Hội thoại, Dashboards & Bảo mật

#### SR-012: Quản lý Phiên Hội thoại & Lịch sử
- **Priority:** P0 | **Stakeholder:** Employee | **Status:** Approved
- **Nội dung:** Người dùng tạo nhiều luồng hội thoại độc lập, xem lại lịch sử chat, gửi feedback Thumbs Up/Down. Dữ liệu hội thoại được cô lập 100% theo `user_id` và `tenant_id`.
- **Nghiệm thu:** Xem lại lịch sử chat chính xác; Admin không thể xem nội dung chat riêng tư của nhân viên.

#### SR-013: Dashboards 3 Cấp Độc lập
- **Priority:** P0 | **Stakeholder:** Platform Admin, Business Admin, Employee | **Status:** Approved
- **Nội dung:**
  1. **Platform Admin Dashboard:** Giám sát tổng quan hệ thống: CPU, RAM máy chủ, trạng thái PostgreSQL, tỷ lệ hạn ngạch Gemma 4 26B, danh sách tenants. Tuyệt đối không hiển thị nội dung tài liệu của khách hàng.
  2. **Business Admin Dashboard:** Thống kê trong tenant: số user, phòng ban, dung lượng lưu trữ, số câu hỏi AI, top tài liệu được trích dẫn, điểm phản hồi hài lòng.
  3. **Employee Workspace:** Giao diện tra cứu tri thức, chat với AI Assistant, danh sách tài liệu được phép đọc.
- **Nghiệm thu:** Cả 3 cấp dashboard hiển thị đúng dữ liệu trong phạm vi quyền được cấp.

#### SR-014: Nhật ký Kiểm toán & Tuân thủ Quyền được Xóa (Compliance)
- **Priority:** P0 | **Stakeholder:** Security Officer, Compliance Officer | **Status:** Approved
- **Nội dung:**
  1. Ghi nhận log kiểm toán bất biến cho mọi thao tác trọng yếu (Đăng nhập, đổi quyền, upload/xóa tài liệu, tra cứu tri thức).
  2. Tuân thủ Nghị định 13/2023/NĐ-CP và GDPR: Khi tenant hủy hợp đồng, thực hiện xóa liên hoàn (Cascading Purge): Xóa bảng dữ liệu trong PostgreSQL ➔ Xóa file FAISS index trên đĩa ➔ Thu hồi toàn bộ session trong vòng 48 giờ.
- **Nghiệm thu:** Lệnh xóa tenant xóa sạch toàn bộ bản ghi và file index tương ứng; không còn dấu vết dữ liệu.

---

## 3. Phase 2: Synthetic User Simulation (10.000 Virtual Users) & Vận hành

#### SR-015: Kiến trúc Mô phỏng 10.000 Virtual Users (k6 Client)
- **Priority:** P2 | **Stakeholder:** Lead Performance Engineer, QA Lead | **Status:** Approved
- **Nội dung:**
  1. Công cụ k6 được cài đặt và thực thi trên **máy tính laptop của lập trình viên (i5-10300H, 16GB RAM)**, đóng vai trò Load Generator bắn tải qua Internet vào máy chủ GCP Always Free `e2-micro`.
  2. Cấu hình hồ sơ của 10.000 Virtual Users (VU) phân bổ trên nhiều Tenant ảo với các vai trò khác nhau (Admin, Manager, Employee).
  3. Mô phỏng 4 hành trình người dùng thực tế: Standard Employee (70%), Knowledge Manager (15%), Business Admin (10%), và Adversarial Security Tester (5%).
  4. Sử dụng chế độ **Mock LLM** trong Quota Manager khi kiểm thử tải quy mô lớn để bảo vệ hạn ngạch và chi phí inference.
- **Nghiệm thu:** k6 duy trì kịch bản tải ổn định; máy chủ GCP e2-micro xử lý trơn tru không bị crash OOM.

#### SR-016: Bộ Kiểm thử Tải & Khả năng Chịu lỗi (Load, Stress & Soak Testing)
- **Priority:** P2 | **Stakeholder:** SRE, Performance Engineer | **Status:** Approved
- **Nội dung:**
  1. **Baseline Test (1K requests):** Đo lường độ trễ chuẩn.
  2. **Standard Load Test (10K requests):** Đo lường throughput và P95 latency dưới tải ổn định.
  3. **Stress & Spike Test:** Đột ngột tăng vọt lưu lượng để quan sát cơ chế tự bảo vệ (Rate limiting 429) và khả năng hấp thụ qua Swapfile.
  4. **Soak Test (8 giờ liên tục):** Chạy tải liên tục trong 8 giờ để chứng minh hệ thống không bị rò rỉ bộ nhớ (Memory Leak).
- **Nghiệm thu:** Báo cáo benchmark có biểu đồ trực quan; không xảy ra lỗi sập dịch vụ (0 crashes, 0 OOM events).

#### SR-017: Kiểm thử Bảo mật Thâm nhập (Penetration Testing) & Báo cáo Vận hành 2 Tháng
- **Priority:** P2 | **Stakeholder:** Security Officer, Pentester | **Status:** Approved
- **Nội dung:**
  1. Thực hiện pentest tự động và thủ công theo danh mục OWASP API Security Top 10 và Security Scenario Catalog (SEC-001 đến SEC-010). Đạt tiêu chuẩn phát hành: **0 lỗi Critical, 0 lỗi High**.
  2. Vận hành nền tảng trong 2 tháng thực tế; thực hiện diễn tập sự cố (Chaos Drills: giả lập sập LLM provider, khôi phục dữ liệu database restore drill đo RTO < 4h, RPO < 1h).
  3. Đóng gói kết quả thành bộ hồ sơ kỹ thuật hoàn chỉnh (Portfolio Artifacts) chứng minh năng lực thực tế tương đương **6 tháng – 1 năm kinh nghiệm**.
- **Nghiệm thu:** Có nhật ký vận hành 60 ngày, báo cáo pentest sạch lỗi, và video demo sản phẩm hoàn chỉnh.

---

## 4. Kế hoạch Triển khai & Phương pháp Đồng hành (Mentorship Plan)

- **Phương pháp thực hiện:**
  - *Lần đầu tiên (Bạn tự tay gõ code 100%):* Tôi giải thích bản chất kiến trúc, cung cấp skeleton/interface, bạn tự tay code từng dòng logic và debug để nắm vững bản chất.
  - *Các lần sau (AI hỗ trợ tăng tốc):* Tôi sinh code tự động cho các thành phần lặp lại (boilerplate, CRUD, schemas phụ) để bạn review và tích hợp nhanh.
- **Lộ trình 8 Modules (Phase 1) ➔ Phase 2:**
  - *Module 1:* Cấu trúc dự án, PostgreSQL 16 Low-Memory & Tenant Data Layer.
  - *Module 2:* Authentication, JWT Rotation & Tenant Context Middleware.
  - *Module 3:* Ingestion Pipeline, SHA-256 Hashing & Hierarchical Chunking (Parent-Child).
  - *Module 4:* FAISS mmap Indexing & Hybrid Search RRF.
  - *Module 5:* Context Filtering Pipeline, Quota Manager & Gemma 4 26B Integration.
  - *Module 6:* Hierarchical RBAC, ABAC & Document-Level ACL.
  - *Module 7:* Three-Tier Dashboards & Streaming SSE API.
  - *Module 8:* Đóng gói Docker, Cấu hình GCP e2-micro (Swap 4GB) & Deploy Production.
  - *Phase 2:* Synthetic User Simulation 10.000 VUs, Load Testing, Pentest & Vận hành 2 tháng.

---

## 5. Lịch sử Thay đổi Tài liệu (Document Change Log)

| Phiên bản | Ngày cập nhật | Tác giả | Mô tả thay đổi chính | Trạng thái |
|---|---|---|---|---|
| **1.0** | 2026-11-20 | Lead BA | Khởi tạo tài liệu yêu cầu ban đầu. | Superseded |
| **1.1** | 2026-11-22 | Solution Architect | Bổ sung phạm vi Multi-business và 3-tier Dashboards. | Superseded |
| **2.0** | 2026-11-23 | Enterprise Architect | Tái cấu trúc chuẩn hóa toàn diện; tách Phase 1 và Phase 2; chuẩn hóa Gemma 4 26B. | Superseded |
| **2.1** | 2026-11-23 | Enterprise Architect | Cập nhật toàn bộ kiến trúc Ultra-Lean: Tinh chỉnh cho **GCP Always Free e2-micro (1.0 GB RAM / 50GB Disk)**; quy mô chuẩn hóa **25.000 tài liệu**; áp dụng mô hình **Zero Raw Storage**; thay thế vector store bằng **FAISS mmap**; bổ sung **Hierarchical Chunking (Parent-Child)** và **Context Filtering & Compression Pipeline**; áp dụng phương pháp tự tay code đồng hành. | **Baseline Approved** |
