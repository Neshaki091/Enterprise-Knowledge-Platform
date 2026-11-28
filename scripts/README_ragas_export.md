# Xuất dataset RAGAS theo tháng

Mục đích: lấy dữ liệu thô từ `chat_turns` để **bên ngoài** hệ thống chấm RAGAS.
Repo không cài `ragas`, không có evaluator, không thêm dependency vào backend image.

## Cách 1 — nút trên giao diện

Đăng nhập bằng OWNER/ADMIN → **Quản trị doanh nghiệp** → chọn tháng → **Xuất JSONL.GZ**.
Nút gọi `GET /api/v1/chat/export?month=YYYY-MM` và tải file **nén gzip** của tenant hiện tại
(`.jsonl.gz`). Mặc định bỏ qua lượt `mock`/`retrieval_only`; tick ô tương ứng nếu muốn lấy cả dữ liệu test.

## Cách 2 — CLI trên VPS

`chat_turns` chứa nội dung hội thoại, nên chỉ export trên VPS và tải file về máy an toàn.
Image backend từ 2.4.0 đã chứa sẵn script tại `/app/scripts/`:

```bash
sudo docker exec saas_fastapi_backend \
  python /app/scripts/export_ragas_dataset.py --month 2026-11 \
  --output /tmp/ragas_2026-11.jsonl.gz
sudo docker cp saas_fastapi_backend:/tmp/ragas_2026-11.jsonl.gz ./ragas_2026-11.jsonl.gz
```

Script tự dùng `POSTGRES_*` có sẵn trong container, không cần truyền mật khẩu trên command line.
Mặc định loại bỏ `mock`/`retrieval_only` và nén gzip. Muốn lấy cả dữ liệu test:
thêm `--include-mock`. Muốn JSONL thô: thêm `--no-gzip` (bỏ đuôi `.gz`).

Chỉ export một tenant:

```bash
sudo docker exec saas_fastapi_backend \
  python /app/scripts/export_ragas_dataset.py --month 2026-11 \
  --tenant-id <tenant-uuid> --output /tmp/ragas_2026-11.jsonl.gz
```

Nếu image cũ chưa có script, dùng `docker cp scripts/export_ragas_dataset.py saas_fastapi_backend:/tmp/` như trước.

## Định dạng file

Mỗi dòng JSON:

```json
{
  "user_input": "câu hỏi",
  "response": "câu trả lời",
  "retrieved_contexts": ["đoạn context 1", "đoạn context 2"],
  "reference": null,
  "metadata": {
    "turn_id": "...",
    "session_id": "...",
    "tenant_id": "...",
    "chunk_ids": ["..."],
    "citations": [],
    "model": "gemma-4-26b-a4b-it",
    "mode": "hybrid",
    "created_at": "...",
    "llm_context": "ngữ cảnh đã nén gửi tới LLM",
    "retrieved_context_count": 3,
    "latency_ms": 0,
    "ttfb_ms": 0,
    "tokens": {},
    "feedback_rating": null
  }
}
```

- `retrieved_contexts` là các chunk đã đưa vào prompt (snapshot tại thời điểm trả lời).
- `llm_context` là phần ngữ cảnh sau bộ lọc 3 tầng thực sự gửi cho LLM; `retrieved_context_count` giúp kiểm tra số chunk.
- `chunk_ids` và `citations` nằm trong `metadata` để truy vết; RAGAS chỉ cần 3 trường đầu.
- Mặc định file export đã bỏ `mock`/`retrieval_only`. Nếu tự bật `--include-mock`, lọc thêm `metadata.mode` trước khi chấm.
- `reference` để `null` vì hệ thống không có ground truth. Khi chấm, dùng các metric không cần reference trước; nếu có bộ câu hỏi chuẩn thì bổ sung `reference` bên ngoài.

## Dọn dữ liệu cũ (retention tối đa 1 năm)

Hệ thống đang demo (Vuser/Phase 3) nên chặn retention tối đa **365 ngày**:

```bash
# Xem trước số dòng sẽ xoá
sudo docker exec saas_fastapi_backend python /app/scripts/prune_chat_history.py --dry-run

# Xoá thật theo CHAT_HISTORY_RETENTION_DAYS (mặc định 365)
sudo docker exec saas_fastapi_backend python /app/scripts/prune_chat_history.py

# Chỉ một tenant
sudo docker exec saas_fastapi_backend \
  python /app/scripts/prune_chat_history.py --tenant-id <tenant-uuid> --dry-run
```

Job xoá theo batch 2000 dòng, dùng PostgreSQL local trên cùng VPS. **Egress gần như bằng 0**:
không gọi API/LLM ra Internet; nếu chạy qua SSH thì chỉ tốn vài KB điều khiển.
Chỉ việc tải file `.jsonl.gz` về máy hoặc backup ra ngoài mới tính vào egress (bằng đúng dung lượng file).

Có thể đặt cron chạy hằng tháng sau khi đã export tháng cũ; ví dụ trên VPS:

```bash
# 03:00 ngày 1 mỗi tháng, ghi log local
0 3 1 * * docker exec saas_fastapi_backend python /app/scripts/prune_chat_history.py >> /var/log/ekp_prune.log 2>&1
```

## Lưu ý

- File export chứa query/answer thật: không commit, không đưa lên GitHub. `.gitignore` đã chặn `ragas_export/`, `ragas_*.jsonl` và `ragas_*.jsonl.gz`.
- Script chạy với quyền DB tin cậy; mặc định set `app.rls_bypass='on'` để export toàn bộ tenant. Dùng `--tenant-id` nếu chỉ muốn một tenant qua RLS context.
- Retention mặc định và tối đa 365 ngày (`CHAT_HISTORY_RETENTION_DAYS`); export tháng cũ trước khi prune.
- Hoàn tác lượt chat (`POST /api/v1/chat/rollback`) xoá hẳn lượt đó khỏi `chat_turns`, nên lượt đã hoàn tác cũng không còn trong dataset RAGAS.
- Nén gzip thường giảm file còn khoảng 15–30% dung lượng JSONL, tiết kiệm egress khi tải về.
- Không chạy script này như một phần của request/API; đây là công cụ vận hành thủ công theo tháng.