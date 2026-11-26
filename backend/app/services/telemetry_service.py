"""
MÔ TẢ KIẾN TRÚC:
Telemetry & Audit Logging Service cho Analytics Dashboard thời gian thực (SEC-026).
Kết hợp:
- In-memory bounded ring buffer (deque maxlen=250, <100KB RAM) cho SSE realtime QPS/latency.
- Bảng PostgreSQL `telemetry_logs` lưu vết dài hạn không chứa nội dung thô tài liệu/câu hỏi.
"""

import uuid
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Deque
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.telemetry import TelemetryLog

logger = logging.getLogger(__name__)


class TelemetryService:
    """Quản lý ghi nhận sự kiện vận hành và tổng hợp chỉ số realtime cho Admin Dashboard."""

    MAX_BUFFER_SIZE: int = 250
    _ring_buffer: Deque[Dict[str, Any]] = deque(maxlen=MAX_BUFFER_SIZE)

    @classmethod
    async def record_event(
        cls,
        db: Optional[AsyncSession],
        *,
        event_type: str,
        tenant_id: Optional[uuid.UUID] = None,
        user_id: Optional[uuid.UUID] = None,
        model_name: Optional[str] = None,
        mode: Optional[str] = None,
        status_code: int = 200,
        latency_ms: float = 0.0,
        input_tokens: int = 0,
        compressed_tokens: int = 0,
        output_tokens: int = 0,
        storage_delta_bytes: int = 0,
        details: Optional[Dict[str, Any]] = None,
        persist: bool = True,
    ) -> None:
        """Ghi nhận sự kiện vào ring buffer realtime và lưu bền vững vào DB."""
        now_utc = datetime.now(timezone.utc)
        safe_details = dict(details or {})
        entry = {
            "id": str(uuid.uuid4()),
            "timestamp_utc": now_utc.isoformat(),
            "epoch_ts": now_utc.timestamp(),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "user_id": str(user_id) if user_id else None,
            "event_type": event_type,
            "model_name": model_name,
            "mode": mode,
            "status_code": int(status_code),
            "latency_ms": round(float(latency_ms), 2),
            "input_tokens": int(input_tokens),
            "compressed_tokens": int(compressed_tokens),
            "output_tokens": int(output_tokens),
            "storage_delta_bytes": int(storage_delta_bytes),
            "details": safe_details,
        }
        cls._ring_buffer.append(entry)

        if persist and db is not None:
            try:
                row = TelemetryLog(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    event_type=event_type,
                    model_name=model_name,
                    mode=mode,
                    status_code=int(status_code),
                    latency_ms=round(float(latency_ms), 2),
                    input_tokens=int(input_tokens),
                    compressed_tokens=int(compressed_tokens),
                    output_tokens=int(output_tokens),
                    storage_delta_bytes=int(storage_delta_bytes),
                    details=safe_details,
                )
                db.add(row)
                await db.flush()
            except SQLAlchemyError as exc:
                logger.warning("Không thể lưu TelemetryLog xuống DB: %s", exc)

    @classmethod
    def get_realtime_metrics(cls, tenant_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
        """Tính toán chỉ số hiệu năng thời gian thực từ ring buffer (độ trễ < 1ms)."""
        now_ts = datetime.now(timezone.utc).timestamp()
        tid_str = str(tenant_id) if tenant_id else None
        events = [
            e for e in list(cls._ring_buffer)
            if (tid_str is None or e.get("tenant_id") == tid_str)
        ]

        last_1m = [e for e in events if (now_ts - e["epoch_ts"]) <= 60.0]
        last_5m = [e for e in events if (now_ts - e["epoch_ts"]) <= 300.0]

        chat_events = [e for e in last_5m if e["event_type"] in ("chat_query", "chat_stream")]
        latencies = sorted(e["latency_ms"] for e in events if e["latency_ms"] > 0)
        avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
        p95_idx = min(len(latencies) - 1, int(len(latencies) * 0.95)) if latencies else 0
        p95_latency = round(latencies[p95_idx], 2) if latencies else 0.0

        savings_vals = [
            float(e["details"]["savings_percent"])
            for e in events
            if isinstance(e.get("details"), dict) and "savings_percent" in e["details"]
        ]
        avg_savings = round(sum(savings_vals) / len(savings_vals), 1) if savings_vals else 0.0

        error_count = sum(1 for e in last_5m if e["status_code"] >= 400)
        rate_limited_count = sum(1 for e in last_5m if e["status_code"] == 429)
        total_recent = len(last_5m)
        error_rate = round((error_count / total_recent) * 100.0, 1) if total_recent > 0 else 0.0

        return {
            "total_events_buffered": len(events),
            "requests_last_1m": len(last_1m),
            "requests_last_5m": total_recent,
            "queries_last_5m": len(chat_events),
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_latency,
            "avg_compression_savings_percent": avg_savings,
            "errors_last_5m": error_count,
            "rate_limited_429_last_5m": rate_limited_count,
            "error_rate_percent": error_rate,
            "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    async def list_logs(
        cls,
        db: AsyncSession,
        tenant_id: Optional[uuid.UUID] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Truy vấn nhật ký sự kiện từ DB (kết hợp sự kiện lỗi gần nhất trong bộ đệm)."""
        stmt = select(TelemetryLog).order_by(TelemetryLog.created_at.desc()).limit(max(1, min(limit, 200)))
        if tenant_id is not None:
            stmt = stmt.where(TelemetryLog.tenant_id == tenant_id)
        if event_type:
            stmt = stmt.where(TelemetryLog.event_type == event_type)

        rows = (await db.execute(stmt)).scalars().all()
        db_items = [
            {
                "id": str(r.id),
                "timestamp_utc": r.created_at.isoformat() if r.created_at else "",
                "tenant_id": str(r.tenant_id) if r.tenant_id else None,
                "user_id": str(r.user_id) if r.user_id else None,
                "event_type": r.event_type,
                "model_name": r.model_name,
                "mode": r.mode,
                "status_code": r.status_code,
                "latency_ms": r.latency_ms,
                "input_tokens": r.input_tokens,
                "compressed_tokens": r.compressed_tokens,
                "output_tokens": r.output_tokens,
                "storage_delta_bytes": r.storage_delta_bytes,
                "details": r.details,
            }
            for r in rows
        ]

        # Bổ sung các sự kiện chưa persist (ví dụ HTTP 429 khi rollback session) từ ring buffer
        tid_str = str(tenant_id) if tenant_id else None
        buffer_only = [
            {
                k: v for k, v in e.items() if k != "epoch_ts"
            }
            for e in reversed(list(cls._ring_buffer))
            if e["status_code"] >= 400
            and (tid_str is None or e.get("tenant_id") == tid_str)
            and (not event_type or e.get("event_type") == event_type)
        ]

        seen_signatures = {
            (item["timestamp_utc"][:19], item["event_type"], item["status_code"], item["tenant_id"])
            for item in db_items
        }
        merged = list(db_items)
        for buf_item in buffer_only:
            sig = (buf_item["timestamp_utc"][:19], buf_item["event_type"], buf_item["status_code"], buf_item["tenant_id"])
            if sig not in seen_signatures:
                merged.append(buf_item)

        merged.sort(key=lambda x: x["timestamp_utc"], reverse=True)
        return merged[:limit]
