# KẾ HOẠCH PHASE 2: ENTERPRISE FRONTEND & REALTIME ADMIN CONSOLE

**Mục tiêu:** Xây dựng giao diện Web hoàn chỉnh cho cả 3 nhóm người dùng (Employee, Business Tenant Admin, Platform Super-Admin), tích hợp đầy đủ 31 API endpoints của Phase 1, bảo đảm chạy mượt mà trong ngân sách **1.0 GB RAM** của GCP `e2-micro`.  
**Thời gian ước tính:** 4 Sprints (4 - 5 tuần)  
**Trạng thái:** ✅ **Hoàn thành (F1–F4)** — Playwright E2E 2/2 PASS, Nginx healthy, SPA production build verified ngày 2026-11-26.

---

## 1. KIẾN TRÚC CÔNG NGHỆ & RÀNG BUỘC HẠ TẦNG (FRONTEND ADR)

- **Stack đề xuất:** **React 19 + TypeScript + Vite + Tailwind CSS + shadcn/ui + TanStack Query + Recharts / ECharts (lightweight)**.
- **Ràng buộc 1.0 GB RAM (Zero Node SSR Runtime):**
  - Tuyệt đối **không** chạy Node.js SSR server (`next start`) trên VPS production vì sẽ ngốn 150–300 MB RAM tĩnh.
  - Biên dịch thành **Static SPA (`dist/`)** ở bước Docker multi-stage build và phục vụ trực tiếp qua **Nginx (`50M` RAM limit)** đã cấu hình sẵn trong [docker-compose.yml](docker-compose.yml).
- **Streaming SSE qua `POST` & Auth Header:**
  - Dùng `fetch()` + `ReadableStream` (thay vì `EventSource` gốc vốn chỉ hỗ trợ `GET` và không gắn được `Authorization: Bearer`) cho `POST /api/v1/chat/stream` và `GET /api/v1/dashboards/*/realtime`.
- **Bảo mật Frontend:**
  - Lưu Access Token (15 phút) trong bộ nhớ ứng dụng (Memory/State), cơ chế Refresh Rotation tự động qua interceptor khi gặp `401`.
  - Sanitize Markdown/HTML khi render câu trả lời LLM (chống XSS), không lưu dữ liệu nhạy cảm của Tenant khác khi chuyển `X-Tenant-ID`.

---

## 2. LỘ TRÌNH 4 SPRINTS CỦA PHASE 2

| Sprint | Trọng tâm | File chi tiết |
|---|---|---|
| **Sprint F1** | Khởi tạo Vite/TS, Auth Flow, JWT Rotation & Multi-Tenant Context Switcher | [sprint-f1-frontend-foundation-auth.md](task/sprint-f1-frontend-foundation-auth.md) |
| **Sprint F2** | Knowledge Base Ingestion, Hybrid Search & RAG Chat SSE (`chunks -> citations -> token -> done`) | [sprint-f2-knowledge-hub-rag-sse.md](task/sprint-f2-knowledge-hub-rag-sse.md) |
| **Sprint F3** | 3-Tier Dashboards + Realtime Analytics, Storage DB/FAISS, API Key/Model Limits & Audit Logs | [sprint-f3-admin-realtime-analytics.md](task/sprint-f3-admin-realtime-analytics.md) |
| **Sprint F4** | Tích hợp Nginx Static Build, Docker Compose, Playwright E2E & Security Hardening | [sprint-f4-frontend-docker-nginx-e2e.md](task/sprint-f4-frontend-docker-nginx-e2e.md) |
