#!/usr/bin/env python3
"""
Dọn `chat_turns` cũ hơn thời hạn lưu trữ (mặc định 365 ngày — tối đa 1 năm).

Đây là job bảo trì chạy trên VPS, nói chuyện với PostgreSQL local trong cùng máy:
- Không gọi API/LLM ra Internet, không tạo egress GCP đáng kể (chỉ SSH vài KB nếu chạy từ xa).
- Xoá theo batch để không giữ lock lâu trên e2-micro 1 GB RAM.
- Mặc định giữ tối đa 365 ngày vì hệ thống đang ở giai đoạn demo (Vuser/Phase 3).

Cách chạy trong container backend (image từ 2.4.0 đã chứa sẵn script):
  # Xem trước sẽ xoá bao nhiêu dòng, không xoá gì
  sudo docker exec saas_fastapi_backend \\
    python /app/scripts/prune_chat_history.py --dry-run

  # Xoá thật theo retention mặc định (365 ngày)
  sudo docker exec saas_fastapi_backend \\
    python /app/scripts/prune_chat_history.py

  # Chỉ dọn một tenant
  sudo docker exec saas_fastapi_backend \\
    python /app/scripts/prune_chat_history.py --tenant-id <tenant-uuid>

Script dùng POSTGRES_* có sẵn trong container, không cần truyền mật khẩu trên command line.
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple
from urllib.parse import quote

import asyncpg

DEFAULT_RETENTION_DAYS = 365
MAX_RETENTION_DAYS = 365


for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _dsn_from_env(explicit: Optional[str]) -> str:
    """Ưu tiên --database-url/DATABASE_URL; nếu không có thì ghép từ POSTGRES_*."""
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


def _resolve_retention(cli_value: Optional[int]) -> int:
    if cli_value is not None:
        days = cli_value
    else:
        raw = os.getenv("CHAT_HISTORY_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
        try:
            days = int(raw)
        except ValueError as exc:
            raise SystemExit(f"CHAT_HISTORY_RETENTION_DAYS không hợp lệ: {raw!r}") from exc
    if days < 1:
        raise SystemExit("Retention phải >= 1 ngày.")
    if days > MAX_RETENTION_DAYS:
        raise SystemExit(
            f"Retention tối đa là {MAX_RETENTION_DAYS} ngày (1 năm) ở giai đoạn demo; nhận được {days}."
        )
    return days


def _build_where(cutoff: datetime, tenant_id: Optional[str]) -> Tuple[str, List[Any]]:
    conditions = ["created_at < $1"]
    params: List[Any] = [cutoff]
    if tenant_id:
        params.append(tenant_id)
        conditions.append(f"tenant_id = ${len(params)}::uuid")
    return " AND ".join(conditions), params


async def prune(
    dsn: str,
    cutoff: datetime,
    retention_days: int,
    tenant_id: Optional[str],
    batch_size: int,
    dry_run: bool,
) -> Tuple[int, int]:
    """Trả về (số dòng đủ điều kiện, số dòng đã xoá)."""
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            if tenant_id:
                # Chỉ dọn một tenant: giữ nguyên ngữ cảnh RLS thay vì bypass.
                await conn.execute("SELECT set_config('app.current_tenant', $1, false)", tenant_id)
            else:
                # Dọn toàn hệ thống: job bảo trì tin cậy, dùng bypass có kiểm soát.
                await conn.execute("SELECT set_config('app.rls_bypass', 'on', false)")

            where_sql, params = _build_where(cutoff, tenant_id)
            total = int(
                await conn.fetchval(f"SELECT count(*) FROM chat_turns WHERE {where_sql}", *params)
                or 0
            )
            print(
                f"Retention {retention_days} ngày | cutoff {cutoff.isoformat()} | "
                f"{'tenant ' + tenant_id if tenant_id else 'toàn hệ thống'} | đủ điều kiện: {total}"
            )
            if dry_run or total == 0:
                return total, 0

            deleted = 0
            while True:
                # DELETE theo batch nhỏ để nhường CPU/I/O cho backend đang phục vụ chat.
                limit_placeholder = f"${len(params) + 1}"
                result = await conn.execute(
                    f"""
                    WITH doomed AS (
                        SELECT id
                          FROM chat_turns
                         WHERE {where_sql}
                         ORDER BY created_at, id
                         LIMIT {limit_placeholder}
                    )
                    DELETE FROM chat_turns t
                     USING doomed d
                     WHERE t.id = d.id
                    """,
                    *params,
                    batch_size,
                )
                batch_deleted = int(result.split()[-1]) if result else 0
                deleted += batch_deleted
                if batch_deleted:
                    print(f"  đã xoá {deleted}/{total}...")
                if batch_deleted < batch_size:
                    break
                await asyncio.sleep(0)
            return total, deleted
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dọn chat_turns cũ hơn retention (tối đa 365 ngày) — job local trên VPS"
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Số ngày giữ lại; mặc định đọc CHAT_HISTORY_RETENTION_DAYS hoặc 365 (tối đa 365)",
    )
    parser.add_argument("--tenant-id", default=None, help="Chỉ dọn dữ liệu của một tenant")
    parser.add_argument("--batch-size", type=int, default=2000, help="Số dòng xoá mỗi batch (mặc định 2000)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ đếm, không xoá")
    parser.add_argument("--database-url", default=None, help="DSN PostgreSQL; mặc định đọc DATABASE_URL/POSTGRES_*")
    args = parser.parse_args()

    retention_days = _resolve_retention(args.retention_days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    dsn = _dsn_from_env(args.database_url)

    total, deleted = asyncio.run(
        prune(
            dsn,
            cutoff,
            retention_days,
            args.tenant_id,
            max(1, min(args.batch_size, 10000)),
            dry_run=args.dry_run,
        )
    )
    if args.dry_run:
        print(f"Dry-run: {total} dòng sẽ bị xoá; chưa thay đổi dữ liệu.")
    else:
        print(f"Hoàn tất: đã xoá {deleted}/{total} dòng chat_turns cũ.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)