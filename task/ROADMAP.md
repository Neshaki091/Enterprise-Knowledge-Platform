# MASTER SPRINT ROADMAP (KẾ HOẠCH PHÂN BỔ SPRINT)

**Dự án:** Enterprise AI Knowledge Platform (Multi-Tenant SaaS)  
**Mục tiêu:** Backend Core (Phase 1) ➔ Enterprise Frontend & Realtime Admin Console (Phase 2) ➔ Synthetic Workload theo giờ doanh nghiệp, Pentest & Vận hành thật (Phase 3).  
**Thời gian ước tính:** Phase 1 (6 tuần) + Phase 2 (4–5 tuần, đã hoàn thành) + Phase 3 (4–6 tuần).  

---

## TỔNG QUAN CÁC SPRINTS

### 🟢 Phase 1: Backend Core, RAG Pipeline & Low-Memory Hardening
| Sprint | Giai đoạn | Tên Sprint & Trọng tâm | Trạng thái | File chi tiết |
|---|---|---|---|---|
| **Sprint 1** | Phase 1 | Cấu trúc Dự án & PostgreSQL 16 Low-Memory | ✅ **Hoàn thành** | [sprint-1-foundation-db.md](sprint-1-foundation-db.md) |
| **Sprint 2** | Phase 1 | Auth Service, JWT Rotation & Tenant Context | ✅ **Hoàn thành** | [sprint-2-auth-tenant-context.md](sprint-2-auth-tenant-context.md) |
| **Sprint 3** | Phase 1 | Ingestion Zero-Storage & Hierarchical Chunking | ✅ **Hoàn thành** | [sprint-3-ingestion-hierarchical.md](sprint-3-ingestion-hierarchical.md) |
| **Sprint 4** | Phase 1 | FAISS mmap Engine & Hybrid Search RRF | ✅ **Hoàn thành** | [sprint-4-faiss-mmap-hybrid.md](sprint-4-faiss-mmap-hybrid.md) |
| **Sprint 5** | Phase 1 | **Tự viết Custom MCP Server & Tool Authorization** | ✅ **Hoàn thành** | [sprint-5-mcp-server-tools.md](sprint-5-mcp-server-tools.md) |
| **Sprint 6** | Phase 1 | Context Filtering & Gemma 4 26B Integration | ✅ **Hoàn thành** | [sprint-6-context-filtering-gemma.md](sprint-6-context-filtering-gemma.md) |
| **Sprint 7** | Phase 1 | 3-Tier Dashboards, Realtime Analytics & Streaming SSE API | ✅ **Hoàn thành** | [sprint-7-dashboards-sse.md](sprint-7-dashboards-sse.md) |
| **Sprint 8** | Phase 1 | Docker hóa, GCP e2-micro 1GB Setup & Release | ✅ **Hoàn thành (Kết thúc Phase 1)** | [sprint-8-gcp-deploy-hardening.md](sprint-8-gcp-deploy-hardening.md) |

---

### 🔵 Phase 2 (Mới): Enterprise Frontend & Realtime Admin Console
> Kế hoạch tổng thể: [phase-2-frontend-plan.md](phase-2-frontend-plan.md)

| Sprint | Giai đoạn | Tên Sprint & Trọng tâm | Trạng thái | File chi tiết |
|---|---|---|---|---|
| **Sprint F1** | Phase 2 | Frontend Foundation, Auth JWT Rotation & Tenant Switcher | ✅ **Hoàn thành** | [sprint-f1-frontend-foundation-auth.md](sprint-f1-frontend-foundation-auth.md) |
| **Sprint F2** | Phase 2 | Knowledge Hub, Hybrid Search Explorer & RAG Chat SSE | ✅ **Hoàn thành** | [sprint-f2-knowledge-hub-rag-sse.md](sprint-f2-knowledge-hub-rag-sse.md) |
| **Sprint F3** | Phase 2 | 3-Tier Dashboards, Realtime Analytics & Storage/Quota Console | ✅ **Hoàn thành** | [sprint-f3-admin-realtime-analytics.md](sprint-f3-admin-realtime-analytics.md) |
| **Sprint F4** | Phase 2 | Nginx Static Delivery, Docker Compose & Playwright E2E | ✅ **Hoàn thành** | [sprint-f4-frontend-docker-nginx-e2e.md](sprint-f4-frontend-docker-nginx-e2e.md) |

> **Xác minh 2026-11-26:** F1–F4 hoàn tất; Playwright E2E 2/2 PASS; container Nginx healthy; SPA production build phục vụ qua Nginx.

---

### 🟣 Phase 3 (Điều chỉnh theo năng lực hạ tầng thực tế): Synthetic Workload, Pentest & Operations

> **Điều chỉnh 2026-11-26:** Kế hoạch "10.000 VUs" cũ chỉ giữ lại làm bài toán lý thuyết/phản biện. Số đo thực tế: 10.000 users/ngày × 300–400 request ≈ 3–4 triệu request/ngày; riêng HTTP header đã tốn 27–36 GB/tháng, vượt 1 GB egress của GCP Always Free 27–36 lần; quota tenant 30 RPM chỉ chịu ~43.200 request/ngày. Phase 3 vì vậy chạy tải mô phỏng đúng giờ doanh nghiệp: **20 user/ngày, 3–5 user đồng thời, 2 phiên/giờ/user, ~160–200 request/giờ/user** (mỗi user hoạt động 1–2 giờ/ngày ➔ **4.000–8.000 request/ngày**, khung 06:00–22:00, nghỉ 22:00–06:00, trần 25 RPM); **soak gộp 5 ngày làm việc với chính workload này**, không chạy 24/7 riêng; egress dự kiến **0,6–0,8 GB/tháng** (ngân sách chat ≤ 300 lượt/ngày, admin realtime ≤ 1 tab-hour/ngày).
>
> **Topology Phase 3:** Frontend SPA tĩnh trên Vercel (`*.vercel.app`) ➔ gọi API/SSE trực tiếp tới backend GCP `e2-micro` qua Nginx TLS (DuckDNS + Let's Encrypt). Không dùng `http://ExIP` trần vì trình duyệt chặn mixed content; asset tĩnh chuyển sang Vercel giúp giảm egress origin.
>
> **Bộ deploy một lần (đã có trong repo):** [deploy/README.md](deploy/README.md) — DuckDNS updater (systemd timer 5 phút), certbot webroot + deploy hook reload Nginx, `frontend/vercel.json` (SPA fallback, CSP, cache). Cert/DNS tự gia hạn, chạy nhiều tháng không cần cấu hình lại. **Khuyến nghị:** build image local → push registry → VPS chỉ pull ([deploy/registry/README.md](deploy/registry/README.md)); `docker-compose.prod.yml` là fallback build trực tiếp trên VM.

| Sprint | Giai đoạn | Tên Sprint & Trọng tâm | Trạng thái | File chi tiết |
|---|---|---|---|---|
| **Sprint 9** | Phase 3 | LLM-Driven UX Simulation (`phase3_ux_sim/`, 100 LLM Users 1-time Seed, Supabase Cloud Telemetry & MCP Test via GitHub Actions) + Saturation k6 + Ingest Stress + RAGAS & Production-Ops Drills (9.5–9.17) | 🟢 **Đang triển khai** | [sprint-9-synthetic-business-hours-load.md](sprint-9-synthetic-business-hours-load.md) |
| **Sprint 10** | Phase 3 | Pentest Bảo mật, Vận hành 4 tuần trên VPS thật & Portfolio | ⏳ **Chờ Sprint 9** | [sprint-10-pentest-operations.md](sprint-10-pentest-operations.md) |

> **Tiến độ Sprint 9 (cập nhật 2026-11-28):** Công cụ `phase3_ux_sim/` + workflow thủ công đã commit (`43e0f2e`). Lần chạy live đầu tiên (2026-11-26, backend production) seed 5 user, chạy 11 hành động — 0 lỗi 5xx, 0 lần 429, search P95 1,18 s — và **MCP Zero-Knowledge PASS vào PostgreSQL/Supabase DB dự án cũ** (SELECT `documents` 3 dòng, chặn `DROP TABLE`). Còn thiếu: secrets Supabase/Google trên GitHub, seed đủ 100 user, telemetry thật lên Supabase Cloud, chạy workflow GitHub lần đầu, k6 stress + soak 5 ngày. **Bổ sung 2026-11-28:** mục 0C của sprint-9 thêm 13 bài 9.5–9.17 — baseline phát hành 2.6.0, dataset vàng + hiệu chuẩn, saturation matrix, soak ledger, đối soát egress GCP, RAGAS + cổng hồi quy, hoàn tất simulator, SLO/runbook/diễn tập, change management, gói bằng chứng và stress ingest + kiểm tra toàn vẹn dữ liệu (không thay thế vận hành 4 tuần của Sprint 10).
