# Load & stress test cho Enterprise AI Knowledge Platform

Bộ test này dùng để đo **load, soak, saturation, spike, SSE, IDOR và ingest** trên
backend EKP. Mục tiêu là tìm điểm gãy và chứng minh hệ thống **từ chối an toàn**
(429/503) thay vì OOM hoặc mất dữ liệu.

> Chỉ chạy trên hệ thống bạn sở hữu, ưu tiên staging/local. Không chạy stress vào
> production đang có khách hàng. Không dùng tài liệu thật/PII. Load generator phải
> chạy **ngoài VPS backend** (laptop, máy riêng hoặc HF Jobs).

## 1. Thành phần

| File | Mục đích |
|---|---|
| `generate_fixtures.py` | Sinh TXT/DOCX/XLSX/PDF, file lỗi, file lớn và 200 file ingest unique |
| `seed_test_data.py` | Tạo 2 tenant synthetic, 10 user tenant A, tài liệu mẫu; ghi config runtime |
| `lib.py` | Helper login/config/HTTP dùng chung cho script Python |
| `k6/journey1_employee.js` | Soak nhân viên: login, refresh, search, list, workspace, chat, feedback |
| `k6/journey2_ingest.js` | Upload tài liệu qua k6 |
| `k6/saturation.js` | Ramp 10→60 VU tìm 429/5xx đầu tiên |
| `k6/spike.js` | Spike 0→20 VU trong 5 giây, kiểm tra phục hồi |
| `sse_probe.py` | Đo TTFB/event SSE cho dashboard realtime hoặc chat stream |
| `idor_probe.py` | Kiểm tra tenant A không đọc/search được tài liệu tenant B |
| `ingest_stress.py` | Stress upload đồng thời, size ramp, duplicate race, failure injection |
| `mock_embedding_server.py` | Mock Gemini embedding, deterministic, không đốt quota |

Config runtime và fixture sinh tự động nằm trong:

```text
load_tests/.runtime/config.json
load_tests/fixtures/generated/
load_tests/reports/
```

Các đường dẫn này đã được `.gitignore` chặn. Không commit config/report thô.

## 2. Chuẩn bị

Yêu cầu:

- Python 3.11+ và dependencies của repo: `pip install -r requirements.txt`
- [k6](https://k6.io/docs/get-started/installation/) cho các bài `k6/`
- Trong repo này venv nằm ở `.venv`: Windows dùng `.venv/Scripts/python.exe`, Linux/macOS dùng `.venv/bin/python` thay cho `python` trong các lệnh dưới
- Backend đang chạy, đã migrate DB và có ít nhất 2 tenant FREE
- Tài khoản test synthetic; **không dùng tài khoản khách hàng**

Từ thư mục gốc repo:

```bash
python load_tests/generate_fixtures.py --large
python load_tests/seed_test_data.py --base-url http://localhost:8000
```

`seed_test_data.py` tạo:

- Tenant A: owner + 9 employee (đủ trần 10 user FREE)
- Tenant B: owner + 1 tài liệu “secret” để test IDOR
- 2 tài liệu mẫu tenant A
- `load_tests/.runtime/config.json` chứa email/password synthetic, tenant ID,
  document ID và danh sách fixture

Nếu backend chạy Docker, đặt `BASE_URL` trỏ tới địa chỉ backend mà máy test truy cập
được (ví dụ `https://staging.example.com`), không dùng `localhost` từ trong container.

### Windows: double-click

Double-click `run_load_tests.cmd` ở thư mục gốc repo. File tự dùng
`.venv\Scripts\python.exe`, tự kiểm tra `/api/v1/health`, tự seed nếu thiếu hoặc nếu
`base_url` trong config khác target hiện tại, và cảnh báo khi target không phải local.
Menu gồm smoke, seed, IDOR, SSE, ingest stress và các bài k6 (k6 phải cài riêng; nếu
chưa có, file in link MSI và bỏ qua các mục k6). Target mặc định lấy từ
`load_tests/.runtime/config.json`, đổi bằng phím `T`.

## 3. Soak nhân viên — Journey 1

Mỗi VU là một nhân viên. Mỗi phiên khoảng 90 request, think-time 15–18 giây, sau đó
nghỉ 0–10 phút; mặc định chat dùng `mode=mock` để không đốt quota Gemini.

```bash
# 4 giờ, 3 VU
k6 run -e VUS=3 -e DURATION=4h load_tests/k6/journey1_employee.js

# 2 giờ, 5 VU
k6 run -e VUS=5 -e DURATION=2h load_tests/k6/journey1_employee.js
```

Các biến môi trường:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `VUS` | `3` | Số nhân viên đồng thời |
| `DURATION` | `30m` | Thời lượng bài chạy |
| `CHAT_MODE` | `mock` | `mock`, `retrieval_only`, `hybrid`, `real` |
| `CHAT_STREAM` | `false` | `true` để gọi `/chat/stream` thay vì `/chat/query` |

Chỉ dùng `CHAT_MODE=hybrid/real` khi đã có quota và xác nhận rõ ràng. Trong soak,
chat chiếm khoảng 4% request để giữ dưới ngân sách 300 lượt/ngày.

Ghi summary ra file:

```bash
k6 run -e VUS=3 -e DURATION=4h \
  --summary-export load_tests/reports/journey1_summary.json \
  load_tests/k6/journey1_employee.js
```

## 4. Upload tài liệu — Journey 2

```bash
k6 run -e VUS=1 -e DURATION=10m load_tests/k6/journey2_ingest.js
```

Mỗi iteration nối thêm một marker unique vào file mẫu nên hash khác nhau; `409` vẫn
được chấp nhận nếu file đã tồn tại. `413` là đúng khi vượt giới hạn 25 MB của app
hoặc 26 MB của Nginx. `429/503` là bảo vệ quá tải, không phải crash.

## 5. Saturation và spike

```bash
# Ramp 10 → 20 → 40 → 60 VU; 429 xuất hiện là mục tiêu, không phải lỗi
k6 run --summary-export load_tests/reports/saturation_summary.json \
  load_tests/k6/saturation.js

# Spike 0 → 20 VU trong 5 giây
k6 run --summary-export load_tests/reports/spike_summary.json \
  load_tests/k6/spike.js
```

Theo dõi song song trên VPS:

```bash
docker stats --no-stream saas_fastapi_backend saas_postgres_db saas_nginx
docker logs --tail 100 saas_fastapi_backend
docker exec saas_postgres_db psql -U saas_app -d saas_knowledge_db \
  -c "select count(*), state from pg_stat_activity group by state;"
ss -s
df -h
```

Phân biệt:

- **429 ứng dụng**: token bucket tenant, thường có `Retry-After`.
- **503 Nginx**: `limit_req` 600 r/m burst 100.
- **5xx ứng dụng**: lỗi thật, phải điều tra.

Dừng ngay nếu RSS backend vượt 300 MB, 5xx > 1% kéo dài, DB connection cạn hoặc đĩa > 80%.

## 6. SSE probe

k6 buffer toàn bộ response nên không đo chính xác từng event SSE. Dùng script Python:

```bash
# Dashboard realtime, đo event đầu tiên trong 30 giây
python load_tests/sse_probe.py --mode dashboard --duration 30

# Chat stream
python load_tests/sse_probe.py --mode chat --duration 30 \
  --query "Chính sách nghỉ phép của nhân viên là gì?"
```

Kết quả JSON gồm `first_event_ms`, `total_ms`, `events`, `bytes`; ghi vào
`load_tests/reports/sse_*.json`.

## 7. IDOR/BOLA probe

```bash
python load_tests/idor_probe.py
```

Script login tenant A và B, kiểm tra A không đọc được document/tenant B, search A
không trả tài liệu B, và B vẫn đọc được tài liệu của mình. Script chỉ dùng GET/POST
đọc, không xoá/sửa dữ liệu. Exit code 1 nếu có vi phạm.

## 8. Stress ingest

### 8.1. Dùng mock embedding (khuyến nghị)

Không gọi Google, không đốt quota. Chạy mock server:

```bash
python load_tests/mock_embedding_server.py --port 8099 --delay-ms 200
```

Cấu hình backend (staging/local):

```text
EMBEDDING_API_URL=http://127.0.0.1:8099
GOOGLE_API_KEY=mock-key
```

Nếu backend chạy trong Docker, `127.0.0.1` là container, không phải host. Dùng IP host
(mặc định Linux bridge thường là `172.17.0.1`), `host.docker.internal` nếu đã cấu hình,
hoặc chạy backend local. Mock server bind `0.0.0.0` nếu cần:

```bash
python load_tests/mock_embedding_server.py --host 0.0.0.0 --port 8099
```

### 8.2. Các bài ingest

```bash
# Ramp upload đồng thời 1 → 2 → 4 → 8, mỗi mức 30 giây
python load_tests/ingest_stress.py --mode concurrency --levels 1,2,4,8 --duration 30

# Size ramp: 1KB → 5MB → DOCX/PDF; thêm --large để có 24MB
python load_tests/ingest_stress.py --mode size

# 10 upload cùng file: đúng 1 thành công, còn lại 409
python load_tests/ingest_stress.py --mode duplicate --duplicate-concurrency 10

# Failure injection: chạy mock server với --fail-after 1 --fail-status 500
python load_tests/ingest_stress.py --mode failure
```

Tắt đo RSS nếu không có Docker CLI:

```bash
python load_tests/ingest_stress.py --mode concurrency --levels 1,2 --container ""
```

Kết quả JSON ghi vào `load_tests/reports/ingest_<timestamp>.json`, gồm status counts,
p50/p95, RSS peak và integrity check qua API. Kiểm tra thêm log backend để chắc chắn
không có vector mồ côi sau failure injection.

## 9. Tiêu chí đọc kết quả

| Bài | Pass | Fail cần điều tra |
|---|---|---|
| Soak Journey 1 | 0 5xx, p95 API < 1,5 s, RSS tăng < 10% sau warm-up | 5xx, 429 ngoài dự kiến, RSS tăng liên tục, FD/connection leak |
| Saturation | Có 429/503, health vẫn 200, 5xx < 1%, không OOM | 5xx cao, health chết, Postgres restart, đĩa đầy |
| Spike | 429/503 tạm thời, hồi phục sau khi giảm tải | Hệ thống không hồi phục, queue không drain |
| Ingest | 2 upload 5MB đồng thời 0 5xx, RSS < 300MB; duplicate đúng 1 success | OOM, 500 duplicate, orphan vector, mất tài liệu |
| IDOR | Tất cả check `pass: true` | Bất kỳ 200 trái phép hoặc search lộ tài liệu B |

Luôn ghi kèm: commit/version backend, thời gian UTC, profile tải, số user, mode chat,
và các thay đổi cấu hình trong ngày đo.

## 10. Lưu ý vận hành

- Không chạy k6/soak trên chính VPS backend: tranh CPU/RAM, làm sai p95 và dễ OOM.
- Không tăng request/giờ để bù thời gian: 25 RPM là trần an toàn; 8.000 request
  trong 4 giờ tương đương 33 RPM, vượt trần.
- `409` duplicate, `413` quá lớn, `429` rate limit, `503` quá tải là các kết quả
  **có kiểm soát**. Chỉ 5xx bất ngờ/OOM/mất dữ liệu mới là lỗi hệ thống.
- `ingest_stress.py` chỉ kiểm tra toàn vẹn qua API. Nếu cần kiểm tra orphan vector
  trong FAISS, dừng tải, backup rồi đối chiếu `faiss_id` trong DB với index trên
  staging; không chạy truy vấn nặng trên production.
- Sau test, xoá tài liệu synthetic qua UI/API hoặc dùng tenant staging riêng.
  Không chạy `docker compose down -v`, không xoá volume.

## 11. Troubleshooting

| Lỗi | Cách xử lý |
|---|---|
| `Không thấy load_tests/.runtime/config.json` | Chạy `seed_test_data.py` trước |
| `Login ... 401` | Kiểm tra base URL, email/password synthetic, tenant còn active |
| `409` khi seed | Bình thường nếu đã seed; script lấy document cũ theo tên |
| `413` | File vượt 25 MB app hoặc 26 MB Nginx; dùng fixture nhỏ hơn |
| `429` trong soak | Giảm VU hoặc chat fraction; 429 không được xuất hiện trong soak |
| Mock embedding không kết nối | Backend Docker không thấy `127.0.0.1` của host; dùng IP host hoặc chạy local |
| `docker stats` lỗi | Đặt `--container ""` để tắt đo RSS |
| k6 báo thiếu config | Chạy k6 từ repo root; config được `open()` tương đối với file script |

## 12. Liên hệ với Sprint 9

Các bài trong thư mục này tương ứng:

- 9.1–9.2: `journey1_employee.js`
- 9.3: `saturation.js`, `spike.js`
- 9.4: soak campaign dùng `journey1_employee.js` 2–4h/ngày
- 9.9: đo egress từ Nginx/GCP Billing (không nằm trong script)
- 9.12: simulator UX chạy trên GitHub Actions (không dùng k6)
- 9.17: `ingest_stress.py`, `mock_embedding_server.py`, `generate_fixtures.py`

Báo cáo nên lưu số tổng hợp (không chứa email/token/nội dung thật) vào
`load_tests/reports/` và chỉ commit bản tổng hợp đã làm sạch nếu cần.