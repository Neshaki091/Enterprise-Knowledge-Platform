# SPRINT 9 (PHASE 3): LLM-Driven UX Simulation, Supabase Cloud Telemetry & MCP Test

**Mục tiêu:** Xây dựng công cụ độc lập [phase3_ux_sim/](../phase3_ux_sim/README.md) chạy trên local và tự động hóa qua GitHub Actions (`.github/workflows/phase3-ux-sim.yml`), mô phỏng hành vi đọc-hỏi thật bằng LLM, lưu toàn bộ lịch sử UX lên Supabase Cloud DB và kiểm thử kết nối MCP Zero-Knowledge tới DB Supabase Cloud dự án cũ.

**Ước tính thời gian:** 2 – 3 tuần

**Trạng thái:** 🟢 Đang triển khai — tool & workflow đã xong, lần chạy live đầu tiên 2026-11-26; còn secrets Supabase/Google, seed 100 user, k6 stress + soak (xem mục 0B) và các bài giả lập/đánh giá/vận hành bổ sung 9.5–9.16 (mục 0C)

---

## 0A. KIẾN TRÚC TOOL MÔ PHỎNG UX & SUPABASE CLOUD (`phase3_ux_sim/`)

1. **Khởi tạo 100 User ID duy nhất 1 lần bằng LLM:**
   - Tool gọi LLM tạo danh sách ~100 hồ sơ nhân sự (`email`/`username`, `password`, `role`, `department`, `persona`), phân bổ đều trên 10 Tenants (10 user/tenant để tôn trọng trần `max_users=10` gói FREE).
   - Kiểm tra bảng `ux_sim_users` trên **Supabase Cloud DB** trước: **chỉ tạo các user còn thiếu cho đủ 100 và KHÔNG BAO GIỜ tạo trùng ở các lần chạy sau**; các lần sau đăng nhập lại bằng thông tin đã lưu.
2. **Hành động & Câu hỏi sinh bởi LLM theo ngữ cảnh đọc thật:**
   - Khi yêu cầu LLM sinh hành động/câu hỏi tiếp theo cho một `user_id`, bắt buộc truyền kèm **`last_query` + `last_llm_reply`** (câu trả lời RAG gần nhất mà user vừa đọc) để sinh câu hỏi nối tiếp tự nhiên, tìm kiếm lại nếu thiếu ý, gửi feedback (`THUMBS_UP` / `THUMBS_DOWN`), hoặc thoát phiên.
3. **Lưu vết toàn bộ lên Supabase Cloud DB theo từng timestamp:**
   - Bảng `ux_sim_users`, `ux_sim_runs`, `ux_sim_events`, `ux_sim_mcp_checks` lưu đủ: `user_id`, `tenant_id`, `timestamp_utc`, `action_type`, `query`, `llm_reply`, `context`, `ttfb_ms`, `total_latency_ms`, `tokens`, `http_status`, `feedback_rating`.
4. **Test kết nối MCP bằng Supabase Cloud DB dự án cũ:**
   - Dùng API `/api/v1/mcp/external-db` (AES-256-GCM + `tenant_secret`) + `/api/v1/mcp/external-db/token` (`db_jwt`) + tool `query_enterprise_db` truy vấn thật vào DB Supabase Cloud và kiểm định chặn bảng ngoài whitelist / DDL.
5. **Tổng hợp toàn diện chỉ số UX (UX Scorecard) & Chạy trên GitHub Actions:**
   - Tính TTFT P50/P95/P99, E2E Latency P95, Compression Savings %, Rate-Limit 429 Friction %, Refusal Rate %, Follow-Up Rate %, Satisfaction (`THUMBS_UP`) % và kết quả MCP; xuất lên `$GITHUB_STEP_SUMMARY`.
6. **Điều phối Multi-Model Flash-Lite Pool độc lập với Backend Gemma:**
   - Simulator xoay vòng `gemini-3.1-flash-lite` và `gemini-3.5-flash-lite` (`SIM_LLM_MODELS`) với bộ giới hạn riêng mỗi model (`25 RPM` / `480 RPD`, tổng `~960–1.000 RPD` hiệu dụng) và tự động failover khi gặp `429/503`, không tranh chấp quota của `gemma-4-26b-a4b-it` bên Backend RAG.
7. **Hành vi ngẫu nhiên & Trần 20 LLM Query/Run bảo vệ Egress < 0,8 GB/tháng:**
   - Mỗi lần chạy lấy mẫu ngẫu nhiên user (`random.sample`), số bước ngẫu nhiên (`1..N`) và hành động ngẫu nhiên (không gò ép làm đủ mọi bước).
   - Giới hạn cứng `SIM_MAX_LLM_QUERIES=20` cho toàn bộ phiên chạy (các lượt vượt trần tự chuyển sang `search_hybrid` / `list_documents` nhẹ), đảm bảo khi chạy chung với tải SOAK 5 ngày (`0,6–0,8 GB/tháng`) vẫn không vượt ngưỡng `0,8 GB` egress của GCP Always Free.

---

## 0B. TIẾN ĐỘ THỰC TẾ (cập nhật 2026-11-28)

### Đã hoàn thành

- [x] **Công cụ `phase3_ux_sim/`** — đủ 8 module: `config`, `storage` (SQLAlchemy async, SQLite fallback khi thiếu `SUPABASE_DB_URL`), `user_seeder` (seed 1 lần, kiểm tra chống trùng), `llm_persona` (pool 2 model Flash-Lite, RPM/RPD riêng + failover), `agent_simulator` (hành động dựa trên `last_query`/`last_llm_reply`, trần `SIM_MAX_LLM_QUERIES`), `mcp_test`, `metrics` (UX Scorecard + egress), `runner`.
- [x] **Workflow GitHub Actions** `.github/workflows/phase3-ux-sim.yml` — chỉ chạy tay (`workflow_dispatch`), schedule đang tắt; upload artifact báo cáo 30 ngày.
- [x] **Vệ sinh bí mật** — bỏ secret hardcode, `.env_vu`/`*.db`/`reports/`/`_asgi_faiss/` đã gitignore; commit `43e0f2e`.
- [x] **Chạy live lần đầu** (2026-11-26, target backend production):
  - Seed 5 user / 3 user hoạt động / 11 hành động (`login` 3, `list_documents` 7, `search_hybrid` 1); 0 lỗi 5xx, 0 lần 429.
  - Search P95 `1.180 ms`; egress `14,68 KB`; ngoại suy `+0,315 MB/tháng` nếu chạy 22 ngày.
  - **MCP Zero-Knowledge PASS vào PostgreSQL/Supabase DB dự án cũ:** SELECT `documents` trả 3 dòng, `DROP TABLE` bị chặn; latency roundtrip `8,1 s`.
  - Báo cáo: `phase3_ux_sim/reports/latest_ux_report.md` (run `6bf71bea…`).
- [x] **Xác minh hạ tầng phát hành** — CI xanh + workflow **Build, Push & Deploy** xanh cho commit `43e0f2e`; backend production chạy `2.3.3`, `/health` công khai trả `"version":"2.3.3"`.

### Chưa hoàn thành

- [ ] Cấu hình GitHub Secrets `SUPABASE_DB_URL`, `SIM_MCP_DB_URL`, `GOOGLE_API_KEY`, `SIM_TENANT_SECRET`; chạy workflow Phase 3 trên GitHub Actions lần đầu (hiện `total_count = 0`).
- [ ] Seed đủ **100 user** (lần đầu mới 5) và lưu telemetry thật lên **Supabase Cloud** thay vì SQLite fallback (`supabase_cloud: false` trong run đầu).
- [ ] Bài tập 9.1–9.4: k6 journey 1–4, stress tìm điểm bão hòa (429 đầu tiên), soak gộp 5 ngày làm việc.
- [ ] Đo/báo cáo egress thực tế, RSS/CPU/connection, đối chiếu trần 25 RPM và 16K TPM.
- [ ] Các bài giả lập/đánh giá/vận hành bổ sung **9.5–9.16** (mục 0C): baseline 2.6.0, dataset vàng, saturation matrix, soak ledger, đối soát egress, RAGAS + cổng hồi quy, hoàn tất simulator, SLO/runbook/diễn tập, change management, gói bằng chứng.
- [ ] Hoàn tất checklist nghiệm thu mục 2 và phần phản biện.

---

## 0C. TASK BỔ SUNG (2026-11-28): GIẢ LẬP — ĐÁNH GIÁ — HỌC VẬN HÀNH PRODUCTION

Mục 0A/0B mô tả công cụ UX simulator; mục 1 mô tả 4 bài tải 9.1–9.4. Phần dưới bổ sung các bài **9.5–9.16** để biến số đo thành bằng chứng kiểm chứng được và tập các kỹ năng vận hành production. Sprint 10 vẫn sở hữu pentest + vận hành 4 tuần; Sprint 9 chỉ **diễn tập một lần** trên staging/local, không thay thế Sprint 10.

**Nguyên tắc chung**

- Mọi bài chạy staging/local trước; production chỉ sau khi `/health` xanh và có backup.
- Không gọi Google AI/LLM thật khi chưa xác nhận; dùng `mode=mock`/`hybrid` cho bài lặp lại. RAGAS cần judge LLM ➔ xin phép trước, ghi rõ model + hạn mức.
- Không commit secret, `.env`, dataset thật, log có PII, báo cáo RAGAS thô; chỉ commit số tổng hợp + quy trình.
- Mỗi bài phải để lại: lệnh đã chạy, timestamp UTC, số đo trước/sau, file báo cáo trong repo (trừ dữ liệu nhạy cảm).
- Điều kiện dừng khẩn: OOM, 5xx > 1% kéo dài, đĩa > 80%, RSS backend > 300 MB, DB connection cạn, egress chạm 80%.

### Nhóm A — Baseline phát hành & dữ liệu đo

- [ ] **9.5 — Baseline 2.6.0 & bằng chứng phát hành.**
  - Deploy qua workflow Build, Push & Deploy; xác nhận `/health` trả `2.6.0`, `alembic current` = `0011_add_chat_attachments`, 3 container healthy, RSS sau warm-up.
  - Smoke tối thiểu: login ➔ upload tài liệu công ty ➔ chat có citation ➔ chat kèm attachment riêng ➔ user khác/session khác không thấy ➔ SSE không lộ `<session_memory>` ➔ export RAGAS loại lượt attachment.
  - Lưu image digest + commit + tag rollback (`previous`); chụp `/health`, `docker ps`, `alembic current` vào `deploy/releases/2.6.0.md` (không chứa secret).
  - Backup `pg_dump` + FAISS index **trước** deploy và ngay sau migration; ghi dung lượng + checksum.
  - *Deliverable:* release note 2.6.0 + ảnh/log baseline.

- [ ] **9.6 — Dataset vàng & hiệu chuẩn tải.**
  - 2 tenant FREE × 10 user; 3–5 tài liệu synthetic/tenant; **20 câu hỏi vàng**: 8 trả lời được, 4 phải từ chối, 4 nối tiếp 2 lượt, 4 không kết quả rồi đổi từ khóa.
  - Mỗi câu ghi `expected_answer`, `expected_source_heading`, `expected_behavior` vào `load_tests/dataset/golden_qa.json` (dữ liệu synthetic, không PII). Đây là `reference` để chấm context recall và là bộ hồi quy hành vi.
  - Hiệu chuẩn Journey 1: 3–5 VU, 200 req/giờ/VU, think-time 12–25 s, tỷ lệ 80% đọc (search/list/dashboard) / 20% chat, 10–30% phiên bỏ ngang; chat ≤ 300 lượt/ngày.
  - Chạy mock 1 lượt, xác nhận kết quả lặp lại; ghi `load_tests/reports/calibration_<YYYY-MM-DD>.md`.
  - *Deliverable:* golden set + báo cáo hiệu chuẩn (không chứa đáp án thật của khách hàng).

### Nhóm B — Đo tải, bão hòa, soak, egress

- [ ] **9.7 — Ma trận bão hòa (saturation matrix).**
  - 10 ➔ 20 ➔ 40 ➔ 60 VU, 5 phút/mức, bỏ pacing; ghi mỗi mức: RPS, p95 API, SSE đồng thời, 5xx, thời điểm 429 đầu tiên, CPU, RSS, `pg_stat_activity`, FD (`ss -s`), đĩa.
  - Phân biệt **429 của token bucket ứng dụng** (có `Retry-After`) với **503/limit_req của Nginx** — hai nguyên nhân khác nhau, không gộp số.
  - Dừng khi 429 kéo dài, 5xx > 1% hoặc RSS > 300 MB; ghi rõ biến nào bão hòa trước (RPM, TPM, RAM, DB pool, SSE).
  - *Deliverable:* bảng + biểu đồ `load_tests/reports/saturation_<YYYY-MM-DD>.md`.

- [ ] **9.8 — Soak 5 ngày làm việc, sổ nhật ký theo ngày.**
  - Chạy đúng workload 8h–17h (nghỉ 12h–13h) trong 5 ngày; lấy mẫu 5 phút/lần.
  - Mỗi ngày ghi: request, 5xx, 429, p95, RSS đầu/cuối/đỉnh, CPU trung bình/đỉnh, connection đỉnh, FD, đĩa, egress ước tính.
  - So mốc cuối ngày 1 sau warm-up: RSS tăng < 10%, không OOM/FD leak, đĩa tăng tuyến tính; nếu vượt ➔ dừng, thu `docker logs`, `dmesg`, `pg_stat_activity` rồi post-mortem.
  - *Deliverable:* `load_tests/reports/soak_<YYYY-MM-DD>.md` với bảng 5 ngày + kết luận pass/fail.

- [ ] **9.9 — Đối soát egress với hoá đơn GCP.**
  - Tính bytes/ngày từ access log Nginx (body + header ước tính), tách asset Vercel, chat SSE, admin realtime, backup và download RAGAS.
  - Đối chiếu với GCP Billing/Network egress; access log **không tính TCP/TLS overhead** nên hoá đơn là nguồn chuẩn.
  - Ngoại suy tháng, so mục tiêu 0,6–0,8 GB; đặt cảnh báo 80% (0,8 GB) và ghi hành động khi chạm ngưỡng (giảm chat/realtime, tạm dừng soak).
  - *Deliverable:* bảng "ước tính vs thực đo" + ảnh dashboard chi phí (che thông tin nhạy cảm).

### Nhóm C — Đánh giá chất lượng RAG (RAGAS)

- [ ] **9.10 — Chạy RAGAS bên ngoài hệ thống.**
  - Export tháng bằng CLI/API; chỉ giữ `mode` thật (`real`/`hybrid`), loại `mock`/`retrieval_only`, loại `has_attachment=true`; lấy mẫu 50–100 lượt.
  - Phân tách **single-turn** (lượt đầu mỗi `session_id`) và **follow-up** (các lượt sau, sắp theo `created_at`); đánh giá riêng vì follow-up dựa vào session memory, không chỉ context.
  - Ghép `reference` từ golden set theo `user_input`; nếu không có reference thì bỏ context recall, chỉ chấm faithfulness/answer relevancy/context precision.
  - Dùng judge LLM bên ngoài (không thêm `ragas` vào backend image), xin phép trước, giới hạn số mẫu và RPM; lưu key ngoài repo.
  - Báo cáo: điểm trung bình + phân bố, tỷ lệ trích dẫn đúng, tỷ lệ từ chối đúng, lỗi điển hình (thiếu context, sai nguồn, lặp câu, lộ reasoning).
  - *Deliverable:* `ragas_eval/report_<YYYY-MM>.md` (gitignored) + bảng tổng hợp không chứa nội dung nhạy cảm.

- [ ] **9.11 — Cổng hồi quy chất lượng trước mỗi lần đổi prompt/model.**
  - Đóng băng bộ 30 mẫu đại diện (gồm refusal + follow-up); chạy trên 2.6.0 làm baseline.
  - Sau mỗi thay đổi prompt/model/context, chạy lại cùng bộ; chấp nhận biến động ±0,03, chặn deploy nếu giảm > 0,05 ở faithfulness hoặc context precision.
  - Ghi model + commit + ngày vào báo cáo để truy vết.
  - *Deliverable:* baseline + quy trình "đo trước/đo sau" trong `ragas_eval/README.md`.

### Nhóm D — Hoàn tất UX simulator

- [ ] **9.12 — Đưa `phase3_ux_sim` lên GitHub Actions và Supabase Cloud.**
  - Cấu hình đủ 4 secret; chạy workflow thủ công lần đầu; xác nhận artifact + `$GITHUB_STEP_SUMMARY`.
  - Seed đủ 100 user (10 tenant × 10), kiểm tra chống trùng bằng đếm `ux_sim_users`; lưu telemetry thật (`supabase_cloud=true`), không fallback SQLite.
  - Chạy 3 lần khác ngày; so sánh scorecard (TTFT, P95, 429 friction, refusal, follow-up, satisfaction).
  - MCP: 1 test dương tính (SELECT trong whitelist) + test âm tính (bảng ngoài whitelist, DDL) phải bị chặn; ghi latency.
  - Kiểm tra log/artifact không lộ secret; nêu rõ đây là **synthetic workload**, không phải người dùng thật.
  - *Deliverable:* run ID + báo cáo UX 3 lần + bảng xu hướng.

### Nhóm E — Học vận hành production

- [ ] **9.13 — SLO, alert và dashboard vận hành.**
  - Chốt SLI/SLO nội bộ: availability (probe 5 phút), tỷ lệ 5xx, p95 API, TTFB chat, 429 friction, egress, đĩa, backup thành công.
  - Ngưỡng cảnh báo đề xuất: 5xx > 1% trong 5 phút; p95 > 1,5 s trong 10 phút; đĩa > 80%; egress > 80%; cert < 14 ngày; backup quá 26 giờ.
  - Chọn công cụ miễn phí (uptime check + script), không thêm dịch vụ nặng lên e2-micro.
  - *Deliverable:* `docs/operations/slo.md` + ảnh dashboard/alert test.

- [ ] **9.14 — Runbook sự cố & diễn tập không phá hoại.**
  - Viết runbook cho: LLM 429/5xx, PostgreSQL mất kết nối, đĩa 80%, cert hết hạn, egress 80%, deploy lỗi, prune attachment lỗi.
  - Mỗi mục: dấu hiệu ➔ kiểm tra ➔ giảm thiểu ➔ xác minh ➔ phòng ngừa; lệnh copy-paste được.
  - Diễn tập trên staging/local: kill PostgreSQL, chặn API key, làm đầy đĩa bằng file tạm rồi xoá, `certbot renew --dry-run`; **không** xoá volume, không chạy `down -v`.
  - Rollback: deploy lại image `previous` (2.5.0) với schema 0011 còn nguyên — app cũ bỏ qua bảng mới; chỉ thử `alembic downgrade 0011➔0010` trên **bản copy DB** vì downgrade xoá bảng attachment.
  - Đo RTO/RPO cho từng kịch bản; ghi timeline thực tế.
  - *Deliverable:* runbook + 3 biên bản diễn tập + post-mortem mẫu.

- [ ] **9.15 — Kỷ luật phát hành (change management).**
  - Checklist trước deploy: test xanh, 1 alembic head, backup + checksum, image digest, diff env, tag rollback, cửa sổ deploy, smoke plan.
  - Sau deploy: health, login, upload, chat, SSE, export RAGAS, telemetry; theo dõi 24 giờ.
  - Thực hành 1 lần release 2.6.0 và 1 lần rollback trên staging, ghi thời gian từng bước.
  - *Deliverable:* checklist + release/rollback log.

- [ ] **9.16 — Gói bằng chứng & tự vấn bảo vệ.**
  - Tập hợp: release note, saturation, soak, egress, RAGAS, UX scorecard, runbook, drill post-mortem, link CI.
  - Chỉ đưa số đo thật vào README/history/ROADMAP; ghi "synthetic business-hours workload", không tuyên bố 10.000 người dùng thật.
  - Tự trả lời 10 câu: vì sao 429 tại RPM nào? tài nguyên nào bão hòa trước? phát hiện rò rỉ bằng gì? RTO/RPO? LLM chết thì sao? rollback mất gì? egress vượt thì cắt gì? RAGAS giảm thì chặn deploy thế nào? attachment có vào FAISS/RAGAS không? làm sao chứng minh user A không thấy file user B?
  - *Deliverable:* `docs/operations/evidence-pack.md` + cập nhật checklist mục 2.

**Thứ tự đề xuất:** 9.5 ➔ 9.6 ➔ 9.12 (song song) ➔ 9.7 ➔ 9.10/9.11 ➔ 9.8/9.9 (5 ngày) ➔ 9.13–9.16. Tổng thêm khoảng 1,5–2 tuần sau khi có secrets và 2.6.0 live.

**Quan hệ với Sprint 10:** 9.14 chỉ là diễn tập một lần; Sprint 10 lặp lại trên production 4 tuần, thêm ZAP/Burp, uptime thật, backup hằng đêm và tối thiểu 2 sự cố thật. Không tính bài diễn tập Sprint 9 là bằng chứng vận hành 4 tuần.

---

## 0. NGÂN SÁCH TẢI & VÌ SAO KHÔNG CHẠY 10.000 VU

| Thông số | Giá trị | Căn cứ |
|---|---|---|
| Người dùng | 20 user/ngày, 3–5 phiên hoạt động đồng thời, mỗi phiên 1–2 giờ | FREE tenant tối đa 10 user ➔ 2 tenant × 10 user |
| Request | **200 req/giờ/user khi hoạt động**; 20 user × 1–2 giờ ➔ **4.000–8.000 req/ngày** | 3–5 phiên đồng thời = 10–17 RPM; trần 25 RPM |
| Trần an toàn | **25 RPM**, chat ≤ 8 lượt/phút | Quota 30 RPM; 16K TPM ≈ 10–11 lượt chat/phút |
| Egress | API ~10–14 MB/ngày (1,2–1,8 KB/req) + asset/chat/realtime ➔ **0,6–0,8 GB/tháng** (cảnh báo 80%) | Đo thực tế: search ~1,9 KB, chat SSE ~16 KB, SPA lạnh ~222 KiB |
| Ngân sách chat | ≤ 300 lượt/ngày (~4,8 MB/ngày) | 300 × 16 KB = 4,8 MB/ngày ➔ ~144 MB/tháng |
| Admin realtime | ≤ 1 tab-hour/ngày (~2,45 MB/ngày) | 2.043 B/event × 3 s ≈ 74,5 MB/tháng/tab-hour |
| Soak | Gộp **5 ngày làm việc liên tiếp** với chính workload này (45 giờ có tải) | Không chạy 24/7 riêng; tiết kiệm egress, phản ánh vận hành thật |
| Khung giờ | 8h–17h, nghỉ trưa 12h–13h | Để đêm trống cho backup/maintenance |
| Load generator | k6 trên laptop, bắn qua Internet | Không chạy trên VPS để tránh tranh chấp CPU/RAM |

**Vì sao 10.000 VU chỉ còn là bài toán lý thuyết:**

- 10.000 user/ngày × 300–400 request ≈ **3–4 triệu request/ngày** (35–46 req/s, 91–122 triệu req/tháng).
- Riêng HTTP header (~300 B/response) đã tốn **27–36 GB/tháng**, trong khi GCP Always Free chỉ có 1 GB (≈ 35,3 MB/ngày) ➔ vượt 27–36 lần.
- Quota 30 RPM/tenant chỉ chịu **43.200 request/ngày** ➔ cần 70–93 tenant để đạt 3–4 triệu req/ngày.
- e2-micro 1 GB RAM, 1 Uvicorn worker không thể vừa chạy app vừa phục vụ tải đó.

➔ Con số 10.000 VU được giữ lại như **câu hỏi phản biện** (phân biệt Configured VUs vs Concurrent Sessions vs RPS vs số lượt gọi LLM thực tế), không phải mục tiêu thực thi.

---

## 1. PHÂN CHIA NHIỆM VỤ

### Phần bạn TỰ CODE TAY 100% (Học bản chất):

1. **Bài tập 9.1:** Viết kịch bản k6 cho **Journey 1 (Standard Employee)**:
   - Đăng nhập ➔ refresh token ➔ tìm kiếm hybrid ➔ chat RAG SSE ➔ gửi feedback.
   - Think-time 12–25 giây giữa các thao tác (tương đương 144–300 req/giờ), phiên 1–2 giờ rồi thoát; 10–30% phiên bỏ ngang giữa chừng.
   - Request dày chủ yếu là search/document/dashboard; chat chỉ chiếm phần nhỏ để không vượt ngân sách 300 lượt/ngày.
   - Thỉnh thoảng tìm kiếm 0 kết quả rồi hỏi lại bằng từ khóa khác (mô phỏng hành vi thật).
   - Đo TTFB và tổng thời gian stream; nếu cần phân tích từng event SSE, viết thêm script Python `httpx` song song.

2. **Bài tập 9.2:** Cấu hình k6 Options cho khung giờ doanh nghiệp:
   - **3–5 VU đồng thời** (mỗi VU đại diện một nhân viên đang hoạt động), pacing **200 req/giờ/VU** (một request mỗi ~18 giây); danh tính xoay vòng để đủ 20 user/ngày.
   - Tổng tải 3–5 VU = 600–1.000 req/giờ = 10–17 RPM, luôn dưới trần **25 RPM**; chạy 08:00–17:00, nghỉ trưa 12h–13h.
   - Thresholds: `http_req_duration: ['p(95)<1500']` cho API JSON; `http_req_failed: ['rate<0.01']`.
   - SSE đánh giá riêng: TTFB p95 < 1,5 s, tổng stream 5–15 s.

3. **Bài tập 9.3:** **Stress Test tìm điểm bão hòa**:
   - Tăng dần 10 ➔ 20 ➔ 40 ➔ 60 VU, bỏ pacing cho đến khi xuất hiện `429 Too Many Requests`.
   - Dự kiến: 429 tại **30 RPM**; chat bão hòa tại **~10–11 lượt/phút** do trần 16K TPM.
   - Ghi lại thời điểm 429 đầu tiên, CPU/RAM/RSS, số phiên SSE đồng thời và trạng thái Nginx (600 r/m, burst 100).

4. **Bài tập 9.4:** **Soak gộp với workload chung (5 ngày làm việc)**:
   - Chạy đúng workload 8h–17h (200 req/giờ/user, 3–5 phiên đồng thời) trong 5 ngày làm việc liên tiếp (45 giờ có tải), không chạy 24/7 riêng.
   - Lấy mẫu RSS/CPU/connection/disk mỗi 5 phút; mốc so sánh là cuối ngày 1 sau warm-up.
   - Yêu cầu: RSS tăng < 10% sau 5 ngày, không OOM, không rò rỉ connection/FD, dung lượng đĩa tăng tuyến tính và có kiểm soát.
   - Nếu cần kiểm chứng rò rỉ thuần túy, chạy thêm 48–72h trên VPS staging — không bắt buộc và không tính vào egress free.

### Phần AI hỗ trợ sinh mã (Tăng tốc):

1. Seed **20 tài khoản trên 2 tenant** (tôn trọng giới hạn 10 user/tenant) cùng 2–3 tài liệu mẫu mỗi tenant.
2. Viết kịch bản k6 cho 3 hành trình còn lại:
   - *Journey 2:* Knowledge Manager tải lên tài liệu mới.
   - *Journey 3:* Business Admin xem dashboard + realtime SSE (≤ 1 tab-hour/ngày để giữ egress).
   - *Journey 4:* Adversarial Tester thử IDOR/BOLA truy cập chéo tenant (kết quả chuyển sang Sprint 10).
3. Bộ sinh persona LLM (tuỳ chọn): dùng Gemini Flash Lite hoặc Gemma 4 32B **khác model trả lời** nếu có thể; API key truyền qua biến môi trường, không commit; chỉ gọi live khi có xác nhận rõ ràng; giữ mode `hybrid` để fallback mock khi provider quá tải.
4. Xuất báo cáo Markdown/HTML từ JSON log k6: p95, error rate, first-429, RSS, egress ước tính và thực đo.

---

## 2. CHECKLIST NGHIỆM THU SPRINT 9

> Trạng thái công cụ UX Simulation: xem mục 0B. Checklist dưới đây áp dụng cho phần tải k6/soak — hiện chưa mục nào được đánh dấu.

- [ ] Chạy đủ 9 giờ/ngày với 4.000–8.000 request (200 req/giờ/user, 3–5 phiên đồng thời): 0 lỗi 5xx, `http_req_failed` < 1%, p95 API < 1,5 s; không có 429 ngoài bài stress.
- [ ] Báo cáo điểm bão hòa: RPM/TPM/số phiên SSE/CPU/RAM tại lần 429 đầu tiên, đối chiếu với 30 RPM và 16K TPM.
- [ ] Soak gộp 5 ngày làm việc với workload chung (45h có tải): RSS tăng < 10% so với cuối ngày 1, không OOM, không rò rỉ connection/FD, đĩa tăng có kiểm soát.
- [ ] Egress đo/ngoại suy ≤ 0,8 GB/tháng (mục tiêu 0,6–0,8 GB); đặt cảnh báo ở 80% hạn mức.
- [ ] Bạn giải thích được: *Sự khác biệt giữa Configured VUs, Concurrent Sessions, RPS và số lượt gọi LLM thực tế? Vì sao 10.000 user/ngày bất khả thi trên 1 GB free?*
- [ ] Không dùng số liệu mô phỏng để tuyên bố "10.000 người dùng thật" trong CV/portfolio.
