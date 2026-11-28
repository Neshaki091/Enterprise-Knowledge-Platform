# History — các thay đổi theo phiên bản

Tài liệu này ghi lại thay đổi kỹ thuật và lỗi đã sửa theo từng phiên bản.
Nguồn chuẩn của version backend là `backend/app/core/version.py`.

---

## 2.4.1 — 2026-11-28

**Trạng thái:** đã code + test; **chưa build/push/deploy** (production đang 2.4.0).

### Sửa lỗi câu trả lời bị lặp hai lần

**Hiện tượng:** chat SSE trả cùng một câu trả lời hai lần, dính liền kiểu
`... [Trang 1]].Huỳnh Công Luyện...` (ảnh live sau 2.4.0).

**Nguyên nhân chính:** nếu stream Google/Gemma đứt kết nối *sau khi đã phát token*,
`LLMService` retry rồi fallback `generateContent` và phát lại toàn bộ câu trả lời.
Ngoài ra model có thể tự lặp nguyên câu, trong khi sanitizer cũ chỉ dedupe theo đoạn
cách nhau bằng dòng trống.

**Đã sửa:**
- `LLMService.stream_answer` theo dõi token đã phát trong từng attempt; nếu lỗi sau khi
  đã gửi thì dừng, không retry/fallback để tránh phát lặp.
- `PromptService.SYSTEM_PROMPT` cấm lặp cùng một câu/ý.
- `sanitize_answer` xoá câu trả lời bị lặp nguyên khối 2–3 lần và câu liền sau trùng
  câu trước, kể cả khi không có khoảng trắng sau dấu chấm.
- Thêm test mô phỏng stream đứt sau token đầu và mẫu câu bị lặp trong ảnh.

**Giới hạn:** stream vẫn không thể “thu hồi” token đã gửi; nếu model tự lặp ở giữa
một câu trả lời dài, chỉ phần non-streaming được dedupe triệt để.

---

## 2.4.0 — 2026-11-28

**Trạng thái:** đã code + 60 test backend PASS + frontend `tsc`/Vite build PASS;
migration `0010` render SQL offline OK; **chưa build/push/deploy** (production vẫn 2.3.3).

### 1. Lưu trace hội thoại để xuất RAGAS theo tháng

- Thêm bảng `chat_turns` (migration `0010_add_chat_turns`) lưu mỗi lượt hỏi-đáp:
  - `query`, `answer`, `session_id`, `tenant_id`, `user_id`
  - snapshot `contexts` (text + chunk ID + score), `citations`, `chunk_ids`, `llm_context`
  - `model_name`, `mode`, `latency_ms`, `ttfb_ms`, token vào/ra, compression savings
- Bật RLS theo `tenant_id`; thêm index cho tenant/user/session/created_at.
- Tích hợp cả hai đường chat:
  - `POST /api/v1/chat/query`
  - `POST /api/v1/chat/stream` (ghi sau khi stream xong, dùng session ngắn như telemetry)
- Thêm `GET /api/v1/chat/history` để đọc lại lượt của chính người dùng.
- Thêm `GET /api/v1/chat/export?month=YYYY-MM` trả JSONL cho tenant hiện tại,
  chỉ OWNER/ADMIN (hoặc superuser); response là attachment, `no-store`.
- Tenant Admin Console có ô chọn tháng + nút **Xuất JSONL**; không cần CLI.
  Frontend dùng `downloadFile` có Bearer token, nhận filename từ `Content-Disposition`.
- `session_id` có thể do client gửi lên; bỏ trống thì server tạo mới.
- Config mới:
  - `CHAT_HISTORY_ENABLED` (mặc định `true`)
  - `CHAT_HISTORY_RETENTION_DAYS` (mặc định `365`)
- Lỗi ghi trace được cô lập bằng savepoint, không làm hỏng câu trả lời chat.

### 2. Xuất dataset RAGAS — không tích hợp RAGAS vào hệ thống

- Thêm `scripts/export_ragas_dataset.py`: đọc `chat_turns` theo tháng (UTC), ghi **JSONL.GZ**.
- Mỗi dòng có `user_input`, `response`, `retrieved_contexts`, `reference` (null) và `metadata`
  (chunk IDs, citations, `llm_context`, số context, model, latency, token, feedback).
- Mặc định loại bỏ lượt `mock`/`retrieval_only` để RAGAS chỉ chấm câu trả lời thật;
  cờ `--include-mock` dành cho dữ liệu test.
- API và CLI đều nén gzip khi stream/ghi file (`.jsonl.gz`), giảm dung lượng tải về;
  frontend tự nhận filename từ `Content-Disposition`.
- Mỗi lần export qua API ghi telemetry `ragas_export` (tháng, include_mock, compression).
- Repo **không cài `ragas`**, không thêm evaluator/dependency vào backend image.
- Hướng dẫn chạy trên VPS và lưu ý dữ liệu nhạy cảm: `scripts/README_ragas_export.md`.
- `.gitignore` chặn `ragas_export/`, `ragas_*.jsonl` và `ragas_*.jsonl.gz`.

### 3. Sửa lỗi Gemma trả về reasoning/self-check (ảnh chat thực tế)

**Hiện tượng:** câu trả lời bắt đầu bằng `User Question`, `Context provided`, `Constraints`,
`Format?`, `Missing info check?` rồi mới đến đáp án, thậm chí lặp đáp án hai lần.

**Nguyên nhân:** Gemma trả thêm phần reasoning/self-check nhưng không gắn cờ `thought: true`,
nên bộ lọc part `thought` hiện có không loại được.

**Đã sửa:**
- Siết `PromptService.SYSTEM_PROMPT`: chỉ xuất đáp án cuối, cấm lặp câu hỏi/context/constraints/quá trình suy luận.
- Siết prompt người dùng: “Trả lời trực tiếp (không lặp lại hướng dẫn)…”.
- Thêm `PromptService.sanitize_answer` cho non-streaming:
  - cắt block reasoning ở đầu theo các nhãn + self-check;
  - lấy phần sau `Answer:`/`Trả lời:` nếu có;
  - xoá đoạn trả lời bị lặp y hệt.
- Thêm `ReasoningStreamSanitizer` cho SSE: giữ buffer ngắn để phát hiện block reasoning trước khi phát token, tránh leak ra người dùng.
- Giữ nguyên bộ lọc `thought: true` cũ như lớp bảo vệ thứ nhất.
- Thêm test `backend/tests/test_prompt_sanitizer.py` dùng chính mẫu lỗi trong ảnh.

**Giới hạn:** chưa gọi Gemma live để xác nhận sau sửa (cần API key/live test có chủ đích);
sanitizer xử lý mẫu reasoning đã biết, không thể bảo đảm mọi biến thể văn bản của model.

### 4. Vận hành prune & giới hạn retention 1 năm

- Thêm `scripts/prune_chat_history.py`: xoá `chat_turns` cũ theo batch 2000 dòng,
  hỗ trợ `--dry-run`, `--tenant-id`, `--batch-size`, DSN từ `POSTGRES_*`.
- Retention tối đa **365 ngày** (demo Vuser/Phase 3): `CHAT_HISTORY_RETENTION_DAYS`
  bị chặn `le=365`; script cũng từ chối giá trị lớn hơn.
- Dockerfile copy sẵn `export_ragas_dataset.py` và `prune_chat_history.py` vào
  `/app/scripts/` để chạy bằng `docker exec`, không cần `docker cp`.
- Prune chạy trên PostgreSQL local cùng VPS: **không tốn egress GCP** đáng kể;
  chỉ tải file export/backup ra ngoài mới tính băng thông.
- Chưa bật cron tự động trên VPS; lệnh cron mẫu nằm trong `scripts/README_ragas_export.md`.

---

## 2.3.3 — 2026-11-27

- Thêm workflow GitHub Actions **Build, Push & Deploy**:
  - build/push `ekp-backend` + `ekp-nginx` lên Docker Hub;
  - deploy VPS qua SSH, cập nhật `IMAGE_TAG`, health gate + rollback;
  - chặn tag `latest`, tag không khớp `APP_VERSION`, tag đã tồn tại.
- Sửa lỗi `PUBLIC_HEALTH_URL` gõ thiếu ký tự `/health`: GitHub runner nhận HTML SPA 200.
  Workflow giờ trim URL, bắt buộc `https://`, fail-fast nếu response là HTML.
- Xác minh live: CI xanh (54 test + frontend build), deploy `2.3.3` xanh trong 20 s,
  `/health` công khai trả `"version":"2.3.3"`.
- Cập nhật `CI-CD/README.md`, `CI-CD/learn.md` và runbook registry.

---

## 2.3.2 — 2026-11-27

- Tối ưu hiệu năng backend:
  - dùng chung HTTP client + LRU cache;
  - FTS PostgreSQL (worst ~236 ms, selective ~0.2 ms);
  - FAISS IVF-PQ khi vượt ngưỡng vector;
  - song song hóa search/embedding.
- Bổ sung composite index cho các truy vấn nóng.
- Hồi quy pytest + endpoint test trước khi phát hành.

---

## Các mốc trước 2.3.2

- Phase 1 (Sprint 1–8): multi-tenant RLS, JWT rotation, ingestion zero-storage,
  FAISS mmap + hybrid RRF, MCP server, context filtering, SSE, Docker/e2-micro.
- Phase 2 (F1–F4): frontend 3 tầng, Knowledge Hub, RAG chat SSE, dashboards realtime,
  Nginx/Docker/Playwright E2E.
- Phase 3 (Sprint 9): tool `phase3_ux_sim`, workflow thủ công, chạy live đầu tiên,
  MCP zero-knowledge PASS vào DB dự án cũ; telemetry Supabase/k6/soak còn pending.

Chi tiết tiến độ từng sprint: `task/ROADMAP.md` và các file `task/sprint-*.md`.

---

## Ghi chú dữ liệu chat trace (2.4.0)

- Mỗi lượt lưu `contexts` + `llm_context`, ước tính 10–20 KB/lượt. Với ngân sách
  ~300 lượt chat/ngày, khoảng 3–6 MB/ngày; retention 365 ngày cần dự trù ~1–2 GB.
- Bảng có RLS theo tenant; API history chỉ trả lượt của chính user.
- Có thể tắt hoàn toàn bằng `CHAT_HISTORY_ENABLED=false` nếu không muốn lưu nội dung.
- File export RAGAS chứa nội dung thật: không commit; `.gitignore` đã chặn `ragas_export/`,
  `ragas_*.jsonl` và `ragas_*.jsonl.gz`.
- Prune dùng `scripts/prune_chat_history.py` (mặc định/tối đa 365 ngày); chưa bật cron tự động,
  chạy thủ công sau khi đã export tháng cũ.