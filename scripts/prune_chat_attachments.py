#!/usr/bin/env python3
"""
Dọn tài liệu đính kèm chat đã hết TTL (mặc định 24h) trên PostgreSQL.

- Chỉ xoá các dòng `expires_at < now()`; không đụng file còn hạn.
- Xoá theo batch để không giữ lock lâu trên e2-micro 1 GB RAM.
- Chunk/embedding bị xoá cascade theo FK `chat_attachment_chunks`.
- Job chạy local trên VPS, không gọi API/LLM, egress không đáng kể.

Cách chạy trong container backend:
  sudo docker exec saas_fastapi_backend \\
    python /app/scripts/prune_chat_attachments.py --dry-run

  sudo docker exec saas_fastapi_backend \\
    python /app/scripts/prune_chat_attachments.py --batch-size 500

Có thể thêm vào cron 15 phút/lần; đây là TTL ngắn nên không cần đợi theo ngày.
"""

import argparse
import asyncio
import os
import sys
from typing import Any, List, Optional, Tuple
from urllib.parse import quote

import asyncpg

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _dsn_from_env(explicit: Optional[str]) -> str:
    raw = (explicit or os.getenv("DATABASE_URL") or "").strip()
    if raw:
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


def _where(tenant_id: Optional[str]) -> Tuple[str, List[Any]]:
    conditions = ["expires_at < now()"]
    params: List[Any] = []
    if tenant_id:
        params.append(tenant_id)
        conditions.append(f"tenant_id = ${len(params)}::uuid")
    return " AND ".join(conditions), params


async def prune(
    dsn: str,
    tenant_id: Optional[str],
    batch_size: int,
    dry_run: bool,
) -> Tuple[int, int]:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            if tenant_id:
                await conn.execute("SELECT set_config('app.current_tenant', $1, false)", tenant_id)
            else:
                await conn.execute("SELECT set_config('app.rls_bypass', 'on', false)")

            where_sql, params = _where(tenant_id)
            total = int(
                await conn.fetchval(
                    f"SELECT count(*) FROM chat_attachments WHERE {where_sql}", *params
                )
                or 0
            )
            print(
                f"TTL attachment | {'tenant ' + tenant_id if tenant_id else 'toàn hệ thống'} | "
                f"hết hạn: {total}"
            )
            if dry_run or total == 0:
                return total, 0

            deleted = 0
            while True:
                # FK ON DELETE CASCADE dọn chunk + embedding.
                removed_rows = await conn.fetch(
                    f"""
                    WITH victims AS (
                        SELECT id FROM chat_attachments
                        WHERE {where_sql}
                        LIMIT ${len(params) + 1}
                    )
                    DELETE FROM chat_attachments a
                    USING victims v
                    WHERE a.id = v.id
                    RETURNING a.id
                    """,
                    *params,
                    batch_size,
                )
                removed = len(removed_rows)
                if removed == 0:
                    break
                deleted += removed
                if removed < batch_size:
                    break
            print(f"Đã xoá {deleted} attachment hết hạn.")
            return total, deleted
    finally:
        await pool.close()


async def main_async(args: argparse.Namespace) -> int:
    dsn = _dsn_from_env(args.database_url)
    total, deleted = await prune(
        dsn,
        tenant_id=args.tenant_id,
        batch_size=max(1, min(args.batch_size, 2000)),
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(f"Dry-run: sẽ xoá tối đa {total} attachment ở các lần chạy tiếp theo.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Dọn tài liệu đính kèm chat hết TTL")
    parser.add_argument("--database-url", default=None, help="DSN PostgreSQL (mặc định từ POSTGRES_*)")
    parser.add_argument("--tenant-id", default=None, help="Chỉ dọn một tenant UUID")
    parser.add_argument("--batch-size", type=int, default=500, help="Số dòng mỗi batch (mặc định 500)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ đếm, không xoá")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())