#!/usr/bin/env python3
"""
Xuất dataset RAGAS theo tháng từ bảng `chat_turns`.

Luôn loại lượt có tài liệu đính kèm chat private (`has_attachment = true`), kể cả
khi dùng --include-mock, để nội dung file riêng của nhân viên không vào dataset chung.

Script này CHỈ đọc dữ liệu và ghi JSONL.GZ; không cài RAGAS, không gọi LLM.
Việc chấm điểm được thực hiện bên ngoài hệ thống (notebook/CLI RAGAS) bằng file xuất ra.

Định dạng mỗi dòng (RAGAS EvaluationDataset):
  {
    "user_input": "...",
    "response": "...",
    "retrieved_contexts": ["...", "..."],
    "reference": null,
    "metadata": {
      "turn_id": "...", "session_id": "...", "tenant_id": "...",
      "chunk_ids": ["..."], "citations": [...],
      "model": "...", "mode": "...", "created_at": "...",
      "llm_context": "...", "retrieved_context_count": 3,
      "latency_ms": 0.0, "ttfb_ms": 0.0,
      "tokens": {...}, "feedback_rating": null
    }
  }

Mặc định loại bỏ lượt mock/retrieval_only và nén gzip để giảm dung lượng tải về.
File vẫn là JSONL sau khi giải nén: `gunzip -c ragas.jsonl.gz | ...`.

Cách chạy trên VPS (image từ 2.4.0 đã chứa sẵn script):
  sudo docker exec saas_fastapi_backend \\
    python /app/scripts/export_ragas_dataset.py --month 2026-11 --output /tmp/ragas_2026-11.jsonl.gz
  sudo docker cp saas_fastapi_backend:/tmp/ragas_2026-11.jsonl.gz ./ragas_2026-11.jsonl.gz

Script dùng POSTGRES_* có sẵn trong container. File xuất chứa nội dung hội thoại:
không commit, không upload công khai.
"""

import argparse
import asyncio
import gzip
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TextIO
from urllib.parse import quote

import asyncpg


# Windows console mặc định có thể là cp1258; ép UTF-8 để --help và log tiếng Việt không lỗi.
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _dsn_from_env(explicit: Optional[str]) -> str:
    """Ưu tiên --database-url/DATABASE_URL; nếu không có thì ghép từ POSTGRES_*."""
    raw = (explicit or os.getenv("DATABASE_URL") or "").strip()
    if raw:
        # asyncpg chỉ hiểu postgresql://, không hiểu driver suffix của SQLAlchemy.
        return raw.replace("postgresql+asyncpg://", "postgresql://", 1)
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "")
    host = os.getenv("POSTGRES_SERVER", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "saas_knowledge_db")
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{quote(db, safe='')}"
    )


def _parse_month(raw: str) -> tuple[datetime, datetime]:
    try:
        start = datetime.strptime(raw, "%Y-%m").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SystemExit(f"Tháng không hợp lệ: {raw!r}. Dùng định dạng YYYY-MM.") from exc
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _as_json(value: Any) -> Any:
    """asyncpg trả JSON/JSONB dưới dạng str; chuẩn hoá về Python object."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _context_texts(contexts: Any) -> List[str]:
    items = _as_json(contexts) or []
    texts: List[str] = []
    for item in items:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("text") or item.get("parent_content") or item.get("child_content") or ""
        else:
            text = ""
        if str(text).strip():
            texts.append(str(text).strip())
    return texts


def _to_record(row: asyncpg.Record) -> Dict[str, Any]:
    contexts = _as_json(row["contexts"]) or []
    citations = _as_json(row["citations"]) or []
    chunk_ids = _as_json(row["chunk_ids"]) or []
    context_texts = _context_texts(contexts)
    return {
        "user_input": row["query"],
        "response": row["answer"],
        "retrieved_contexts": context_texts,
        "reference": None,
        "metadata": {
            "turn_id": row["id"],
            "session_id": row["session_id"],
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "chunk_ids": chunk_ids,
            "citations": citations,
            "model": row["model_name"],
            "mode": row["mode"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "llm_context": row["llm_context"],
            "retrieved_context_count": len(context_texts),
            "latency_ms": row["latency_ms"],
            "ttfb_ms": row["ttfb_ms"],
            "tokens": {
                "input": row["input_tokens"],
                "compressed": row["compressed_tokens"],
                "output": row["output_tokens"],
                "compression_savings_percent": row["compression_savings_percent"],
            },
            "has_attachment": bool(row["has_attachment"]),
            "session_memory": None if row["has_attachment"] else row["session_memory"],
            "feedback_rating": row["feedback_rating"],
        },
    }


def _open_output(path: str, gzip_enabled: bool) -> TextIO:
    """Ghi text UTF-8; mặc định nén gzip stream để giảm dung lượng file."""
    if gzip_enabled:
        return gzip.open(path, "wt", encoding="utf-8", compresslevel=6)
    return open(path, "w", encoding="utf-8")


async def export_month(
    dsn: str,
    start: datetime,
    end: datetime,
    output: str,
    tenant_id: Optional[str],
    limit: Optional[int],
    include_mock: bool,
    gzip_enabled: bool,
) -> tuple[int, str]:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            if tenant_id:
                # Chỉ đọc một tenant: dùng đúng ngữ cảnh RLS thay vì bypass.
                await conn.execute("SELECT set_config('app.current_tenant', $1, false)", tenant_id)
            else:
                # Xuất toàn bộ tenant cho báo cáo tháng; yêu cầu quyền DB tin cậy.
                await conn.execute("SELECT set_config('app.rls_bypass', 'on', false)")

            conditions = [
                "created_at >= $1",
                "created_at < $2",
                "btrim(answer) <> ''",
                # Không bao giờ đưa lượt dùng tài liệu đính kèm private vào dataset chung.
                "has_attachment = false",
            ]
            params: List[Any] = [start, end]
            if tenant_id:
                params.append(tenant_id)
                conditions.append(f"tenant_id = ${len(params)}::uuid")
            if not include_mock:
                # RAGAS phải chấm câu trả lời thật; mock/retrieval_only chỉ dùng để test.
                conditions.append("(mode IS NULL OR mode NOT IN ('mock', 'retrieval_only'))")

            sql = f"""
                SELECT id::text AS id,
                       tenant_id::text AS tenant_id,
                       user_id::text AS user_id,
                       session_id::text AS session_id,
                       query, answer, contexts, citations, chunk_ids, llm_context,
                       model_name, mode, latency_ms, ttfb_ms,
                       input_tokens, compressed_tokens, output_tokens,
                       compression_savings_percent, has_attachment, session_memory,
                       feedback_rating, created_at
                  FROM chat_turns
                 WHERE {' AND '.join(conditions)}
                 ORDER BY created_at, id
            """
            if limit:
                sql += f" LIMIT {int(limit)}"

            rows = await conn.fetch(sql, *params)

        actual_output = output
        if gzip_enabled and not actual_output.endswith(".gz"):
            actual_output += ".gz"

        os.makedirs(os.path.dirname(os.path.abspath(actual_output)) or ".", exist_ok=True)
        with _open_output(actual_output, gzip_enabled) as fh:
            for row in rows:
                fh.write(json.dumps(_to_record(row), ensure_ascii=False) + "\n")
        return len(rows), actual_output
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Xuất dataset RAGAS theo tháng từ chat_turns (JSONL, nén gzip)")
    parser.add_argument("--month", required=True, help="Tháng cần xuất, định dạng YYYY-MM (UTC)")
    parser.add_argument("--output", required=True, help="Đường dẫn file đầu ra; tự thêm .gz khi nén")
    parser.add_argument("--database-url", default=None, help="DSN PostgreSQL; mặc định đọc DATABASE_URL/POSTGRES_*")
    parser.add_argument("--tenant-id", default=None, help="Chỉ xuất một tenant (khuyến nghị khi cần giới hạn)")
    parser.add_argument("--limit", type=int, default=None, help="Giới hạn số lượt để lấy mẫu")
    parser.add_argument(
        "--include-mock",
        action="store_true",
        help="Bao gồm lượt mock/retrieval_only; mặc định loại bỏ để RAGAS chỉ chấm câu trả lời thật.",
    )
    parser.add_argument("--no-gzip", action="store_true", help="Xuất JSONL thô thay vì nén gzip")
    args = parser.parse_args()

    start, end = _parse_month(args.month)
    dsn = _dsn_from_env(args.database_url)
    count, output = asyncio.run(
        export_month(
            dsn,
            start,
            end,
            args.output,
            args.tenant_id,
            args.limit,
            include_mock=args.include_mock,
            gzip_enabled=not args.no_gzip,
        )
    )
    size_kb = os.path.getsize(output) / 1024
    print(f"Đã xuất {count} lượt chat của tháng {args.month} -> {output} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)