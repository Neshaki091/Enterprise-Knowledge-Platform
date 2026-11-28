# Phase 3 UX Simulation & Supabase Cloud MCP Tool (`phase3_ux_sim`)

Công cụ độc lập phục vụ **Phase 3 (Sprint 9)** để:
1. **Khởi tạo 1 lần duy nhất ~100 User ID** bằng LLM (`ux_sim_users`), phân bổ qua các Tenants (tối đa 10 user/tenant), không bao giờ tạo lại user khi chạy các lần tiếp theo.
2. **Mô phỏng hành vi người dùng thật bằng LLM:** Mỗi bước gửi kèm `last_query` + `last_llm_reply` (câu trả lời RAG gần nhất) để LLM quyết định hành động tiếp theo (`search_hybrid`, `chat_stream`, `chat_query`, `submit_feedback`, `list_documents`, `exit_session`).
3. **Lưu toàn bộ dữ liệu lên Supabase Cloud DB** (`ux_sim_users`, `ux_sim_runs`, `ux_sim_events`, `ux_sim_mcp_checks`) theo từng timestamp: `user_id`, `query`, `llm_reply`, `context`, `ttfb_ms`, `total_latency_ms`, `tokens`, `http_status`, `feedback_rating`.
4. **Test kết nối MCP Zero-Knowledge với Supabase Cloud DB dự án cũ:** Đăng ký DB qua `/api/v1/mcp/external-db`, cấp `db_jwt`, chạy `query_enterprise_db` (`SELECT`) và kiểm định chặn `DROP TABLE`.
5. **Tổng hợp toàn diện chỉ số UX** ra `phase3_ux_sim/reports/latest_ux_report.md` và GitHub Step Summary.

## Trạng thái hiện tại (2026-11-28)

- Đã commit đủ bộ module + workflow thủ công `.github/workflows/phase3-ux-sim.yml` (schedule đang tắt) tại commit `43e0f2e`.
- **Lần chạy live đầu tiên** (2026-11-26, target backend production): seed 5 user, 3 user hoạt động, 11 hành động; 0 lỗi 5xx, 0 lần 429; search P95 `1.180 ms`; egress `14,68 KB`.
- **Chạy local quick trên backend 2.6.0** (2026-11-28): seed 20 user/2 tenant, 5 active, 15 hành động (100%), search P95 `501 ms`, 0 lần 429; MCP PASS (SELECT 3 dòng, chặn DROP TABLE); egress `24,64 KB`. Telemetry dùng SQLite riêng theo target (`sim_target_<hash>.db`) do chưa cấu hình `SUPABASE_DB_URL`.
- **Chạy local standard/full trên pool 100 user/10 tenant** (2026-11-28): standard 20 active/4 bước — 68 hành động (100%), search P95 `812 ms`, 1 chat `18,26 s`, egress `98,26 KB`; full 100 active/4 bước/50 query — 336 hành động (100%), search P95 `1.076 ms`, 4 chat P50/P95 `15,44/17,87 s`, TTFT `388,6 ms`, nén context `19,8%`, MCP PASS `8,05 s`, egress `560,37 KB`; 0 lỗi 429/5xx. Chat sample còn nhỏ (4 lượt), chưa đủ kết luận chất lượng RAG.
- **MCP Zero-Knowledge: PASS** vào PostgreSQL/Supabase DB dự án cũ — SELECT `documents` trả 3 dòng, `DROP TABLE` bị chặn (latency `8,1 s`).
- **Chưa chạy trên GitHub Actions lần nào** (`total_count = 0` trong Actions API) và telemetry vẫn là SQLite fallback vì chưa có `SUPABASE_DB_URL`/`SIM_MCP_DB_URL`/`SIM_TENANT_SECRET` trong GitHub Secrets.
- Còn thiếu: telemetry thật lên Supabase Cloud/GitHub Actions, phần tải k6 stress/soak (Bài tập 9.1–9.4), golden dataset + chấm RAGAS.

## Cấu hình biến môi trường / GitHub Secrets

- `SUPABASE_DB_URL`: PostgreSQL DSN của Supabase Cloud dùng để **lưu toàn bộ dữ liệu bài test UX** (`ux_sim_users`, `ux_sim_runs`, `ux_sim_events`, `ux_sim_mcp_checks`). Nếu chưa đặt khi chạy local, tool tự fallback sang SQLite: `phase3_ux_sim/sim_local.db` cho `asgi://local`, hoặc `phase3_ux_sim/sim_target_<hash>.db` riêng cho từng target remote (tránh dùng lại user cũ khi đổi target/reset DB).
- `SIM_MCP_DB_URL`: PostgreSQL DSN của **Supabase Cloud DB dự án cũ** dùng để test kết nối MCP Zero-Knowledge (nếu để trống, tự fallback về `SUPABASE_DB_URL`). Đi kèm `SIM_MCP_ALLOWED_TABLES` và `SIM_MCP_TEST_SQL` tương ứng với bảng thật của dự án cũ.
- `SIM_TARGET_API_URL`: URL Backend production (ví dụ `https://<ten>.duckdns.org`) hoặc `asgi://local` để test nội bộ.
- `GOOGLE_API_KEY`: API key dùng cho LLM sinh persona/hành động.
- `SIM_LLM_MODELS`: Danh sách model Simulator xoay vòng song song + failover khi gặp 429 (mặc định `gemini-3.1-flash-lite,gemini-3.5-flash-lite`, giúp nhân đôi RPD/RPM độc lập khỏi `gemma-4-26b-a4b-it` của Backend).
- `SIM_LLM_RPM_LIMIT` / `SIM_LLM_RPD_LIMIT`: Giới hạn an toàn mỗi model (mặc định `25` RPM / `480` RPD).
- `SIM_MAX_LLM_QUERIES`: Trần số lượt hỏi LLM RAG (`chat_stream`/`chat_query`) cho toàn bộ một lần chạy (mặc định `20` query/run) giúp kết hợp với tải SOAK vẫn giữ tổng GCP egress dưới `0,8 GB/tháng`.
- `SIM_TENANT_SECRET`: Khóa bí mật Zero-Knowledge cho phiên test MCP.
- `SIM_MCP_ALLOWED_TABLES`: Danh sách bảng cho phép test MCP (mặc định `ux_sim_events,ux_sim_users,ux_sim_runs`).

## Chạy thử trên Local

```bash
.venv\Scripts\python -m phase3_ux_sim.runner --target-url asgi://local --target-users 20 --active-users 5 --steps 4 --offline-llm
```

## Chạy nhanh trên Windows (double-click)

Double-click `run_phase3_ux_sim.cmd` ở thư mục gốc repo. Menu gồm:

- Local ASGI smoke: 20 user, 5 active, offline LLM, skip MCP, telemetry SQLite riêng
  `phase3_ux_sim/sim_local_smoke.db`.
- Seed-only 100 user, chạy nhanh, tiêu chuẩn hoặc full trên target trong
  `phase3_ux_sim/.env_vu`.
- Chạy với URL tùy chỉnh.

File tự dùng `.venv\Scripts\python.exe`, hiện cảnh báo và yêu cầu xác nhận khi
target không phải local, và lưu lịch sử report `reports/ux_report_<timestamp>.md/json`
(đã gitignore).

## Đánh giá kết quả

Xem [how_eval.md](../how_eval.md): ngưỡng pass/fail, cách đọc TTFT/chat E2E/refusal/
nén context/RPD theo số mẫu, và hành động cải thiện cho Phase 3, load test và RAGAS.
