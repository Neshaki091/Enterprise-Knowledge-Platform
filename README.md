# Enterprise Knowledge Platform (EKP)

**Nền tảng tri thức doanh nghiệp đa người thuê (Multi-Tenant RAG SaaS) vận hành trong 1 GB RAM và ngân sách hạ tầng $0.**

[![CI](https://github.com/Neshaki091/Enterprise-Knowledge-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/Neshaki091/Enterprise-Knowledge-Platform/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

EKP giúp nhân viên hỏi đáp tài liệu nội bộ bằng ngôn ngữ tự nhiên, có trích dẫn nguồn, đồng thời cô lập dữ liệu tuyệt đối giữa các phòng ban và các tenant. Toàn bộ hệ thống — FastAPI, PostgreSQL 16, FAISS vector search — chạy gọn trên một máy **GCP Always Free `e2-micro` (2 vCPU shared, 1.0 GB RAM)** nhờ kiến trúc Ultra-Lean: không Redis, không Celery, không MinIO, không vector database độc lập.

---

## Điểm nổi bật

- **Multi-tenant thật** — Row-Level Security (RLS) của PostgreSQL 16 với role ứng dụng `NOSUPERUSER / NOBYPASSRLS`; mọi truy vấn fail-closed nếu thiếu tenant context.
- **Zero Raw Storage** — file PDF/DOCX/XLSX chỉ tồn tại trong RAM lúc ingest; hệ thống lưu `SHA-256`, metadata và `raw_text` trong PostgreSQL rồi hủy file gốc ngay lập tức.
- **Hierarchical Ingestion** — Parent chunk (~1.000 tokens) giữ ngữ cảnh cho LLM, Child chunk (~200 tokens) nạp vào FAISS để tìm kiếm chính xác.
- **FAISS mmap theo tenant** — index mở bằng `faiss.IO_FLAG_MMAP`, tách file `storage/indexes/{tenant_id}.index`; RAM ứng dụng không phình theo kích thước corpus.
- **Hybrid Search + RRF** — hợp nhất tìm kiếm vector (FAISS) và từ khóa toàn văn (PostgreSQL FTS) bằng Reciprocal Rank Fusion.
- **Context Filtering 3 tầng** — Dynamic Thresholding → Deduplication → Sentence Compression, giảm 60–70% token đầu vào để nằm trong hạn mức 16K TPM cấu hình cho LLM.
- **RAG streaming SSE** — trả citations trước, sau đó stream từng token; Nginx tắt `proxy_buffering` để TTFT thấp.
- **Session memory 300 token** — chính lượt LLM trả về bản tóm tắt phiên trong cùng một call, bắt đầu từ câu hỏi đầu tiên; lượt sau đọc lại memory thay vì nhồi raw history.
- **Tài liệu đính kèm riêng trong chat** — file chỉ thuộc owner + session, lưu chunk/embedding trong PostgreSQL với TTL, **không** ghi FAISS và không vào kho tri thức chung; đồng nghiệp/tenant khác không truy xuất được.
- **MCP Server tự viết** — công cụ `search_knowledge_base`, `get_document_context` cho Claude Desktop, có kiểm tra tenant/ACL và bound `top_k`.
- **Frontend 3 tầng vai trò** — Workspace (nhân viên), Tenant Admin, Platform Admin; dashboard realtime qua SSE.
- **Vận hành $0** — Nginx TLS + Let's Encrypt trên e2-micro, SPA tĩnh trên Vercel Hobby, DuckDNS cho tên miền động.

---

## Kiến trúc

```
Trình duyệt
   │  HTTPS (SPA tĩnh)
   ▼
Vercel Hobby  ──── API + SSE (HTTPS) ────►  <ten>.duckdns.org
                                              │
                                              ▼
                                        Nginx 443 (TLS 1.2/1.3)
                                        ├── SPA build tĩnh
                                        ├── /api/v1/*  ──► FastAPI (1 async worker)
                                        └── /health                    │
                                                                       ▼
                                                          PostgreSQL 16 (single instance)
                                                          ├── dữ liệu quan hệ + RLS
                                                          ├── full-text search
                                                          ├── job queue (SKIP LOCKED)
                                                          └── raw_text + metadata
                                                                       │
                                                          FAISS mmap index / tenant
                                                          storage/indexes/{tenant_id}.index
```

**Luồng ingest:** upload → kiểm tra size/MIME → parse trong RAM → SHA-256 chống trùng → chunk parent/child → embed child chunks (Gemini Embedding API) → ghi PostgreSQL + FAISS → hủy file gốc.

**Luồng hỏi đáp:** câu hỏi → kiểm tra quota (30 RPM / 16K TPM mỗi tenant) → hybrid search → lọc ngữ cảnh 3 tầng → prompt Gemini 3.5 Flash Lite → trả lời kèm trích dẫn (JSON hoặc SSE).

---

## Công nghệ

| Lớp | Thành phần |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Alembic |
| CSDL | PostgreSQL 16 (RLS, FTS, `SKIP LOCKED` job queue) |
| Vector | FAISS `faiss-cpu` với `IO_FLAG_MMAP`, index riêng từng tenant |
| AI | Gemini Embedding API + Gemini 3.5 Flash Lite qua Google AI Studio (không nạp model vào RAM server) |
| Frontend | React 19, TypeScript, Vite, TanStack Query, Tailwind CSS, Recharts |
| Hạ tầng | Docker Compose, Nginx (TLS, security headers, rate limit), Let's Encrypt, DuckDNS, Vercel |
| Kiểm thử | pytest + pytest-asyncio (SQLite in-memory), Playwright E2E, bộ endpoint test riêng |

---

## Cấu trúc kho mã

```
backend/
  app/
    api/v1/endpoints/   # auth, tenants, departments, documents, search, chat, mcp, dashboards, health
    core/               # config, database, security, middleware
    models/             # SQLAlchemy models (tenant-scoped)
    services/           # ingestion, chunking, embedding, FAISS, RRF, context filter, LLM, quota, SSE
    mcp/                # MCP server + CLI (stdio)
  tests/                # 52 test backend (SQLite in-memory, AI offline)
frontend/
  src/                  # React SPA: auth, knowledge hub, search, chat SSE, 3-tier dashboards
  e2e/                  # Playwright End-to-End
nginx/                  # dev (HTTP) và prod (TLS, security headers, rate limit)
deploy/                 # runbook GCP e2-micro, DuckDNS, TLS, firewall, local production-stack test
deploy/registry/        # build local → push Docker Hub/GHCR → VPS pull, cleanup VPS
scripts/                # entrypoint, tạo role RLS, swap/kernel, verify staging
endpoint_test/          # bộ kiểm thử endpoint tổng thể (báo cáo sinh theo ngày, không commit)
task/                   # kế hoạch sprint Phase 1–3
SR.md                   # Đặc tả yêu cầu doanh nghiệp (SRS/BRD)
AGENTS.md               # Quy chuẩn coding agent & vận hành
```

---

## Chạy nhanh (Docker)

Yêu cầu: Docker Engine + Compose plugin.

```bash
cp .env.example .env
# Sửa tối thiểu: SECRET_KEY, POSTGRES_PASSWORD, APP_DB_PASSWORD
# (tùy chọn) GOOGLE_API_KEY để bật embedding/LLM thật.

docker compose up -d --build
```

Mở `http://localhost` — SPA và API cùng origin qua Nginx. Tài liệu Swagger bị chặn ở production; khi phát triển có thể gọi thẳng backend trong container.

Kiểm tra sức khỏe:

```bash
curl http://localhost/health
# {"status":"healthy","database":"connected", ...}
```

> Khi `GOOGLE_API_KEY` trống, hệ thống dùng embedding deterministic và trả lời offline — đủ để chạy test và demo luồng RAG mà không gọi API ngoài.

### Frontend dev riêng

```bash
cd frontend
npm install
npm run dev        # Vite dev server, proxy /api về backend
npm run build      # typecheck + build production
```

---

## Kiểm thử

```bash
# Backend: 80 test, SQLite in-memory, không gọi AI API
python -m pytest backend/tests -v

# Frontend E2E (cần stack đang chạy)
cd frontend
npx playwright test
```

Bộ kiểm thử endpoint tổng thể:

```bash
python endpoint_test/run_all_endpoint_tests.py
# Báo cáo: endpoint_test/endpoint_test_report_<YYYY-MM-DD>.md (không commit)
```

---

## Lưu trace hội thoại & xuất RAGAS

- Mỗi lượt chat được lưu vào bảng `chat_turns` (RLS theo tenant): query, answer,
  snapshot context, chunk IDs, citations, model, latency, token.
- Frontend giữ `session_id` trong localStorage theo tenant/user: reload trang tự nạp lại
  các lượt cũ qua `GET /api/v1/chat/history?session_id=...`.
- **Session memory:** ngay từ lượt đầu, LLM trả kèm khối `<session_memory>` (~300 token)
  trong **cùng một call**; backend tách khối này khỏi câu trả lời, lưu vào `chat_turns.session_memory`
  và đưa lại vào lượt sau. Memory chỉ để hiểu câu hỏi nối tiếp, không phải chứng cứ.
  Session cũ/mock chưa có memory sẽ fallback nén extractive 3 lượt gần nhất
  (`CHAT_HISTORY_CONTEXT_TURNS`, `CHAT_HISTORY_CONTEXT_TOKENS`).
- **Hoàn tác lượt cuối:** `POST /api/v1/chat/rollback` xoá lượt gần nhất của phiên
  (chỉ của chính user); memory tự quay về bản của lượt trước và lượt đó cũng biến mất
  khỏi dataset RAGAS.
- **Tài liệu đính kèm chat:** `POST /api/v1/chat/attachments` (multipart, kèm `session_id`),
  `GET /api/v1/chat/attachments`, `DELETE /api/v1/chat/attachments/{id}`. Chỉ owner + session
  đọc được; admin tenant cũng không đọc qua API attachment. File có TTL
  (`CHAT_ATTACHMENT_TTL_HOURS`, mặc định 24h), tối đa 3 file/phiên và 10 file còn hạn/user.
  Tối đa 2 upload xử lý song song (`CHAT_ATTACHMENT_MAX_CONCURRENT_UPLOADS`) để 10 người
  upload cùng lúc không làm OOM backend 350 MB; các request còn lại xếp hàng.
  Chunk + embedding lưu trong PostgreSQL, **không ghi FAISS**; chat tự tìm cosine trong đúng
  tập chunk của phiên nên không làm bẩn index tenant và không cần file index theo user.
  Lượt chat có attachment được đánh dấu `has_attachment` và **loại khỏi export RAGAS**.
  Dọn file hết hạn bằng `scripts/prune_chat_attachments.py` (cron 15–60 phút/lần).
- Xuất dataset theo tháng để chấm RAGAS **bên ngoài** hệ thống: nút **Xuất JSONL.GZ**
  trong Quản trị doanh nghiệp (OWNER/ADMIN), hoặc CLI
  `scripts/export_ragas_dataset.py`; hướng dẫn tại
  [`scripts/README_ragas_export.md`](scripts/README_ragas_export.md).
- File export **nén gzip** (`.jsonl.gz`), mặc định bỏ lượt `mock`/`retrieval_only`.
- Mỗi record có `metadata.has_attachment` và `metadata.session_memory` để phân tích multi-turn;
  memory **không** được đưa vào `retrieved_contexts` và không phải chứng cứ.
- Backend **không cài RAGAS** và không chạy evaluator; file export chứa nội dung
  hội thoại nên không commit. Retention tối đa 365 ngày
  (`CHAT_HISTORY_RETENTION_DAYS`), dọn bằng `scripts/prune_chat_history.py`;
  tắt hoàn toàn bằng `CHAT_HISTORY_ENABLED=false`.

---

## Triển khai production

Runbook một lần cho kiến trúc $0: xem [`deploy/README.md`](deploy/README.md).

**Cách khuyến nghị — VPS không build:** build image backend + nginx trên máy local, push lên Docker Hub hoặc GHCR, VPS chỉ `docker compose pull` và chạy `docker-compose.registry.yml`. Quy trình đầy đủ, gồm dọn sạch Docker cũ trên VPS, nằm tại [`deploy/registry/README.md`](deploy/registry/README.md).

Sau khi có `.env`, một lệnh duy nhất trên VPS:

```bash
sudo bash deploy/registry/bootstrap_vps.sh \
  --domain <ten>.duckdns.org \
  --email <email> \
  --vercel-origin https://<project>.vercel.app
```

Tóm tắt: GCP e2-micro (Ubuntu 22.04) + swap 4 GB → Docker Compose (image từ registry hoặc build tại chỗ) → Nginx TLS 443 + Let's Encrypt (HTTP-01 webroot, tự gia hạn) → DuckDNS tự cập nhật IP → Vercel Hobby chạy SPA tĩnh và trỏ `VITE_API_BASE_URL` về backend HTTPS. Ràng buộc thật sự là **egress 1 GB/tháng** của GCP Always Free.

Các biến bắt buộc phải đổi trước khi lên production: `SECRET_KEY`, `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `CORS_ALLOW_ORIGINS` (đúng một origin Vercel, không wildcard). Backend **fail-closed**: từ chối khởi động nếu cấu hình chưa đạt.

### Kiểm chứng local production-stack (2026-11-26)

Đã chạy đúng cấu hình production bằng Docker tại chỗ (TLS tự ký thay Let's Encrypt):

| Hạng mục | Kết quả |
|---|---|
| HTTP → HTTPS | `301` redirect, ACME path không bị redirect |
| TLS | chỉ 1.2 / 1.3; 1.1 bị từ chối |
| Security headers | HSTS, CSP, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` — mỗi header đúng 1 lần |
| Swagger / OpenAPI | `/docs`, `/redoc`, `/openapi.json` → `404` |
| CORS | đúng origin `200`; origin lạ `400`, không có ACAO |
| SSE | `text/event-stream`, event đầu ~0,04 s |
| Body guard | POST 27 MB → Nginx `413` trước backend |
| Rate limit | 140 request dồn dập → ~125 × `200` + ~15 × `503` (600 r/m, burst 100) |
| RLS fail-closed | role `saas_app` thấy `0` dòng dù bảng có dữ liệu; `FORCE RLS` |
| RAM thực tế | PostgreSQL ~74 MB, backend ~143 MB, Nginx ~3 MB |
| Test | 52/52 backend PASS, Playwright E2E 2/2 PASS |

Các luồng gọi Google AI thật không nằm trong lần đo này (không dùng API key thật trên máy local); chúng được kiểm bằng `scripts/verify_staging.py` trên VM.

---

## Mô hình bảo mật

- **Tenant isolation:** RLS + role ứng dụng không đặc quyền; tenant context lấy từ JWT đã xác thực, không tin header từ client.
- **ACL phòng ban:** mọi truy vấn tài liệu/search/chat/MCP đều đi qua `ensure_department_allowed`.
- **Auth:** JWT access ngắn hạn + refresh token rotation theo family; phát hiện reuse và thu hồi cả family.
- **Fail-closed:** thiếu cấu hình bảo mật hoặc thiếu quyền → từ chối, không chạy ở chế độ mở.
- **Tối thiểu bề mặt:** backend không publish cổng ra host; chỉ Nginx 80/443; Swagger tắt ở production.
- **Chat trace có kiểm soát:** `chat_turns` có RLS theo tenant, retention tối đa 365 ngày,
  prune bằng script local, tắt được bằng `CHAT_HISTORY_ENABLED=false`; file export RAGAS
  (`.jsonl.gz`) không commit và **luôn loại lượt có tài liệu đính kèm private**.
- **Tài liệu đính kèm chat:** RLS tenant + lọc owner/session ở mọi truy vấn; không ghi FAISS;
  TTL ngắn và giới hạn số file/user; nội dung không xuất hiện trong RAGAS export.
- **Dữ liệu nhạy cảm:** không lưu file gốc; không commit `.env`, token, chứng chỉ hay báo cáo test.

Nếu phát hiện lỗ hổng, vui lòng mở issue riêng tư hoặc liên hệ chủ sở hữu thay vì công khai chi tiết khai thác.

---

## Tài liệu

| Tài liệu | Nội dung |
|---|---|
| [`SR.md`](SR.md) | Đặc tả yêu cầu doanh nghiệp (SRS/BRD), ràng buộc phi chức năng |
| [`AGENTS.md`](AGENTS.md) | Quy chuẩn coding agent, ràng buộc kiến trúc, quy trình kiểm thử |
| [`history.md`](history.md) | Thay đổi và lỗi đã sửa theo từng phiên bản |
| [`task/ROADMAP.md`](task/ROADMAP.md) | Lộ trình 3 giai đoạn và toàn bộ sprint |
| [`ANON.md`](ANON.md) | Playbook kiến trúc & bảo mật Ultra-Lean |
| [`DEFENSE_PLAN.md`](DEFENSE_PLAN.md) | Kế hoạch bảo vệ đồ án / phỏng vấn kiến trúc |
| [`deploy/README.md`](deploy/README.md) | Runbook triển khai production $0 |
| [`deploy/local-test/README.md`](deploy/local-test/README.md) | Kiểm thử production stack trên máy local |

---

## Giới hạn đã biết

- Hạ tầng mục tiêu là **1 GB RAM**: số người dùng đồng thời thấp, phù hợp demo/pilot chứ không phải tải production quy mô lớn.
- Gemini 3.5 Flash Lite qua Google AI Studio có hạn mức phụ thuộc model/gói; hệ thống tự áp **30 RPM / 16K TPM** mỗi tenant và trả `429` có kiểm soát thay vì vượt quota.
- Egress GCP Always Free chỉ **1 GB/tháng** — SPA phải ở Vercel, API chỉ trả JSON/SSE.
- Chưa có background worker độc lập: job nặng chạy qua hàng đợi PostgreSQL `SKIP LOCKED` trong tiến trình API.

---

## Giấy phép

Phát hành theo [MIT License](LICENSE) © 2026 Neshaki091.
