# query_for_app_owner.md — Sổ tay Vấn đáp Kỹ thuật: Bug Fixes, Tối ưu Hiệu năng, Triển khai Production & Thử thách Dành cho App Owner

**Dự án:** Enterprise AI Knowledge Platform (Multi-Tenant SaaS)  
**Tài liệu dành cho:** App Owner / Tech Lead / Lead Architect  
**Cập nhật lần cuối:** 2026-11-26 (Khởi tạo cùng Sprint 1 & 2, mở rộng theo từng Sprint)  

> **Quy ước báo cáo (từ 2026-11-26):** Endpoint test ghi báo cáo vào `endpoint_test/endpoint_test_report_<YYYY-MM-DD>.md` theo ngày local của máy chạy; xem `AGENTS.md` Mục 5. Các link `ENDPOINT_TEST_REPORT.md` bên dưới là snapshot lịch sử PRE-FIX, không phải báo cáo mặc định hiện hành.

---

## 📌 MỤC ĐÍCH & CÁCH SỬ DỤNG SỔ TAY NÀY

Tài liệu này là **tập hợp sống (living document)** ghi lại toàn bộ các câu hỏi chuyên sâu, tình huống chẩn đoán lỗi thực tế (bug deep-dives), các quyết định tối ưu hóa hiệu năng (performance tuning), và các góc khuất kiến trúc phát sinh trong quá trình xây dựng hệ thống.

Một kỹ sư chỉ thực sự làm chủ dự án khi có khả năng **trả lời rành mạch, bảo vệ thuyết phục từng dòng code và từng con số cấu hình** trước Nhà tuyển dụng, Khách hàng doanh nghiệp hoặc Ban Giám đốc kỹ thuật.

---

## PHẦN 1: CHẨN ĐOÁN LỖI THỰC TẾ & BÀI HỌC KỸ THUẬT (BUG FIXING DEEP-DIVES)

### 🐛 Bug 01: Windows Asyncio Event Loop Hang khi chạy Test Runner
- **Hiện tượng:** Khi chạy `pytest` với `httpx.AsyncClient` hoặc `TestClient`, tiến trình kiểm thử bị kẹt vĩnh viễn (hang) ở bước gửi HTTP request đầu tiên mà không báo lỗi hay timeout.
- **Bản chất kỹ thuật (Root Cause):**
  - Trên Windows với Python 3.8+, thư viện `asyncio` mặc định sử dụng `ProactorEventLoop` (dựa trên I/O Completion Ports - IOCP).
  - Khi một event loop hoặc background thread được khởi tạo, nó gọi hàm `socket.socketpair()` trong `socket.py`.
  - Trên Windows, do hệ điều hành không hỗ trợ `AF_UNIX` socketpair nguyên bản như POSIX, Python giả lập bằng cách mở một listening socket trên địa chỉ loopback rồi tạo kết nối TCP loopback (`connect()` $\rightarrow$ `accept()`).
  - Trong môi trường sandbox kiểm soát nghiêm ngặt mạng (Standard Sandbox Mode), kết nối loopback bị chặn hoặc chờ xác thực, khiến hàm `connect()` bị block vĩnh viễn.
- **Giải pháp:** Cấu hình quyền loopback cục bộ cho test runner (`BypassSandbox: true`), các socketpair kết nối thành công trong 0.001s, 16/16 test cases chạy hoàn tất trong 3.85s.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Nếu hệ thống được đóng gói vào Docker container và deploy lên máy chủ GCP e2-micro (Ubuntu 22.04 LTS), lỗi này có nguy cơ tái diễn không? Tại sao Linux sử dụng `epoll` và `AF_UNIX` socketpair lại không gặp phải rào cản này?"*

---

### 🐛 Bug 02: Python Module & Variable Name Collision (Shadowing)
- **Hiện tượng:** Trong file `conftest.py`, fixture `client` ném ngoại lệ: `AttributeError: module 'app' has no attribute 'dependency_overrides'`.
- **Bản chất kỹ thuật (Root Cause):**
  - Trong `conftest.py` có hai dòng import:
    ```python
    from app.main import app          # app ở đây là đối tượng FastAPI
    import app.models                 # Dòng này ghi đè biến 'app' thành module 'app'!
    ```
  - Trong cơ chế import của Python, câu lệnh `import a.b` sẽ gán tên gói cấp cao nhất (`a`) vào namespace cục bộ nếu biến đó chưa được bảo vệ. Kết quả là biến `app` (FastAPI instance) bị module `app` ghi đè!
- **Giải pháp:** Đổi tên rõ ràng `from app.main import app as fastapi_app` và sử dụng `fastapi_app.dependency_overrides`.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Python binding scope hoạt động theo thứ tự nào? Làm cách nào để cấu hình các công cụ linter tĩnh (như Ruff hoặc Flake8) để tự động bắt lỗi biến bị shadowing trước khi code được đẩy lên CI/CD?"*

---

### 🐛 Bug 03: Sai lệch Thuộc tính Khởi tạo Model (Declarative Attribute Mismatch)
- **Hiện tượng:** Khi chạy test đăng ký tài khoản, ném ngoại lệ: `TypeError: 'subscription_tier' is an invalid keyword argument for Tenant`.
- **Bản chất kỹ thuật (Root Cause):**
  - Model `Tenant` trong SQLAlchemy 2.0 được thiết kế lưu cấu hình hạn mức linh hoạt bằng cột JSON `quota_limits: Mapped[Dict[str, Any]] = mapped_column(JSON)`.
  - Service layer lại truyền nhầm tham số phẳng `subscription_tier="FREE"`, không tồn tại trong schema bảng CSDL.
- **Giải pháp:** Sửa lại dữ liệu truyền vào model đúng chuẩn schema: `quota_limits={"tier": "FREE", "max_users": 10, "max_documents": 50}`.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao việc dùng cột `JSON` / `JSONB` cho hạn mức `quota_limits` lại tối ưu hơn việc tạo nhiều cột riêng lẻ (`subscription_tier`, `max_users`, `max_docs`)? Khi nào cần tạo Index GIN trên trường JSON này trong PostgreSQL?"*

---

### 🐛 Bug 04: Lỗ hổng BOLA Logic Fallback (Broken Object Level Authorization)
- **Hiện tượng:** Test case `test_bola_cross_tenant_access_blocked` ban đầu trả về `200 OK` thay vì bị chặn với lỗi `403 Forbidden`.
- **Bản chất kỹ thuật (Root Cause):**
  - Trong logic trích xuất `target_tenant_str` ban đầu:
    ```python
    target_tenant_str = payload.get("tenant_id") or x_tenant_id
    ```
  - Vì JWT token của User A đã chứa `payload.get("tenant_id")` (Tenant A), toán tử `or` đã lấy luôn Tenant A và bỏ qua header `x_tenant_id` (Tenant B) mà attacker gửi kèm! Kết quả là hệ thống xử lý trên Tenant A và trả về 200, tạo ra ảo giác rằng request thành công.
- **Giải pháp:** Nếu client chủ động truyền header `X-Tenant-ID` (yêu cầu chuyển đổi context), hệ thống bắt buộc phải ưu tiên lấy `X-Tenant-ID` đó và truy vấn bảng `memberships` để xác thực xem User có thuộc Tenant đó không. Nếu không, lập tức quăng `HTTPException(403, "BOLA Blocked")`.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao không bao giờ được tin tưởng dữ liệu định danh do client gửi lên? Nguyên tắc Fail-Closed được thể hiện như thế nào trong hàm `get_current_tenant_context`?"*

---

### 🐛 Bug 05: SQLAlchemy Asyncio MissingGreenlet do Lazy Loading ngầm định
- **Hiện tượng:** Khi upload tài liệu hoặc xóa tài liệu, API ném ngoại lệ: `sqlalchemy.exc.StatementError: (sqlalchemy.exc.MissingGreenlet) greenlet_spawn has not been called; can't call await_() here. Was IO attempted in an unexpected place?` tại câu lệnh truy cập `len(doc.parent_chunks)`.
- **Bản chất kỹ thuật (Root Cause):**
  - Trong SQLAlchemy 2.0 Async, các thuộc tính quan hệ (Relationships) mặc định sử dụng cơ chế **Lazy Loading** đồng bộ.
  - Sau khi `await db.commit()` được gọi, session giải phóng hoặc đánh dấu các đối tượng là expired. Khi truy cập thuộc tính `doc.parent_chunks`, SQLAlchemy cố gắng phát sinh một câu truy vấn `SELECT` đồng bộ ngầm định.
  - Vì không có `await` và không chạy trong context `greenlet_spawn`, async engine lập tức chặn đứng để bảo vệ Event Loop khỏi bị block!
- **Giải pháp:** Bắt buộc sử dụng kỹ thuật **Eager Loading tường minh** thông qua `selectinload(Document.parent_chunks)` và `selectinload(Document.child_chunks)` ngay trong câu lệnh `SELECT`, hoặc load lại đối tượng sau khi commit.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao Lazy Loading bị coi là Anti-Pattern trong lập trình Backend Async? Sự khác biệt cốt lõi về số lượng câu lệnh SQL và mức tiêu thụ RAM giữa `selectinload()` (sử dụng toán tử `WHERE id IN (...)`) và `joinedload()` (sử dụng phép `LEFT OUTER JOIN`) là gì? Tại sao đối với cấu trúc phân cấp Parent-Child, `selectinload()` lại tránh được hiện tượng bùng nổ dữ liệu trùng lặp (Cartesian Product)?"*

---

### 🐛 Bug 06 (CRITICAL): Thiếu Router Registration cho `auth` và `documents` trong `router.py`
- **Phát hiện từ:** [ENDPOINT_TEST_REPORT.md](endpoint_test/ENDPOINT_TEST_REPORT.md) (`A03`, `C01–C09`, `D01–D05`).
- **Hiện tượng:** Toàn bộ các route `/api/v1/auth/*` và `/api/v1/documents/*` trả về `HTTP 404 Not Found` khi kiểm tra OpenAPI schema và gọi API thực tế.
- **Bản chất kỹ thuật:** Trong [backend/app/api/v1/router.py](backend/app/api/v1/router.py), `auth` và `documents` đã được import ở dòng 2 nhưng bị thiếu hai dòng `api_router.include_router(...)` (dễ xảy ra khi merge conflict hoặc lưu file đè trên IDE).
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao bộ unit test pytest có thể vẫn pass hoặc không bắt được lỗi ngay lập tức nếu IDE vô tình chỉnh sửa `router.py`? Làm thế nào thiết lập Smoke Test tự động kiểm tra OpenAPI schema (`/openapi.json`) trong CI/CD để chặn đứng tình trạng rớt route trước khi deploy?"*

---

### 🐛 Bug 07 (CRITICAL SECURITY): Toàn bộ Router `/api/v1/tenants` thiếu xác thực & phân quyền
- **Phát hiện từ:** [ENDPOINT_TEST_REPORT.md](endpoint_test/ENDPOINT_TEST_REPORT.md) (`B03`, `B04`, `B05`).
- **Hiện tượng:** Người dùng ẩn danh (không có JWT Bearer Token) có thể gọi `GET /api/v1/tenants` để liệt kê toàn bộ danh sách khách hàng, gọi `POST /api/v1/tenants/{id}/suspend` để khóa hệ thống của đối thủ, hoặc gọi `PATCH /api/v1/tenants/{id}` để nâng cấp `quota_limits` trái phép!
- **Bản chất kỹ thuật:** [backend/app/api/v1/endpoints/tenants.py](backend/app/api/v1/endpoints/tenants.py) được viết ở Sprint 1 trước khi Sprint 2 triển khai `get_current_tenant_context`, nhưng chưa được gắn dependency bảo vệ `is_superuser` / `TenantContext`.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao nguyên tắc bảo mật **Fail-Closed & Default-Deny** yêu cầu mọi endpoint quản trị cấp nền tảng (`/tenants`) bắt buộc phải kiểm tra `is_superuser`? Trong thực tế triển khai nhiều Sprint nối tiếp nhau, làm sao kiểm toán tự động 100% các route xem có route nào quên gắn `Depends(get_current_tenant_context)` hay không?"*

---

### 🐛 Bug 08 (HIGH SECURITY): `AuthService.authenticate()` cho phép đăng nhập vào Tenant đã bị Suspend
- **Phát hiện từ:** [ENDPOINT_TEST_REPORT.md](endpoint_test/ENDPOINT_TEST_REPORT.md) (`I04`).
- **Hiện tượng:** Khi một Tenant bị tạm khóa (`tenants.is_active = False`), nhân viên của Tenant đó vẫn đăng nhập thành công và nhận JWT token hợp lệ từ `AuthService.authenticate()`!
- **Bản chất kỹ thuật:** Hàm `AuthService.authenticate()` (và `get_current_tenant_context`) mới chỉ kiểm tra `User.is_active` và `Membership.is_active`, chưa JOIN hoặc đối soát `Tenant.is_active`.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Nếu một công ty ngừng thanh toán hoặc bị nghi ngờ rò rỉ dữ liệu và Platform Admin bấm Suspend Tenant, việc chỉ kiểm tra `Tenant.is_active` lúc Login có đủ không, hay bắt buộc phải kiểm tra thêm ngay trong `get_current_tenant_context` đối với các Access Token đã cấp trước đó (còn hạn 15 phút)?"*

---

### 🐛 Bug 09 (HIGH): Parser ném lỗi Raw Exception (`PdfStreamError`, `BadZipFile`) gây HTTP 500
- **Phát hiện từ:** [ENDPOINT_TEST_REPORT.md](endpoint_test/ENDPOINT_TEST_REPORT.md) (`I03`).
- **Hiện tượng:** Khi người dùng tải lên file `.pdf`, `.docx`, hoặc `.xlsx` bị hỏng định dạng hoặc giả mạo phần mở rộng, `extract_document_content()` làm văng ngoại lệ chưa được bắt (`pypdf.errors.PdfStreamError`, `zipfile.BadZipFile`), khiến API trả về `500 Internal Server Error` thay vì `400 Bad Request`.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao để lọt lỗi `500 Internal Server Error` từ thư viện parser ra ngoài lại cực kỳ nguy hiểm về mặt an ninh (rò rỉ stack trace) lẫn trải nghiệm người dùng? Ngoài try/catch, cần kiểm tra Magic Bytes (file signature) như `%PDF-` hay `PK\\x03\\x04` ở lớp đầu vào để từ chối file giả mạo sớm (Fail-Fast) trước khi tốn CPU parse như thế nào?"*

---

### 🐛 Bug 10 (HIGH): `ImportError` trong MCP Stdio CLI Runner (`app/mcp/cli.py`)
- **Phát hiện từ:** [ENDPOINT_TEST_REPORT.md](endpoint_test/ENDPOINT_TEST_REPORT.md) (`I01`).
- **Hiện tượng:** Khi import hoặc chạy `python -m app.mcp.cli`, chương trình crash ngay lập tức với lỗi `ImportError: cannot import name 'async_session_factory' from 'app.core.database'`.
- **Bản chất kỹ thuật:** Trong [database.py](backend/app/core/database.py), session factory được đặt tên là `AsyncSessionLocal`, không phải `async_session_factory`. Vì `pytest` chỉ test cổng HTTP `/api/v1/mcp` mà không import module `cli.py`, bug này đã lọt qua unit test!
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Bài học gì rút ra khi một module CLI standalone không được import trong test suite chính? Ngoài sửa tên import, làm sao đảm bảo `cli.py` không chạy `connect_read_pipe` lỗi trên Windows event loop (`ProactorEventLoop` không hỗ trợ `connect_read_pipe` cho `sys.stdin` trên Windows)?"*

---

### 🐛 Bug 11 (MEDIUM): `LLMService.generate_answer(mode="real")` âm thầm fallback về Mock
- **Phát hiện từ:** [ENDPOINT_TEST_REPORT.md](endpoint_test/ENDPOINT_TEST_REPORT.md) (`G02`).
- **Hiện tượng:** Khi client gửi yêu cầu `"mode": "real"`, hàm `LLMService.generate_answer` chạy qua nhánh `elif mode == "real": pass` và rơi xuống dòng cuối gọi `_generate_mock_response()`, trả về câu trả lời giả lập nhưng API vẫn gắn nhãn `"mode": "real"` với HTTP 200!
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Trong hệ thống AI doanh nghiệp, tại sao **Silent Fallback** (âm thầm trả về mock khi người dùng yêu cầu LLM thật) lại là một lỗi hợp đồng API (API Contract Bug) nghiêm trọng? Nên ném lỗi `503 Service Unavailable` / `501 Not Implemented` hay cấu hình HTTP Client (Async `httpx` + Exponential Backoff + Circuit Breaker) gọi API Gemma thật khi có API Key?"*

---

### 🐛 Bug 12 (HIGH / RATE-LIMIT RISK): Context quá dài vượt ngưỡng khiến Prompt chạm trần Rate Limit 16K TPM của Gemma 4 26B
- **Hiện tượng / Nguy cơ:** Khi cấu hình `top_k` mặc định là `5` (tối đa `20`), cùng với các `Parent Chunk` dài (~1.000 tokens/chunk), `ContextFilterPipeline` bản cũ gặp 5 lỗ hổng khiến **input token gửi tới Gemma 4 26B phình to bất thường** (lên tới 3.000–5.000 tokens/query):
  1. Không có giới hạn cứng số chunk tối đa (`max_chunks`) và trần token ngữ cảnh tối đa (`max_context_tokens`).
  2. Ngưỡng RRF cố định `0.005` quá lỏng (vì ngay cả rank 10 của Dense search vẫn đạt `~0.0085`), không lọc được các chunk đuôi nhiễu.
  3. Ngoại lệ `len(sentences) <= 3` trong Tier 3 làm vô hiệu hóa bộ lọc câu đối với các đoạn Markdown/gạch đầu dòng ít chấm câu (và regex cũ `(?<=[.!?\n])\s+` gộp nguyên danh sách bullet thành 1 câu khổng lồ).
  4. `PromptService.SYSTEM_PROMPT` dài ~260 tokens và header block lặp lại dư thừa.
  5. `QuotaManager.acquire(..., estimated_tokens=600)` dự đoán cứng 600 tokens trong khi prompt thực tế có thể vượt gấp nhiều lần, dễ khiến API Google trả lỗi `429 Resource Exhausted` do vượt **16K TPM** (Tokens Per Minute).
- **Hướng khắc phục đã triển khai:** Áp dụng **Context Filter + Sentence Compression 3 tầng** siết chặt trong [context_filter.py](backend/app/services/context_filter.py), [prompt_service.py](backend/app/services/prompt_service.py), [chat.py](backend/app/schemas/chat.py), [chat_service.py](backend/app/services/chat_service.py), và [quota_service.py](backend/app/services/quota_service.py):
  - Hạ `top_k` mặc định từ `5` xuống `3` (tối đa `10`), khóa cứng tối đa **3 chunks** vào LLM (`DEFAULT_MAX_CHUNKS = 3`) và ngưỡng RRF tương đối `>= 55% * top_score`.
  - Làm sạch Markdown, tách chuẩn theo cả câu lẫn dòng (`\n+`), xóa câu trùng/bao hàm, bỏ ngoại lệ `len(sentences) <= 3`, chấm điểm câu theo query + bigram + neo `child_content` + số liệu, và áp **trần cứng `max_context_tokens = 550`** để giảm mạnh input token.
  - Tinh gọn `SYSTEM_PROMPT` từ ~260 down còn ~107 tokens (~59% nhẹ hơn) và đối soát hoàn trả token dư về 16K TPM bucket qua `QuotaManager.reconcile_tokens()`.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Vì sao dù đã có Context Filtering mà vẫn phải thêm bước cắt cứng `max_context_tokens` và giảm `SYSTEM_PROMPT`? Khi giảm mạnh context gửi vào LLM, hệ thống vẫn bảo toàn trích dẫn và độ chính xác câu trả lời bằng cơ chế gì?"*

---

### 🐛 Bug 13 (HIGH ACCURACY): Trộn sai Heading giữa các Parent Chunk & Cắt `LIMIT` Sparse SQLite trước khi xếp hạng
- **Hiện tượng:** Khi chạy kiểm thử live với tài liệu gồm 6 phân mục `# CHÍNH SÁCH SỐ 1..6`, câu trả lời trả về `"Xin lỗi, tôi không tìm thấy thông tin..."` dù nội dung đáp án (`CHÍNH SÁCH SỐ 6`) có trong tài liệu!
- **Bản chất kỹ thuật (Root Cause kép):**
  1. Trong [chunking_service.py](backend/app/services/chunking_service.py), nhánh `else:` gộp nhiều phân mục có Heading khác nhau vào chung 1 `Parent Chunk` (~1000 tokens) rồi ghi đè `current_heading = heading_candidate`. Hậu quả là phân mục cuối cùng bị gắn nhầm ngữ cảnh cha của phân mục trước, làm loãng vector khi nhúng `contextual_embed_texts`.
  2. Trong [search_service.py](backend/app/services/search_service.py), truy vấn SQLite `ILIKE` dùng `.limit(request.top_k * 2)` mà không có `ORDER BY` trước, cộng thêm chưa lọc stopword (`"và"`, `"về"`, `"của"`), khiến SQLite cắt 6 dòng đầu tiên và bỏ sót chunk đúng nằm ở cuối bảng!
- **Hướng khắc phục đã triển khai:** Tách `Parent Chunk` mới ngay khi gặp heading phân mục mới (bảo toàn 100% ranh giới ngữ nghĩa & nhãn `heading`), loại stopword trước khi match sparse, và lấy tập ứng viên đủ rộng (`max(200, top_k * 10)`) để chấm điểm rồi mới cắt top vào `RRFService.fuse`.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao việc cắt `.limit(K)` ở SQL trước khi có hàm `ORDER BY relevance` lại gây mất recall thầm lặng trên dữ liệu nhiều đoạn văn bản lặp từ dừng? Và vì sao việc giữ ranh giới Parent Chunk theo từng Heading quan trọng đối với Hierarchical RAG?"*

---

### ⚡ Performance Question (Bonus từ Edge Case Audit): `TenantService` Commit Consistency & Hybrid Search Post-Filtering
- **Vấn đề 1 (Transaction Boundary):** `TenantService` chỉ gọi `session.flush()` (phụ thuộc `get_db()` ở router commit), trong khi `AuthService` và `DocumentService` gọi `db.commit()` trực tiếp trong Service. Nếu test hoặc background job gọi `TenantService` qua session riêng, dữ liệu có thể bị rollback âm thầm.
- **Vấn đề 2 (`department_id` Post-Filtering trong `SearchService`):** Trong [search_service.py](backend/app/services/search_service.py), FAISS `search_mmap` tìm `top_k * 2` vector trên toàn Tenant trước, sau đó mới lọc `department_id` ở tầng Python (`if child.document.department_id != request.department_id: continue`). Nếu một phòng ban nhỏ chỉ chiếm 5% tài liệu nhưng toàn công ty có 10.000 chunks, `top_k * 2` vector hàng đầu có thể thuộc hết về các phòng ban khác $\rightarrow$ trả về **0 kết quả** dù phòng ban đó có tài liệu khớp chính xác!
- **❓ Câu hỏi chất vấn App Owner (Performance & Accuracy):**
  > *"Làm thế nào giải quyết bài toán **Pre-filtering vs Post-filtering** cho `department_id` trong FAISS mmap mà không cần phải tách file `.index` riêng cho từng phòng ban (tránh bùng nổ số lượng file descriptor)?"*

- **✅ Trạng thái Khắc phục Toàn bộ Bug 06 $\rightarrow$ Bug 11:** Toàn bộ 7 điểm sửa (khôi phục `router.py`, siết RBAC `require_superuser` trên `/tenants`, chặn `Tenant.is_active == False`, bắt lỗi parser trả HTTP 400, sửa `AsyncSessionLocal` & `asyncio.to_thread` trong MCP CLI, chặn silent mock fallback ở `LLMService`, đồng bộ `FaissService.remove_vectors` + SQL `department_id` pre-filtering) đã được vá hoàn chỉnh và kiểm chứng đạt **51/51 endpoint E2E PASSED** & **43/43 pytest PASSED** ([endpoint_test_report_2026-11-25.md](endpoint_test/endpoint_test_report_2026-11-25.md)).

---

## PHẦN 2: TỐI ƯU HÓA HIỆU NĂNG & RÀNG BUỘC PHẦN CỨNG (PERFORMANCE & RESOURCE TUNING)

### ⚡ Perf 01: Bài toán Giới hạn RAM 1.0 GB trên GCP e2-micro
- **Bối cảnh:** VPS Always Free của GCP chỉ cung cấp 1 vCPU và **1.0 GB RAM**. Nếu server bị OOM (Out Of Memory), Linux Kernel OOM Killer sẽ lập tức "bắn hạ" tiến trình PostgreSQL hoặc FastAPI.
- **Bộ nhớ thực tế phân bổ:**
  - OS Ubuntu 22.04 Minimal: ~150 MB
  - PostgreSQL 16 (Tuned): ~130 MB (`shared_buffers = 128MB`, `work_mem = 4MB`)
  - FastAPI 1 Async Worker: ~120 - 180 MB
  - FAISS mmap (OS Page Cache): < 50 MB RAM Heap
  - Bộ nhớ đệm dự phòng Swapfile 4GB: Đảm bảo server không bao giờ bị kernel kill khi có burst traffic.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao FastAPI chỉ được chạy duy nhất **1 async worker** trên máy chủ này mà không chạy 4 workers theo công thức `(2 x CPU) + 1`? Cơ chế Event Loop của Asyncio xử lý hàng nghìn I/O request đồng thời bằng 1 thread duy nhất như thế nào?"*

---

### ⚡ Perf 02: Kích thước Connection Pool (Pool Sizing) CSDL
- **Thiết lập:** Trong `backend/app/core/database.py`, chúng ta cố định:
  ```python
  pool_size = 10
  max_overflow = 5
  pool_timeout = 30.0
  pool_pre_ping = True
  ```
- **Bản chất kỹ thuật:**
  - Mỗi kết nối PostgreSQL tiêu tốn khoảng **5 MB – 10 MB RAM** trên máy chủ CSDL.
  - Nếu mở `pool_size = 50`, riêng CSDL đã ngốn >350 MB RAM $\rightarrow$ làm sập server 1GB RAM!
  - Nhờ FastAPI sử dụng non-blocking async (`asyncpg`), 10 kết nối có thể luân phiên phục vụ hàng trăm requests mỗi giây (thời gian chiếm giữ kết nối chỉ vài mili-giây cho mỗi câu truy vấn).
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Điều gì xảy ra nếu có 100 requests đồng thời gửi đến hệ thống khi `pool_size=10` và `max_overflow=5`? Khi nào người dùng sẽ nhận lỗi HTTP 500 / Database Timeout và bạn xử lý hàng đợi (Backpressure) như thế nào?"*

---

### ⚡ Perf 03: Hiệu năng Bảng Thu hồi Token (`revoked_tokens`)
- **Bối cảnh:** Trong kiến trúc JWT stateless, việc kiểm tra token đã bị thu hồi (Blacklist) ở mỗi request có thể biến thành điểm nghẽn hiệu năng (Database Bottleneck).
- **Giải pháp:**
  1. Đánh Index duy nhất trên cột `jti` (`ix_revoked_tokens_jti`) giúp tốc độ tra cứu $O(\log N) < 0.5\text{ ms}$.
  2. Cấu hình bảng ở chế độ `UNLOGGED` trong PostgreSQL production (không ghi nhật ký WAL, tăng tốc độ ghi `INSERT` lên 300%).
  3. Lập chỉ mục trên `expires_at` để tiến trình cronjob dọn rác (Pruning) xóa các token đã quá hạn chỉ với 1 câu lệnh nhanh gọn: `DELETE FROM revoked_tokens WHERE expires_at < NOW()`.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Khi server bị mất điện đột ngột hoặc khởi động lại, dữ liệu trên bảng `UNLOGGED` sẽ bị xóa trắng. Liệu điều này có tạo ra lỗ hổng bảo mật cho phép các token cũ đã thu hồi dùng lại được không? Bạn đối phó với rủi ro này bằng cách nào?"*

---

### ⚡ Perf 04: Ephemeral Ingestion (Zero Raw Storage)
- **Bối cảnh:** Ổ cứng GCP chỉ có 50GB. Nếu lưu file gốc PDF, Word dung lượng lớn, hệ thống sẽ nhanh chóng cạn kiệt bộ nhớ đĩa.
- **Giải pháp:**
  - File tải lên được đọc theo từng chunk nhỏ qua memory stream.
  - Tính mã băm SHA-256 ngay trong lúc đọc luồng.
  - Bóc tách toàn bộ `raw_text` sạch và metadata.
  - Hủy ngay lập tức buffer nhị phân gốc khỏi bộ nhớ, không bao giờ ghi xuống đĩa SSD.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Nếu 5 người dùng cùng lúc tải lên 5 file tài liệu PDF nặng 50MB, làm thế nào bạn đảm bảo server không bị tràn RAM bộ nhớ đệm (Peak Memory Spike)?"*

---

### ⚡ Perf 05: Hierarchical Chunking (Parent-Child) & Vector Retrieval
- **Bối cảnh:** Nghịch lý cốt lõi của RAG truyền thống:
  - Chunk nhỏ (100–200 tokens): Khớp vector rất chuẩn, nhưng khi gửi vào LLM thì thiếu ngữ cảnh bao quát.
  - Chunk lớn (1000–2000 tokens): Đủ ngữ cảnh cho LLM, nhưng vector bị pha loãng (Noise), giảm độ chính xác tra cứu.
- **Giải pháp:** **Hierarchical Chunking**:
  - Cắt `Parent Chunk` (~1000 tokens) lưu trữ trong PostgreSQL.
  - Cắt nhỏ thành các `Child Chunks` (~200 tokens) gắn khóa ngoại `parent_id` và đưa vào FAISS mmap để tìm kiếm vector.
  - Khi tra cứu: Khớp vector bằng Child Chunk, nhưng nạp văn bản của **Parent Chunk** vào Prompt!
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao Hierarchical Chunking giải quyết triệt để hiện tượng 'Lost in the Middle' và giảm thiểu ảo giác (Hallucination) cho LLM?"*

---

## PHẦN 3: BỘ CÂU HỎI BẢO VỆ DỰ ÁN THEO TỪNG SPRINT (SPRINT DEFENSE CHECKLIST)

### 🛡️ Sprint 1: CSDL & Cấu trúc Dự án
1. Tại sao lại chọn **Modular Monolith** thay vì Microservices cho dự án này?
2. Tại sao `expire_on_commit` bắt buộc phải đặt là `False` trong SQLAlchemy Asyncio?
3. Ràng buộc `UniqueConstraint("user_id", "tenant_id")` trong bảng `memberships` giải quyết bài toán nghiệp vụ gì?

### 🛡️ Sprint 2: Xác thực & Tenant Context
1. Cơ chế **Token Rotation** và **Token Reuse Detection** bảo vệ hệ thống khỏi các cuộc tấn công đánh cắp Refresh Token như thế nào?
2. Tại sao mật khẩu bắt buộc phải dùng **Bcrypt work factor 12** thay vì SHA-256 hay MD5?
3. Giả sử người dùng muốn thuộc nhiều Tenant khác nhau (Multi-membership), quy trình chuyển đổi Context diễn ra như thế nào?

### 🛡️ Sprint 3: Ingestion & Hierarchical Chunking (Đang thực hiện)
1. Thuật toán Semantic Distance Chunking hoạt động ra sao nếu không dùng LLM để chia đoạn?
2. Làm thế nào để loại bỏ các ký tự rác, watermarks, hoặc bảng biểu bị vỡ khi trích xuất text từ file PDF scan?
3. Tại sao kiểm tra trùng lặp mã băm SHA-256 trong CSDL lại nhanh hơn kiểm tra so sánh nội dung văn bản?

---

## PHẦN 4: LỜI GIẢI ĐÁP & HƯỚNG DẪN BẢO VỆ CHUYÊN SÂU TỪ TECH LEAD

### 💡 Lời giải cho Câu 1: Tại sao SQLAlchemy Async ném lỗi `MissingGreenlet` và `selectinload` giải quyết ra sao?
- **Bản chất `MissingGreenlet`:** Trong mô hình async, event loop là một luồng (thread) duy nhất. Mọi tác vụ I/O mạng hoặc đĩa bắt buộc phải được `await`. Khi lập trình viên truy cập một quan hệ chưa nạp (ví dụ `doc.parent_chunks`), SQLAlchemy theo thói quen đồng bộ (sync) sẽ cố gắng tạo một socket query tức thì. Vì không có từ khóa `await` nào ở cú pháp truy cập thuộc tính (`.`), engine async phát hiện I/O bị "lén lút" thực thi ngoài luồng `greenlet` và ném ngay `MissingGreenlet` để ngăn chặn việc block toàn bộ server!
- **Tại sao chọn `selectinload()` thay vì `joinedload()`:**
  - `joinedload()` sinh câu lệnh `SELECT ... LEFT OUTER JOIN ...`. Khi một Document có 10 Parent Chunks, và mỗi Parent Chunk có 5 Child Chunks, câu lệnh JOIN sẽ nhân chéo các dòng tạo ra **hiện tượng tích Descartes (Cartesian Product Explosion)**. Dữ liệu văn bản của Document bị lặp lại 50 lần trong gói tin gửi từ PostgreSQL về FastAPI, gây lãng phí băng thông và tràn RAM server 1.0 GB!
  - `selectinload()` sinh 2 câu lệnh SQL riêng biệt: Câu 1 lấy Document, Câu 2 lấy các Chunks bằng toán tử tối ưu `WHERE document_id IN (...)`. Không có dòng nào bị nhân chéo, dữ liệu được ánh xạ sạch sẽ vào RAM, tiêu tốn ít bộ nhớ hơn 70% so với `joinedload()`.

### 💡 Lời giải cho Câu 2: Tại sao áp dụng Hierarchical Chunking (Child cho FAISS, Parent cho LLM)?
- **Lời bảo vệ của App Owner (Chính xác 100%):**
  > *"Chúng ta cắt đoạn theo cấu trúc ngữ nghĩa và phân cấp Heading (tiêu đề, phân mục, trang) để tránh việc cắt đứt đôi một câu quan trọng ở giữa chừng, làm mất đi tính toàn vẹn của thông tin. Khi tìm kiếm trên FAISS mmap, chúng ta tìm bằng Child Chunk (~200 tokens) vì đoạn văn ngắn có mật độ từ khóa và vector tập trung cao, không bị pha loãng. Nhưng khi nạp vào Prompt của Gemma, chúng ta nạp Parent Chunk (~1000 tokens) để cung cấp ngữ cảnh đầy đủ, giúp mô hình hiểu sâu và triệt tiêu hoàn toàn ảo giác (Hallucination)!"*

---

### 🛡️ Sprint 4: FAISS mmap Engine & Hybrid Search RRF (Đang thực hiện)
1. **Tại sao lại dùng FAISS với cờ `IO_FLAG_MMAP` thay vì nạp toàn bộ Vector Index vào RAM?**
   - Cơ chế `mmap` (Memory-Mapped Files) của Linux cho phép hệ điều hành ánh xạ trực tiếp file index từ ổ đĩa SSD vào không gian địa chỉ ảo của tiến trình.
   - Dữ liệu vector được OS Page Cache nạp lười (lazy load) từng trang khi cần. Mức tiêu thụ RAM Heap của Python gần như **0 MB**!
2. **Tại sao phải kết hợp cả Vector Search (Dense) và Keyword Search (Sparse - FTS) qua thuật toán RRF?**
   - Vector search rất giỏi tìm kiếm ý nghĩa ngữ nghĩa (ví dụ "quy định thôi việc" $\leftrightarrow$ "chính sách nghỉ việc"), nhưng cực kỳ dở khi tìm kiếm từ khóa chính xác, mã văn bản (ví dụ "QĐ-2024/01", "Thông tư 15/TT-BTC").
   - FTS trên PostgreSQL bù đắp hoàn hảo điểm yếu này. Thuật toán RRF ($k=60$) kết hợp hai bảng xếp hạng một cách công bằng mà không cần phải chuẩn hóa thang điểm (Score Normalization) phức tạp.

---

### 🛡️ Sprint 5: Custom Hand-Coded MCP Server & Tool Authorization
1. **Model Context Protocol (MCP) giải quyết vấn đề gì khác biệt so với một REST API thông thường?**
   - **REST API:** Client phải tự tìm hiểu tài liệu (Swagger/OpenAPI), hardcode đường dẫn endpoint, methods, headers và payload schema. Nếu API thay đổi, code của client bị gãy.
   - **MCP:** Cung cấp cơ chế **Tự khám phá (Tool Discovery)** theo chuẩn JSON-RPC 2.0. Khi AI Agent kết nối, nó tự động gọi `tools/list` để nhận JSON Schema mô tả chi tiết chức năng, ý nghĩa từng tham số và kiểu dữ liệu. LLM tự động suy luận để quyết định khi nào cần gọi Tool, truyền tham số gì và tự động sửa lỗi nếu dữ liệu chưa khớp!
2. **Tại sao việc tự code tay MCP Server (thay vì dùng thư viện sẵn) lại giúp bảo vệ kiến trúc Multi-Tenant?**
   - Hầu hết các thư viện MCP mã nguồn mở đều mặc định chạy ở chế độ Single-User (chạy local cho 1 người trên Desktop).
   - Khi tự tay viết engine JSON-RPC 2.0, chúng ta tích hợp được tầng xác thực **TenantContext** ngay trước khi router thực thi `tools/call`. Điều này triệt tiêu hoàn toàn nguy cơ một Agent bên ngoài gọi Tool để đánh cắp tri thức của Tenant khác (BOLA/IDOR Prevention).

---

### 🛡️ Sprint 6: Context Filtering, In-Memory Quota Manager & Gemma RAG
1. **Tại sao Context Filtering 3 tầng lại giúp tăng tốc độ phản hồi (TPS) và triệt tiêu hiện tượng "Lost in the Middle"?**
   - **Về mặt hiệu năng (TPS):** Thời gian sinh token đầu tiên (TTFT) của LLM tỷ lệ thuận với độ dài prompt đầu vào. Giảm 70% số token (từ 3.500 xuống < 1.000 tokens) giúp thời gian sinh giảm từ 4.5s xuống < 1.2s, cho phép phục vụ nhiều người dùng hơn trên cùng một giới hạn TPM (16K TPM).
   - **Về mặt độ chính xác (Lost in the Middle):** Các mô hình Transformer thường chú ý mạnh nhất vào đầu và cuối prompt, dễ bỏ qua các chi tiết quan trọng nằm ở giữa các đoạn văn dài. Tầng lọc câu trọng tâm (Tier 3) cô đọng chính xác câu chứa chứng cứ đặt sát cạnh câu hỏi, giúp mô hình bắt đúng ý và triệt tiêu ảo giác.
2. **Tại sao dùng Token Bucket trong bộ nhớ (In-memory) thay vì cài Redis?**
   - Máy chủ GCP Always Free chỉ có 1.0 GB RAM. Việc cài Redis ngốn thêm 40MB - 80MB RAM tĩnh.
   - Vì ứng dụng chạy 1 worker FastAPI duy nhất, biến singleton `QuotaManager` trong bộ nhớ Python với khóa `asyncio.Lock` hoạt động an toàn 100%, không xảy ra race condition, tốc độ xử lý là $O(1)$ chỉ mất ~0.005 mili-giây, nhanh hơn 50 lần so với việc gọi Redis qua TCP socket!

---

### 🛡️ Sprint 7: Three-Tier Dashboards & Streaming SSE
1. **Tại sao lại dùng Server-Sent Events (SSE) cho Chatbot AI thay vì WebSocket?**
   - **Bản chất giao tiếp:** Chatbot AI là dạng tương tác bán song công (Half-duplex): Client gửi 1 HTTP POST prompt lên, server truyền luồng nhiều tokens về. Không cần thiết lập kết nối 2 chiều liên tục (Full-duplex) như WebSocket.
   - **Tương thích hạ tầng:** SSE chạy trên nền HTTP chuẩn, tự động tận dụng được Nginx reverse proxy, HTTP/2 multiplexing, cơ chế reconnect tự động qua trường `retry`, và không bị chặn bởi tường lửa hoặc proxy công ty.
2. **Nguyên tắc bảo vệ dữ liệu khách hàng (SEC-026) được thể hiện ra sao trong Platform Admin Dashboard?**
   - Platform Admin (Super-Admin) chỉ được phép xem các chỉ số đếm tổng hợp (Aggregate Metrics) như: tổng số tenants, số user, tổng dung lượng byte.
   - Hệ thống tuyệt đối **không cung cấp bất kỳ API nào** cho phép Platform Admin đọc trộm nội dung tài liệu, tên file chi tiết hoặc lịch sử chat của từng doanh nghiệp.

---

### 🛡️ Sprint 8: Docker Hóa, Tinh chỉnh Kernel & Giới hạn RAM trên GCP
1. **Tại sao cần thiết lập `vm.swappiness = 10` trên VPS GCP 1.0 GB RAM?**
   - Mặc định của Linux là `swappiness = 60`, khiến kernel tích cực đẩy dữ liệu ra đĩa ngay cả khi RAM vẫn còn trống.
   - Đặt `vm.swappiness = 10` ép kernel giữ dữ liệu trên RAM vật lý, chỉ kích hoạt swap khi RAM thực sự chạm ngưỡng nguy hiểm (< 10%). Điều này bảo vệ tốc độ phản hồi TTFT < 1.5s trong khi vẫn có 4GB Swap cứu nguy khi có đột biến tải.
2. **Tại sao cần thiết lập `limits: memory` trong Docker Compose?**
   - Nếu không có giới hạn, một câu truy vấn nặng có thể ngốn hết RAM vật lý, khiến Linux Kernel OOM Killer bắn hạ ngẫu nhiên tiến trình quan trọng (như PostgreSQL hoặc SSH daemon). Giới hạn cứng 350M cho backend và 300M cho CSDL đảm bảo toàn bộ hệ thống luôn vận hành ổn định trong ngân sách 1.0 GB.

### ⚡ Perf 06: Thiết kế Nén Ngữ Cảnh & Kiểm soát Trần 16K TPM của Gemma 4 26B
- **Bối cảnh:** Mô hình `gemma-4-26b-a4b-it` có trần giới hạn **16.000 Tokens Per Minute (16K TPM)** và **30 RPM**. Nếu nạp nguyên `5 Parent Chunks` thô (~1.000 tokens/chunk), một câu hỏi sẽ ngốn 3.500–5.000 input tokens $\rightarrow$ chỉ 3–4 câu hỏi trong 1 phút đã kịch trần 16K TPM và gây lỗi `429`!
- **Giải pháp đã triển khai:**
  1. **Giảm `top_k` mặc định (`5` $\rightarrow$ `3`, trần tối đa `10`)** và khóa cứng **tối đa 3 chunks** đưa vào prompt của Gemma (`DEFAULT_MAX_CHUNKS = 3`) ngay cả khi API vẫn trả đầy đủ `retrieved_chunks` để audit/hiển thị trích dẫn.
  2. **Tầng 1 (Dynamic RRF Cutoff):** Ngoài ngưỡng tuyệt đối `0.005`, loại bỏ các chunk đuôi có điểm `rrf_score < 0.55 * top_score` để không nạp đoạn nhiễu vào LLM.
  3. **Tầng 2 (Markdown Cleanup & Cross-Chunk Dedup):** Chuẩn hóa bớt ký tự Markdown thừa (`#`, `**`), tách theo cả dấu câu lẫn dòng gạch đầu dòng (`\n+`), và khử trùng lặp cả trường hợp câu ngắn nằm lọt trong câu dài của chunk trước.
  4. **Tầng 3 (Child-Guided Sentence Scoring & Hard Token Budget):** Bỏ luật ngoại lệ `len(sentences) <= 3` cũ (vốn làm lọt câu rác); chấm điểm từng câu theo từ khóa query, bigram, neo `child_content`, và boost câu chứa số liệu khi query hỏi số lượng; đóng gói theo thứ tự tài liệu và **khóa cứng `max_context_tokens = 550`**.
  5. **Tinh gọn `PromptService.SYSTEM_PROMPT`:** Rút gọn chỉ thị hệ thống từ ~260 tokens xuống ~107 tokens (tiết kiệm ~59%) nhưng vẫn giữ nguyên đủ 3 luật Fail-Closed (chỉ dùng `<context>`, trích dẫn `[Nguồn: ..., Phân mục: ...]`, câu từ chối chuẩn).
  6. **Cơ chế Đặt cọc & Hoàn trả TPM (`reconcile_tokens`):** Trước khi tìm kiếm, đặt cọc trần dự kiến để chặn spam tức thì; ngay sau khi nén ngữ cảnh, hoàn trả phần token tiết kiệm được về lại Token Bucket 16K TPM.
- **❓ Câu hỏi chất vấn App Owner:**
  > *"Tại sao không giữ toàn bộ Parent Chunk mà lại nén theo từng câu có neo Child Chunk trước khi gửi cho Gemma 4 26B? Việc hoàn trả token qua `reconcile_tokens()` giúp hệ thống xử lý nhiều câu hỏi đồng thời hơn dưới giới hạn 16K TPM như thế nào?"*

*(Các câu hỏi cho Sprint 4 đến Sprint 10 sẽ được bổ sung theo tiến độ thực thi.)*

---

## PHẦN 5: NHẬT KÝ TRIỂN KHAI PRODUCTION & BÀI HỌC XƯƠNG MÁU (DEPLOYMENT RETROSPECTIVE)

> **Bối cảnh:** triển khai EKP lên GCP e2-micro (1 GB RAM, 30 GB disk) ngày 2026-11-26: backend HTTPS qua DuckDNS + Let's Encrypt, frontend Vercel, image build local rồi push Docker Hub, VPS chỉ pull. Đây là các lỗi thật đã gặp, bằng chứng đo được và cách xử lý — không phải mô phỏng.

### 5.1 Kết quả đo được

| Hạng mục | Kết quả thực tế |
|---|---|
| Image backend / nginx | 584 MB / 74,7 MB; tag `2.1.0` + `latest`; kiến trúc `linux/amd64` |
| VPS disk | 30 GB; sau khi xóa waterbase còn ~20 GB trống |
| Pull + start | 3 image (backend, nginx, `postgres:16-alpine`), stack healthy |
| HTTPS | Let's Encrypt cert hạn 2027-02-24; `certbot.timer` active |
| Health | `database:connected`, `faiss_storage_ready:true`, `llm_real_configured:true`, `embedding_real_configured:true` |
| Frontend → backend | `POST /api/v1/auth/register` → `201`; ACAO đúng origin Vercel |
| Clean clone frontend | `npm ci && npm run build` → 1.995 modules, build thành công |
| RLS | role `saas_app` thấy 0 dòng khi thiếu tenant context (fail-closed) |

### 5.2 Bài học 01 — `.gitignore` sai phạm vi làm Vercel build fail

**Triệu chứng:** local `npm run build` pass; Vercel clean clone báo hàng loạt:

```text
Cannot find module '@/lib/types' or its corresponding type declarations
Cannot find module '@/lib/utils' ...
```

**Nguyên nhân:** `.gitignore` có pattern Python `lib/` (không có `/` ở đầu). Git hiểu là ignore **mọi thư mục tên `lib` ở mọi cấp**, gồm `frontend/src/lib/`. Bảy file `api-client.ts`, `api.ts`, `types.ts`, `utils.ts`, `labels.ts`, `navigation.ts`, `session.ts` chưa từng được commit. Local có file nên không thấy lỗi.

**Cách sửa:**

```gitignore
/lib/
/lib64/
```

Commit đủ 7 file rồi kiểm chứng bằng clone sạch:

```bash
git clone <repo> /tmp/ekp-check
cd /tmp/ekp-check/frontend
npm ci && npm run build
```

**Bài học:**
- Pattern không có `/` đầu là recursive; pattern có `/` cuối chỉ khớp thư mục.
- Luôn build từ **clean clone** trước khi tin kết quả local/CI; dùng `git status --ignored` để soi file bị bỏ sót.
- Nên thêm job CI `npm ci && npm run build` cho frontend để bắt lỗi này sớm.

### 5.3 Bài học 02 — Vite env, CSP, CORS và lỗi 405

**Triệu chứng:** bấm đăng ký trên Vercel, request đi tới
`https://<project>.vercel.app/api/v1/auth/register` → `405 Method Not Allowed`;
response có `content-disposition: inline; filename="index.html"`.

**Chẩn đoán:**
- `405 + index.html` là SPA fallback của Vercel, **không phải lỗi CORS**.
- Nguyên nhân: build Vercel không có `VITE_API_BASE_URL`, nên `API_BASE = ""` → gọi same-origin. Vite chỉ đọc biến `VITE_*` **lúc build**.
- CORS backend thực tế đã đúng: preflight `OPTIONS` trả `200` + `Access-Control-Allow-Origin` đúng origin Vercel.

**Cách sửa:**
1. Vercel → Settings → Environment Variables → **Production**:
   `VITE_API_BASE_URL=https://<ten>.duckdns.org`.
2. Redeploy, không dùng build cache cũ.
3. `frontend/vercel.json` CSP phải có:
   `connect-src 'self' https://<ten>.duckdns.org`.

**Bài học — phân biệt 3 tầng:**

| Hiện tượng | Bản chất | Cách kiểm tra |
|---|---|---|
| 405 + `index.html`, request tới domain frontend | Sai `VITE_API_BASE_URL` (same-origin) | DevTools → Network → Request URL |
| Trình duyệt báo CORS, request tới backend | Backend thiếu ACAO/preflight | `OPTIONS` với `Origin` + `Access-Control-Request-Method` |
| CSP violation `connect-src` | `vercel.json` thiếu host backend | DevTools Console + header CSP |

- `VITE_*` là build-time; đổi env phải build lại.
- CSP là tĩnh trong `vercel.json`; CORS là runtime ở backend.
- Domain `https://<project>.vercel.app` có thể đã thuộc app khác; luôn lấy đúng domain trong tab **Domains** của project.

### 5.4 Bài học 03 — Build local, push registry, VPS chỉ pull

**Kiến trúc đã chọn:**

```text
Local: docker build backend + nginx → docker push :TAG + :latest
VPS:   docker compose pull → up -d (KHÔNG build, KHÔNG npm/pip)
```

**Vì sao không build trên e2-micro:**
- 1 GB RAM: `npm ci` + `tsc` + `vite build` và `pip install faiss-cpu` dễ OOM, phải bật swap 4 GB và rất chậm.
- Image là artifact bất biến: cùng digest chạy local test và VPS, dễ rollback.
- VPS không cần toolchain Node/gcc/Python headers → giảm bề mặt tấn công.

**Bài học vận hành:**
- `docker-compose.registry.yml` không có mục `build`, đặt `name: ekp`, container name cố định để TLS script tìm đúng.
- **Dọn VPS phải có phạm vi:** script mặc định chỉ xóa `saas_*`/`ekp_*`; `--all` sẽ xóa mọi project Docker. VPS đang chạy waterbase — phải hỏi và xác nhận trước khi xóa.
- `docker compose down -v` xóa **volume PostgreSQL/FAISS** = mất dữ liệu. Backup trước; không xóa `/etc/letsencrypt` và `/etc/duckdns`.
- Kiểm tra cổng trước khi deploy: `sudo ss -ltnp | grep -E ':(80|443)\b'`. Container nginx của project cũ giữ cổng 80 là lỗi phổ biến.
- Cập nhật phiên bản: build tag mới → push → sửa `IMAGE_TAG` trong `.env` → `deploy_vps.sh`; volume/cert giữ nguyên. Rollback bằng tag cũ, không rebuild.
- Docker Hub public chứa toàn bộ source backend trong image; nếu cần giấu source, dùng GHCR private.

### 5.5 Bài học 04 — Env production fail-closed

Các lỗi cấu hình thật đã gặp:

- `SECRET_KEY` 23 ký tự bị backend từ chối vì `< 32`; phải dùng chuỗi random ≥ 32 ký tự (file chuẩn dùng 64 hex).
- Thiếu `APP_DB_PASSWORD` → Compose fail ngay ở interpolation (`APP_DB_PASSWORD must be set`).
- `POSTGRES_SERVER=localhost` là bẫy dev; trong Docker network phải là `postgres`.
- `DB_RLS_ENABLED=True`; role ứng dụng `saas_app` phải `NOSUPERUSER/NOBYPASSRLS`; init role chỉ chạy khi volume PostgreSQL còn trống.
- `.env` phải `chmod 600`; không commit. Chỉ `.env.example` được public.
- Đổi `POSTGRES_PASSWORD`/`APP_DB_PASSWORD` sau khi volume đã khởi tạo không đủ: phải `ALTER ROLE` trong PostgreSQL rồi restart backend, nếu không sẽ mất kết nối DB.
- File `.env_vps` từng nằm trong working tree; phải ignore rõ ràng (`.env_vps`, `.env_vps.*`) và kiểm tra `git ls-tree -r origin/main` chỉ có file `.example`.

### 5.6 Bài học 05 — DuckDNS & Let's Encrypt

- Đăng nhập DuckDNS bằng GitHub **không phải email**. Let's Encrypt cần email thật; `neshaki@github` không hợp lệ.
- Token DuckDNS là UUID **36 ký tự** (8-4-4-4-12). Token 35 ký tự do gõ thiếu → API trả `KO`; phải copy, không gõ tay.
- `DUCKDNS_DOMAIN` chỉ là subdomain, **không** kèm `.duckdns.org`.
- Reset token làm token cũ chết ngay; phải cập nhật `/etc/duckdns/duckdns.env` rồi restart service.
- Cột `current ip` trên web DuckDNS là IP của **trình duyệt** đang mở trang, không chắc là IP VPS. Chỉ tin updater chạy trên VPS.
- HTTP-01 cần cổng 80; `issue_tls.sh` kiểm tra DNS trước khi xin cert để tránh đốt rate limit.
- Kiểm tra TLS qua loopback (dùng `--resolve` trỏ domain về máy local) **không chứng minh** cổng 443 mở từ Internet. Phải test từ laptop/browser và kiểm tra **GCP VPC firewall** (UFW chỉ là lớp trong máy).
- Cert name cố định `saas-backend`; `certbot.timer` tự renew; chạy `certbot renew --dry-run` sau khi deploy.

### 5.7 Bài học 06 — Bảo mật trong lúc vận hành

- Ảnh chụp tab SSH có `.env` làm lộ `SECRET_KEY`, `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `GOOGLE_API_KEY`. Coi như đã lộ: rotate Google API key và `SECRET_KEY` ngay; DB password chỉ rotate nếu cần (DB không publish ra Internet).
- Không chụp màn hình `.env`, không mở tab SSH-in-browser chứa secret khi screen-share.
- DuckDNS token từng lộ trong ảnh: recreate token, cập nhật `/etc/duckdns/duckdns.env`, restart updater.
- Image public trên Docker Hub chứa source backend. Đây là trade-off chấp nhận cho portfolio; nếu cần private, chuyển GHCR.
- `.dockerignore` phải chặn `.env`, `*.pem`, `*.key`, `backend/storage`, tests/reports. Kiểm tra image bằng `docker run --rm <image> env`/`find` nếu nghi ngờ.

### 5.8 Checklist tái sử dụng (một trang)

**Trước deploy:**
- [ ] Clean clone build frontend: `npm ci && npm run build`.
- [ ] `pytest backend/tests -q` pass.
- [ ] `.env` có đủ secret, `chmod 600`; `SECRET_KEY` ≥ 32; `APP_DB_PASSWORD` không rỗng.
- [ ] `DOCKER_IMAGE_PREFIX` + `IMAGE_TAG` trỏ đúng image đã push.
- [ ] Cổng 80/443 trống; UFW và GCP VPC firewall mở 80/443.
- [ ] DNS trỏ đúng IP VPS; token DuckDNS đúng 36 ký tự.
- [ ] Đĩa còn đủ cho image + DB + swap + backup.

**Deploy:**
- [ ] `bootstrap_vps.sh` hoặc `deploy_vps.sh` pull/up thành công.
- [ ] `/health` healthy, `llm_real_configured:true`.
- [ ] TLS cấp thành công; test HTTPS từ ngoài.
- [ ] CORS preflight đúng origin Vercel.
- [ ] Vercel có `VITE_API_BASE_URL` và CSP đúng host backend.

**Sau deploy:**
- [ ] Đăng ký/login; upload; search; chat SSE.
- [ ] Kiểm tra RLS bằng role `saas_app`.
- [ ] `docker stats --no-stream`, `df -h`, `free -h`.
- [ ] `certbot renew --dry-run`.
- [ ] Rotate mọi secret từng xuất hiện trong ảnh chụp/chia sẻ.

### 5.9 Câu hỏi tự vấn (dùng để bảo vệ trước Tech Lead)

1. Vì sao `npm run build` ở local pass nhưng Vercel clean clone fail? Pattern `.gitignore` nào gây ra và sửa thế nào?
2. Lỗi `405 + index.html` khác CORS và CSP ở chỗ nào? Kiểm tra từng lỗi bằng lệnh/DevTools nào?
3. Vì sao `VITE_API_BASE_URL` đổi xong phải redeploy, còn `CORS_ALLOW_ORIGINS` chỉ cần restart backend?
4. Vì sao không build image trên e2-micro? Nêu 3 lý do về RAM, tốc độ và bề mặt tấn công.
5. `docker compose down -v` xóa những gì? Vì sao không được chạy `--all` khi VPS còn project khác?
6. Token DuckDNS 35 ký tự gây lỗi gì? Vì sao reset token làm updater fail?
7. Vì sao TLS kiểm tra qua loopback vẫn có thể thất bại từ Internet? Lớp firewall nào còn thiếu?
8. `SECRET_KEY` 23 ký tự bị từ chối ở đâu? Cơ chế fail-closed nằm ở file nào?
9. Public Docker Hub image có rủi ro gì về source code? Khi nào nên dùng GHCR?
10. Nếu phát hiện secret trong ảnh chụp, quy trình rotate gồm những bước nào và dịch vụ nào bị ảnh hưởng?
11. Làm sao rollback phiên bản backend mà không rebuild và không mất dữ liệu?
12. Sau lần deploy này, nên thêm check gì vào CI để không tái diễn lỗi frontend thiếu file?

---
