# SPRINT 9 (PHASE 3): LLM-Driven UX Simulation, Supabase Cloud Telemetry & MCP Test

**Mục tiêu:** Xây dựng công cụ độc lập [phase3_ux_sim/](../phase3_ux_sim/README.md) chạy trên local và tự động hóa qua GitHub Actions (`.github/workflows/phase3-ux-sim.yml`), mô phỏng hành vi đọc-hỏi thật bằng LLM, lưu toàn bộ lịch sử UX lên Supabase Cloud DB và kiểm thử kết nối MCP Zero-Knowledge tới DB Supabase Cloud dự án cũ.

**Ước tính thời gian:** 2 – 3 tuần

**Trạng thái:** 🟢 Đang triển khai (Phase 2 đã hoàn thành)

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

- [ ] Chạy đủ 9 giờ/ngày với 4.000–8.000 request (200 req/giờ/user, 3–5 phiên đồng thời): 0 lỗi 5xx, `http_req_failed` < 1%, p95 API < 1,5 s; không có 429 ngoài bài stress.
- [ ] Báo cáo điểm bão hòa: RPM/TPM/số phiên SSE/CPU/RAM tại lần 429 đầu tiên, đối chiếu với 30 RPM và 16K TPM.
- [ ] Soak gộp 5 ngày làm việc với workload chung (45h có tải): RSS tăng < 10% so với cuối ngày 1, không OOM, không rò rỉ connection/FD, đĩa tăng có kiểm soát.
- [ ] Egress đo/ngoại suy ≤ 0,8 GB/tháng (mục tiêu 0,6–0,8 GB); đặt cảnh báo ở 80% hạn mức.
- [ ] Bạn giải thích được: *Sự khác biệt giữa Configured VUs, Concurrent Sessions, RPS và số lượt gọi LLM thực tế? Vì sao 10.000 user/ngày bất khả thi trên 1 GB free?*
- [ ] Không dùng số liệu mô phỏng để tuyên bố "10.000 người dùng thật" trong CV/portfolio.
