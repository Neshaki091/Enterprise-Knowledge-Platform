# How to Evaluate — Load Tests & Phase 3 UX Simulation

Tài liệu này hướng dẫn cách đọc kết quả, đặt ngưỡng pass/fail và biến số đo thành
hành động cải thiện cho backend EKP 2.6.0 (1 GB RAM, 1 async worker, PostgreSQL 16,
FAISS mmap, quota mặc định 30 RPM/tenant và 16K TPM).

Nguyên tắc: **load test đo hạ tầng**, **Phase 3 đo hành vi UX + LLM thật**,
**RAGAS đo chất lượng câu trả lời**. Không dùng số của tầng này để kết luận tầng kia.

---

## 0. TL;DR

- Baseline 2.6.0 (2026-11-28) đang ổn định: 0 lỗi 429/5xx, MCP PASS, 100 user/10 tenant.
- Nhưng mẫu chat còn ít (1–4 lượt/run), chat E2E 15–18 s và refusal metric 100% trên
  4 mẫu — **chưa đủ để kết luận chất lượng RAG**.
- Muốn đánh giá chất lượng: tạo golden dataset 20 câu (9.6), export 50–100 chat turn
  thật, chấm RAGAS bên ngoài (9.10/9.11).
- Muốn đo tải 2 giờ: k6 **5 VU, `CHAT_MODE=hybrid`** (khoảng 90 chat/2h, ~20 RPM)
  hoặc `mock` nếu chỉ đo hạ tầng. Không chạy 500 active.
- Phase 3 tối đa nên giữ **100 user pool, 20–100 active/run**; 500 active × 4 bước
  cần ~2.000 call simulator, vượt quota 960 RPD.

---

## 1. Nguyên tắc đo

1. **Cùng điều kiện**: cùng commit/version, cùng target, cùng cấu hình VU/active/steps/
   chat mode. Ghi ngày UTC, người chạy, thay đổi cấu hình trong ngày.
2. **Đo trước/sau**: mỗi thay đổi prompt, model, retrieval, index hoặc ingest chỉ
   thay một biến; so với baseline ngay trước đó.
3. **Mẫu đủ lớn**: Phase 3 là stochastic, chạy **3 lần** cùng cấu hình rồi lấy trung vị;
   không so sánh hai lần chạy đơn lẻ.
4. **Tách tầng**:
   - Load: k6, mock chat, đo p95/429/RSS/DB/SSE.
   - Phase 3: hành vi người dùng + LLM thật, mẫu nhỏ, dùng cho UX scorecard.
   - RAGAS: chất lượng câu trả lời, cần golden/reference và judge bên ngoài.
5. **Synthetic only**: không dùng dữ liệu khách hàng/PII; không commit secret, file
   export thô, report có nội dung hội thoại.
6. **Không ngoại suy quá mức**: 30 phút không phải soak 5 ngày; 100 user synthetic
   không phải “100 khách hàng thật”.

---

## 2. Baseline hiện tại (2026-11-28, backend 2.6.0)

| Run | Cấu hình | Hành động | Search P95 | Chat | MCP | Egress | Thời gian |
|---|---|---:|---:|---|---|---:|---:|
| Quick | 20 seed, 5 active, 3 bước, 5 query | 15 (100%) | 501 ms | 0 | PASS | 24,64 KB | ~78 s |
| Standard | 100 seed, 20 active, 4 bước, 20 query | 68 (100%) | 812 ms | 1 (18,26 s) | PASS 7,1 s | 98,26 KB | 4m51 |
| Full | 100 active, 4 bước, 50 query | 336 (100%) | 1.076 ms | 4 (P50 15,44 / P95 17,87 s) | PASS 8,05 s | 560,37 KB | 10m22 |

Ghi chú:

- Full run: TTFT stream `388,6 ms`; nén context `19,8%`; RAG query dùng `4/50`;
  simulator 234 call, 21 failover; RPD còn `369/323`.
- Standard: nén context `24,3%`; chat duy nhất 18,26 s; RAG query `1/20`.
- Quick: 0 chat nên TTFT/chat latency/nén/refusal đều bằng 0 — **không phải lỗi**.
- Refusal 100% của full run là metric đếm thô: 3/4 câu hỏi ngoài tài liệu synthetic,
  câu stream còn lại trả lời một phần nhưng vẫn chứa cụm “không tìm thấy thông tin”.
  Đây là vấn đề **độ phủ dataset**, không phải bằng chứng RAG kém.

---

## 3. Đánh giá Load Test (k6 + probe + ingest)

### 3.1 Mục đích từng bài

| Bài | Đo cái gì | Khi nào chạy |
|---|---|---|
| Journey 1 soak | Hành vi đọc/chat lặp, p95 API, 429, RSS, DB pool | Mỗi tuần / sau thay đổi backend |
| Journey 2 upload | Upload qua k6, 409/413, dung lượng | Sau thay đổi ingest |
| Saturation | Ramp 10→60 VU, tìm 429/503 đầu tiên, điểm bão hòa | Trước release lớn / hàng tháng |
| Spike | 0→20 VU trong 5 s, khả năng hồi phục | Trước release lớn |
| SSE probe | TTFB, số event, byte stream | Sau thay đổi SSE/Nginx |
| IDOR/BOLA | Tenant A không đọc/search tài liệu B | Mỗi release |
| Ingest stress | Upload đồng thời, size ramp, duplicate race, failure injection, RSS, orphan | Sau thay đổi ingest/FAISS/queue |
| Mock embedding | Giữ quota, deterministic, test failure | Mọi bài ingest lặp lại |

### 3.2 Cách chạy

```bat
:: Sinh fixture + seed 2 tenant synthetic
python load_tests/generate_fixtures.py --large
python load_tests/seed_test_data.py --base-url https://<staging-host>

:: Soak 2h, 5 VU, chat mock (đo hạ tầng)
k6 run -e VUS=5 -e DURATION=2h -e CHAT_MODE=mock ^
  --summary-export load_tests\reports\soak_2h_mock.json ^
  load_tests\k6\journey1_employee.js

:: Soak 2h, 5 VU, chat thật (đo LLM + hạ tầng; ~90 chat/2h)
k6 run -e VUS=5 -e DURATION=2h -e CHAT_MODE=hybrid ^
  --summary-export load_tests\reports\soak_2h_hybrid.json ^
  load_tests\k6\journey1_employee.js

:: Bão hòa và spike
k6 run --summary-export load_tests\reports\saturation_summary.json load_tests\k6\saturation.js
k6 run --summary-export load_tests\reports\spike_summary.json load_tests\k6\spike.js

:: SSE + IDOR
python load_tests/sse_probe.py --base-url https://<host> --mode dashboard --duration 30
python load_tests/sse_probe.py --base-url https://<host> --mode chat --duration 30 --chat-mode mock
python load_tests/idor_probe.py --base-url https://<host>

:: Ingest stress (mock embedding)
:: Terminal 1: mock server; Terminal 2: các lệnh ingest
python load_tests/mock_embedding_server.py --port 8099 --delay-ms 200
python load_tests/ingest_stress.py --base-url http://localhost:8000 --mode concurrency --levels 1,2,4,8 --duration 30
python load_tests/ingest_stress.py --base-url http://localhost:8000 --mode size
python load_tests/ingest_stress.py --base-url http://localhost:8000 --mode duplicate --duplicate-concurrency 10
```

Lưu ý:

- `CHAT_MODE=mock` **vẫn gọi** `/chat/query|/chat/stream` và chạy retrieval/SSE/DB;
  chỉ bỏ bước gọi Google LLM.
- 5 VU là mức soak an toàn cho Tenant A (~20 RPM). 10+ VU cần chia nhiều tenant;
  load seed hiện chỉ có 10 employee Tenant A + 1 owner B.
- Không chạy k6 trên VPS backend; không stress production trong giờ phục vụ.
- k6 chưa cài trên máy Windows hiện tại: dùng MSI `https://dl.k6.io/msi/k6-latest-amd64.msi`
  hoặc `winget install k6.k6` (nếu máy chưa có k6).

### 3.3 Ngưỡng đọc kết quả

| Chỉ số | Pass | Fail cần điều tra |
|---|---|---|
| 5xx | 0 trong soak | > 1% kéo dài, 500 bất ngờ |
| `http_req_failed` | < 1% | > 1% |
| 429 | 0 ngoài bài saturation | 429 trong soak, 429 lan rộng |
| API p95 | < 1,5 s | > 1,5 s trong 10 phút |
| SSE TTFB p95 | < 1,5 s | > 1,5 s hoặc stream đứt |
| Chat tổng (hybrid) | 5–15 s | > 15 s (hiện 15–18 s cần xem) |
| RSS backend | < 300 MB, tăng < 10% sau warm-up | tăng liên tục, OOM |
| DB connection | < pool 10+5 | cạn pool, chờ lâu |
| FD / đĩa | ổn định / < 80% | rò rỉ FD, đĩa tăng nhanh |
| Egress | 0,6–0,8 GB/tháng | chạm 80% (0,8 GB) |
| Saturation | Có 429/503, health 200, 5xx < 1% | health chết, Postgres restart, OOM |
| Spike | 429/503 tạm thời, hồi phục | không hồi phục, queue không drain |
| Ingest | 2 upload 5 MB đồng thời 0 5xx, RSS < 300 MB | OOM, 500, orphan |
| Duplicate | đúng 1×201, còn lại 409 | 500, nhiều row/vector |
| IDOR | tất cả check `pass: true` | 200 trái phép, search lộ tenant B |

### 3.4 Đọc lỗi: 429 ứng dụng vs 503 Nginx vs 5xx

- **429 ứng dụng**: token bucket tenant; thường có `Retry-After`. Nguyên nhân: VU vượt
  30 RPM/tenant (mặc định, có thể tenant tự hạ) hoặc chat vượt 16K TPM.
- **503 Nginx**: `limit_req` 600 r/m burst 100; khác hoàn toàn 429 ứng dụng.
- **5xx ứng dụng**: lỗi thật; xem `docker logs`, `pg_stat_activity`, FAISS lock, RAM.
- **p95 cao nhưng CPU thấp**: nghẽn external (embedding/LLM), DB hoặc khóa FAISS.
- **p95 cao + CPU cao**: search/ingest/parse; xem index, chunk, batch embedding.

Lệnh quan sát song song trên VPS:

```bash
docker stats --no-stream saas_fastapi_backend saas_postgres_db saas_nginx
docker logs --tail 100 saas_fastapi_backend
docker exec saas_postgres_db psql -U saas_app -d saas_knowledge_db \
  -c "select count(*), state from pg_stat_activity group by state;"
ss -s
df -h
```

Điều kiện dừng khẩn: RSS backend > 300 MB, 5xx > 1% kéo dài, DB connection cạn,
đĩa > 80%, OOM hoặc health 503.

### 3.5 Cải thiện theo triệu chứng

| Triệu chứng | Nguyên nhân thường gặp | Hành động |
|---|---|---|
| 429 trong soak | 5+ VU dồn một tenant, chat quá nhiều | Giảm VU, chia tenant, dùng mock, kiểm tra token bucket |
| 503 Nginx | `limit_req` thấp hơn tải | Không tăng mù; thêm tenant/instance hoặc giảm VU |
| 5xx | DB pool cạn, FAISS lock, RAM, lỗi parse | Xem log; tăng pool có kiểm soát; sửa ingest; restart có giám sát |
| RSS tăng | Rò rỉ, giữ file/response, FAISS | So sánh sau warm-up; kiểm tra upload/attachment; mmap |
| Search p95 tăng | Embedding/FTS/FAISS/DB | Cache, parallel, IVF-PQ `nprobe`, index, top_k |
| SSE TTFB cao | Nginx buffering, LLM chậm | `proxy_buffering off`, stream, context nhỏ hơn |
| Ingest 5xx/OOM | Đọc 25 MB không semaphore, embedding concurrency | Semaphore trước `file.read`, global embedding semaphore, cap chunk |
| Duplicate 500 | Race unique `(tenant_id,file_hash)` | Bắt `IntegrityError` → 409, khóa quota |
| Orphan vector | Commit DB/FAISS lệch | Rollback có kiểm soát, reconcile index, failure test |
| Egress tăng | Chat thật, asset, export | Ngân sách chat, Vercel asset, gzip, prune |

---

## 4. Đánh giá Phase 3 UX Simulation

### 4.1 Đo gì và không đo gì

- **Đo**: phân bố hành động, tỷ lệ thành công, 429 friction, search p95, TTFT/chat E2E,
  nén context, refusal/follow-up, satisfaction, MCP, egress, LLM pool.
- **Không đo**: chất lượng RAG tổng thể. Simulator hỏi ngẫu nhiên, không có reference;
  nhiều câu ngoài tài liệu synthetic. Muốn chấm chất lượng phải dùng golden dataset/RAGAS.
- **Active ≠ concurrent**: `active_users` là số user được sample; simulator chỉ chạy
  tối đa 5 session đồng thời. 100 active không phải 100 người online cùng lúc.
- **Seed pool ≠ active**: 100 user/10 tenant là pool 1-time; 20 active/run là mẫu ngày.

### 4.2 Cách chạy

```bat
:: Quick: 20 seed, 5 active, 3 bước, tối đa 5 query
.venv\Scripts\python.exe -m phase3_ux_sim.runner ^
  --target-users 20 --active-users 5 --steps 3 --max-llm-queries 5 --source local

:: Standard: seed đủ 100, 20 active, 4 bước, 20 query
.venv\Scripts\python.exe -m phase3_ux_sim.runner ^
  --target-users 100 --active-users 20 --steps 4 --max-llm-queries 20 --source local

:: Full coverage: 100 active (không seed thêm nếu đã đủ pool), 50 query
.venv\Scripts\python.exe -m phase3_ux_sim.runner ^
  --target-users 100 --active-users 100 --steps 4 --max-llm-queries 50 --source local

:: Local offline, không gọi Google/MCP
.venv\Scripts\python.exe -m phase3_ux_sim.runner ^
  --target-url asgi://local --target-users 20 --active-users 5 --steps 4 ^
  --offline-llm --skip-mcp --source local
```

Hoặc double-click `run_phase3_ux_sim.cmd`; target lấy từ `phase3_ux_sim/.env_vu`.
File này tự dùng SQLite riêng theo target (`sim_target_<hash>.db`) nếu chưa có
`SUPABASE_DB_URL`.

### 4.3 Chỉ số và cách đọc

| Chỉ số | Công thức / nguồn | Đọc thế nào |
|---|---|---|
| Tổng hành động / thành công | 2xx–3xx / tổng | Mẫu nhỏ: 15–68 actions; full 336 |
| 429 friction | 429 / tổng | 0 ngoài bài stress; >0 là chạm quota |
| TTFT | chỉ `chat_stream`, `ttfb_ms` | 0 nếu không có stream; full 388,6 ms |
| Chat E2E | `chat_query` + `chat_stream` | Gồm provider; P50/P95; full 15,4/17,9 s |
| Search P95 | `search_hybrid` | 501 → 812 → 1.076 ms khi scale |
| Nén context | `1 - compressed/in` | Full 19,8%; mục tiêu kiến trúc 70% cần kiểm chứng |
| Refusal | đếm cụm “không tìm thấy thông tin” | Metric thô; phải xem từng reply |
| Follow-up | user có ≥2 chat | 0 nếu không multi-turn |
| Satisfaction | thumbs | 0 vote → 100% là mặc định, không có ý nghĩa |
| MCP | SELECT + DROP | PASS: có row, DDL bị chặn |
| LLM pool | calls/failover/waits/RPD | 234 call full; RPD còn 369/323 |
| Egress | bytes event | 560 KB full; ngoại suy tháng |
| RAG query | used / cap | 4/50 full; 0 là không chat, không phải lỗi |

### 4.4 Mẫu bao nhiêu là đủ?

| Số chat | Kết luận được gì |
|---|---|
| 0 | Không kết luận chat; chỉ đọc hành vi/search |
| 1–5 | Giai thoại; xem từng reply, không dùng P95 làm SLO |
| 10–30 | Xu hướng; so sánh trước/sau với cùng cấu hình |
| 50–100 | Đủ cho RAGAS smoke; vẫn cần golden/reference |
| >100 | Ổn cho phân tích phân bố; chú ý quota/egress |

Với 4 chat của full run, `refusal 100%` và `chat P95 17,9 s` chỉ là **chỉ báo**, không
phải kết luận. Phải xem từng event (`ux_sim_events.llm_reply`) và chấm RAGAS.

### 4.5 Cải thiện Phase 3

| Vấn đề | Cách cải thiện |
|---|---|
| Chat E2E 15–18 s | Đo TTFB vs tổng; giảm `top_k`/`max_context_tokens`; prompt ngắn hơn; stream; kiểm tra provider/16K TPM; cache |
| Search p95 tăng theo scale | FTS/FAISS/embedding; cache; IVF-PQ `nprobe`; top_k; DB; cold start |
| Refusal cao | Bổ sung tài liệu/heading; golden 8 câu trả lời được + 4 refusal + 4 follow-up; prompt/retrieval threshold |
| Ít chat | Tăng steps/active có kiểm soát; không tăng 500; dùng golden dataset để chấm trực tiếp |
| Nén thấp (19,8–24,3%) | Xem `tokens_in`/`tokens_compressed`; chunk dài/ngắn; sentence compression; mục tiêu 70% chỉ hợp lý với corpus dài |
| RPD cạn | Giảm active/steps; chia ngày; thêm model/API key có quota riêng; offline cho stress |
| MCP fail | Kiểm tra `SIM_MCP_DB_URL`, `SIM_TENANT_SECRET`, allowed tables; DDL phải bị chặn |
| Egress cao | Giảm chat thật; mock cho soak; gzip export; asset Vercel |

---

## 5. Đánh giá chất lượng RAG bằng RAGAS

### 5.1 Chuẩn bị dữ liệu

1. Tạo golden set theo 9.6: 20 câu, gồm 8 trả lời được, 4 phải từ chối, 4 follow-up
   2 lượt, 4 không kết quả rồi đổi từ khóa. Ghi `reference` + `expected_behavior`
   vào `load_tests/dataset/golden_qa.json`.
2. Chạy chat thật (hybrid) với các câu golden, không dùng câu random của simulator.
3. Export `chat_turns` theo tháng, loại `mock`/`retrieval_only` và `has_attachment=true`:

```bash
sudo docker exec saas_fastapi_backend \
  python /app/scripts/export_ragas_dataset.py --month 2026-11 \
  --limit 100 --output /tmp/ragas_2026-11.jsonl.gz
sudo docker cp saas_fastapi_backend:/tmp/ragas_2026-11.jsonl.gz ./ragas_2026-11.jsonl.gz
```

Nếu user thuộc group `docker`, bỏ `sudo`. File export chứa nội dung hội thoại:
không commit, không đưa lên GitHub.

### 5.2 Chấm điểm

- Chấm **single-turn** (lượt đầu mỗi `session_id`) và **follow-up** riêng; follow-up
  dựa vào session memory, không chỉ context.
- Có reference: `faithfulness`, `answer_relevancy`, `context_precision`,
  `context_recall`, `answer_correctness`.
- Không reference: bỏ `context_recall`; chỉ chấm faithfulness/relevancy/precision.
- Judge LLM bên ngoài backend image; xin phép trước, giới hạn RPM/mẫu; key ngoài repo.
- Báo cáo: điểm trung bình + phân bố, trích dẫn đúng, refusal đúng, lỗi điển hình.

### 5.3 Cổng hồi quy

- Đóng băng 30 mẫu đại diện (gồm refusal + follow-up), chấm trên 2.6.0 làm baseline.
- Sau mỗi thay đổi prompt/model/context, chạy lại cùng bộ.
- Chấp nhận biến động ±0,03; chặn deploy nếu giảm >0,05 ở faithfulness hoặc context
  precision. Ghi model + commit + ngày.

---

## 6. Nhịp đánh giá đề xuất

| Tần suất | Việc làm | Mục tiêu |
|---|---|---|
| Sau mỗi thay đổi | Phase 3 standard 3 lần, hoặc full 100 active 1 lần | Không regression UX/chat |
| Hàng tuần | k6 2h, 5 VU, mock; 10 phút hybrid | p95/RSS/429 ổn định |
| Hàng tháng | Saturation + Ingest stress + RAGAS 50–100 | Điểm bão hòa, chất lượng, orphan |
| Sau thay đổi ingest | Ingest concurrency/size/duplicate/failure | Không OOM/500/orphan |
| Trước release | 100 active + 10 phút hybrid + IDOR + health/version | Bằng chứng release |
| 5 ngày (chiến dịch) | 2–4h/ngày, 20 user/ngày, 3–5 VU | Soak theo giờ doanh nghiệp |

Không cần 500 active: full 100 active đã cho 336 hành động; muốn thêm mẫu chat hãy
dùng golden dataset/RAGAS, không thêm user random. Muốn 500 user pool thì seed 50
tenant (100 tài liệu) và vẫn chỉ chạy 100 active/lần; đây là coverage, không phải tải.

---

## 7. Checklist trước / trong / sau

**Trước**

- [ ] `/health` xanh, `/version` đúng commit; backup DB + FAISS nếu production.
- [ ] Seed đúng pool; target không nhầm frontend Vercel; `.env_vu`/`config.json` đúng.
- [ ] k6 đã cài; mock embedding sẵn sàng cho ingest; chạy ngoài giờ phục vụ.
- [ ] Ghi commit, cấu hình VU/active/steps/chat mode, ngày UTC.

**Trong**

- [ ] Lấy mẫu RSS/CPU/DB/FD/đĩa mỗi 5 phút (soak) hoặc mỗi mức (saturation).
- [ ] Dừng khi RSS >300 MB, 5xx >1%, DB pool cạn, đĩa >80%, OOM.
- [ ] Phân biệt 429 app vs 503 Nginx; không gộp số.

**Sau**

- [ ] Đọc report, snapshot `ux_report_<timestamp>` / k6 summary / ingest JSON.
- [ ] So baseline trước/sau; ghi rõ mẫu bao nhiêu chat; không kết luận từ 1–4 mẫu.
- [ ] Cập nhật task/README bằng số tổng hợp; không commit secret/report thô.
- [ ] Dọn dữ liệu synthetic nếu chạy staging; không xóa volume production.

---

## 8. Lệnh nhanh

```bat
:: Phase 3
run_phase3_ux_sim.cmd
.venv\Scripts\python.exe -m phase3_ux_sim.runner --target-users 100 --active-users 20 --steps 4 --max-llm-queries 20 --source local

:: Load
run_load_tests.cmd
k6 run -e VUS=5 -e DURATION=2h -e CHAT_MODE=mock --summary-export load_tests\reports\soak_2h_mock.json load_tests\k6\journey1_employee.js
k6 run -e VUS=5 -e DURATION=2h -e CHAT_MODE=hybrid --summary-export load_tests\reports\soak_2h_hybrid.json load_tests\k6\journey1_employee.js

:: Probe
python load_tests/idor_probe.py --base-url https://<host>
python load_tests/sse_probe.py --base-url https://<host> --mode chat --duration 30 --chat-mode mock

:: Ingest
python load_tests/mock_embedding_server.py --port 8099 --delay-ms 200
python load_tests/ingest_stress.py --base-url http://localhost:8000 --mode concurrency --levels 1,2,4,8 --duration 30
python load_tests/ingest_stress.py --base-url http://localhost:8000 --mode duplicate --duplicate-concurrency 10

:: RAGAS export (VPS)
docker exec saas_fastapi_backend python /app/scripts/export_ragas_dataset.py --month 2026-11 --limit 100 --output /tmp/ragas_2026-11.jsonl.gz
```

Tài liệu liên quan: `load_tests/README.md`, `phase3_ux_sim/README.md`,
`scripts/README_ragas_export.md`, `task/sprint-9-synthetic-business-hours-load.md`.