# History — các thay đổi theo phiên bản

Tài liệu này ghi lại thay đổi kỹ thuật và lỗi đã sửa theo từng phiên bản.
Nguồn chuẩn của version backend là `backend/app/core/version.py`.

---

## 2.6.0 — 2026-11-28

**Trạng thái:** đã code + 80 test backend PASS + frontend build PASS;
**chưa build/push/deploy** (production đang 2.4.0; 2.4.1/2.4.2/2.5.0/2.6.0 cùng chờ deploy).

### Session memory do chính LLM trả về trong cùng một call (~300 token)

- Bỏ cách nhồi raw Q/A của 3 lượt gần nhất làm mặc định; prompt yêu cầu model trả
  câu trả lời trước, rồi một khối `<session_memory>...</session_memory>` tóm tắt
  ≤ 250–300 token (thực thể, con số, ràng buộc, mục tiêu, điều đã trả lời).
- Backend tách khối memory khỏi câu trả lời:
  - non-stream: `PromptService.split_answer_and_memory`;
  - SSE: `SessionMemoryStreamFilter` giữ memory ở server, không phát ra UI,
    xử lý được thẻ bị chia qua nhiều token và thẻ mở nhưng không đóng.
- Memory lưu vào `chat_turns.session_memory` của chính lượt đó; lượt sau đọc bản mới nhất
  với đúng tenant/user/session. Rollback xoá lượt cuối nên memory tự quay về bản trước.
- Memory bắt đầu ngay từ lượt đầu tiên của session; không cần endpoint tạo session riêng.
- Nếu model/mock không trả khối memory, fallback extractive từ câu trả lời
  (`PromptService.build_fallback_memory`); session cũ chưa có memory dùng nén extractive
  3 lượt gần nhất (`CHAT_HISTORY_CONTEXT_TURNS`/`CHAT_HISTORY_CONTEXT_TOKENS`).
- Cấu hình mới `CHAT_SESSION_MEMORY_TOKENS` (mặc định 300).
- Đánh đổi: memory là output token của cùng call nên tăng ~200–300 output token/lượt
  và phụ thuộc model tuân thủ thẻ; filter + fallback bảo đảm không lộ memory ra UI.
  Không gọi thêm LLM như phương án tóm tắt riêng.

### Tài liệu đính kèm riêng trong chat (ephemeral, không vào FAISS)

- Migration `0011_add_chat_attachments`:
  - `chat_attachments` (owner + session + TTL), `chat_attachment_chunks`
    (nội dung + embedding float32 BYTEA);
  - RLS tenant trên cả hai bảng;
  - `chat_turns.has_attachment` và `chat_turns.session_memory`.
- `POST /api/v1/chat/attachments`, `GET /api/v1/chat/attachments`,
  `DELETE /api/v1/chat/attachments/{id}`:
  - chỉ owner + đúng session đọc được; admin tenant cũng không đọc qua API attachment;
  - tối đa 3 file/phiên, 10 file còn hạn/user, 120 chunk/file, 8 MB/file;
  - TTL mặc định 24h (tối đa 168h), kèm script prune;
  - tối đa 2 upload xử lý song song (`CHAT_ATTACHMENT_MAX_CONCURRENT_UPLOADS`);
    semaphore được acquire **trước khi đọc bytes** nên 10 người upload cùng lúc sẽ
    xếp hàng thay vì cùng giữ 8 MB/file trong RAM backend 350 MB.
- Không dùng mmap cho file chat: file gốc bị hủy sau parse, vector chỉ vài trăm
  chunk lưu BYTEA trong PostgreSQL; mmap không giảm peak parse/embed. FAISS mmap
  vẫn chỉ dành cho index tenant.
- Không ghi FAISS, không tạo file index theo user: chunk/embedding nằm trong PostgreSQL,
  chat tính cosine in-process trên đúng tập chunk của session (tối đa vài trăm vector).
  Nhờ vậy không làm bẩn index tenant, không tăng số file index, và không có chuyện
  top-K của người khác lấn át tài liệu private.
- Query embedding được tính một lần và dùng chung cho hybrid search + attachment search.
- Attachment context đi qua đúng `ContextFilterPipeline` 3 tầng với ngân sách riêng
  (`CHAT_ATTACHMENT_MAX_CONTEXT_TOKENS`, mặc định 300) trước khi vào prompt.
- Lượt chat dùng attachment được đánh dấu `has_attachment=true` và **luôn bị loại khỏi
  export RAGAS**, kể cả khi bật `--include-mock`.
- Record RAGAS có thêm `metadata.has_attachment` và `metadata.session_memory` để phân tích
  multi-turn; memory không nằm trong `retrieved_contexts` và bị che (`null`) nếu lượt private
  (phòng thủ nhiều lớp dù SQL đã lọc).
- Job dọn TTL: `scripts/prune_chat_attachments.py` (dry-run/batch/tenant), đã copy vào image.
- Đánh đổi: file không dùng lại ngoài phiên/TTL; nội dung câu trả lời vẫn có thể chứa
  thông tin private nên export bị loại, nhưng người có quyền DB/admin hệ thống vẫn là
  ranh giới tin cậy. Muốn thư viện cá nhân lâu dài phải chuyển sang mô hình `visibility`
  + ACL owner trong kho tri thức, không dùng bảng ephemeral này.

### UI

- Chat có nút đính kèm, chip file + số đoạn + hạn dùng, xoá file; danh sách được nạp lại
  sau reload theo session.
- Hiển thị **Nguồn đính kèm** trong câu trả lời; ghi rõ file chỉ thuộc phiên và tự hết hạn.

---

## 2.5.0 — 2026-11-28

**Trạng thái:** đã code + 66 test backend PASS + frontend build PASS;
**chưa build/push/deploy** (production đang 2.4.0; 2.4.1/2.4.2/2.5.0 cùng chờ deploy).

### Session memory, nạp lại hội thoại và rollback

- Thêm session memory ngắn vào prompt:
  - lấy tối đa `CHAT_HISTORY_CONTEXT_TURNS` lượt gần nhất (mặc định 3) của đúng
    tenant/user/session;
  - giới hạn `CHAT_HISTORY_CONTEXT_TOKENS` (mặc định 500) để không phình 16K TPM;
  - prompt ghi rõ `<conversation>` chỉ để hiểu câu hỏi nối tiếp, không phải chứng cứ.
- Frontend giữ `session_id` theo tenant/user trong localStorage:
  - gửi kèm mọi request chat;
  - reload trang tự gọi `/chat/history?session_id=...` và dựng lại các cặp user/assistant;
  - nút **Cuộc trò chuyện mới** tạo session mới, không trộn với hội thoại cũ.
- Thêm `POST /api/v1/chat/rollback`:
  - xoá lượt gần nhất của chính user trong session;
  - ghi telemetry `chat_rollback`;
  - lượt đã xoá cũng không còn trong session memory và file export RAGAS.
- Frontend thêm nút **Hoàn tác lượt cuối**; chỉ bật khi lượt cuối là câu trả lời đã hoàn tất.
- Không cần migration mới; dùng bảng `chat_turns` từ 2.4.0.
- Đánh đổi: rollback là hard-delete để đúng kỳ vọng “xoá khỏi lịch sử”; nếu cần audit
  đầy đủ về sau nên chuyển sang soft-delete.

---

## 2.4.2 — 2026-11-28

**Trạng thái:** đã code + 63 test backend PASS; **chưa build/push/deploy**
(production đang 2.4.0; 2.4.1 cũng chưa deploy).

### Chuyển backend LLM sang Gemini 3.5 Flash Lite

- Đổi mặc định `LLM_MODEL` thành `gemini-3.5-flash-lite` trong config backend,
  `.env.example` và hai compose file.
- Vẫn dùng **một model duy nhất**; chưa bật pool 3.5 → 3.1. Cơ chế retry
  Exponential Backoff với 429/503 và fallback mock của mode `hybrid` giữ nguyên.
- Không đổi embedding (`gemini-embedding-001`), context filter, quota tenant
  30 RPM / 16K TPM, prompt grounding và sanitizer 2.4.1.
- Cập nhật test dashboard/e2e sang model mới; 63/63 test PASS.
- **Lưu ý deploy:** `.env` trên VPS (và `backend/.env` local nếu có) đang ghi đè
  compose default. Phải sửa `LLM_MODEL` trong `.env` thành `gemini-3.5-flash-lite`
  trước khi recreate backend, nếu không container vẫn dùng Gemma cũ.
- Hạn mức RPM/TPM thực tế của Gemini 3.5 Flash Lite cần đối chiếu Google AI Studio;
  hệ thống vẫn tự áp trần tenant 30 RPM / 16K TPM.

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