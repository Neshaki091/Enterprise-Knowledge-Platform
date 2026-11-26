"""
MÔ TẢ KIẾN TRÚC:
In-Memory Quota Manager & Token Bucket Rate Limiter (ADR-002).
Kiểm soát hạn mức truy cập của Gemma 4 26B: 30 RPM (Requests Per Minute) và 16K TPM (Tokens Per Minute).
Tích hợp giám sát thời gian thực hạn mức API Key / Model và mức tiêu thụ từng Tenant mà không cần Redis.
"""

import time
import asyncio
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional, Any
from app.core.config import settings


DEFAULT_RPM_LIMIT: int = 30
DEFAULT_TPM_LIMIT: int = 16000


def resolve_tenant_limits(quota_limits: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """
    Đọc hạn mức hiệu lực của Tenant từ cột JSON quota_limits.
    Khóa không hợp lệ hoặc thiếu sẽ rơi về mặc định 30 RPM / 16.000 TPM.
    """
    raw = quota_limits or {}
    rpm_limit = int(raw.get("rpm_limit", DEFAULT_RPM_LIMIT) or DEFAULT_RPM_LIMIT)
    tpm_limit = int(raw.get("tpm_limit", DEFAULT_TPM_LIMIT) or DEFAULT_TPM_LIMIT)
    return {
        "rpm_limit": max(1, min(rpm_limit, 60_000)),
        "tpm_limit": max(100, min(tpm_limit, 100_000_000)),
        "max_users": max(0, int(raw.get("max_users", 10) or 0)),
        "max_documents": max(0, int(raw.get("max_documents", 50) or 0)),
    }


def mask_api_key(key: Optional[str]) -> str:
    """Che giấu API key, chỉ giữ 4 ký tự đầu/cuối phục vụ nhận diện trên Dashboard."""
    if not key or key.startswith("your_"):
        return "not_configured"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


class TokenBucket:
    """Thuật toán Token Bucket tính toán lượng nạp theo thời gian thực."""

    def __init__(self, capacity: float, refill_rate_per_sec: float):
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.tokens = capacity
        self.last_refill = time.monotonic()

    def configure(self, capacity: float, refill_rate_per_sec: float) -> None:
        """Cập nhật hạn mức khi Tenant được nâng/hạ gói mà không làm mất trạng thái đã dùng."""
        self.current_available()
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.tokens = min(self.tokens, capacity)

    def current_available(self) -> float:
        """Tính số token khả dụng hiện tại (bao gồm lượng vừa hồi phục)."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        return self.tokens

    def consume(self, amount: float = 1.0) -> Tuple[bool, float]:
        """
        Tiêu thụ tokens từ bucket.
        Returns:
            Tuple[bool, float]: (is_allowed, wait_seconds_needed)
        """
        self.current_available()

        if self.tokens >= amount:
            self.tokens -= amount
            return True, 0.0
        missing = amount - self.tokens
        wait_time = missing / self.refill_rate if self.refill_rate > 0 else 60.0
        return False, wait_time


class QuotaManager:
    """Quản lý hạn mức toàn hệ thống, theo từng Tenant và giám sát API Key / Model limits."""

    DEFAULT_RPM_CAPACITY = 30.0
    DEFAULT_RPM_RATE = 30.0 / 60.0

    DEFAULT_TPM_CAPACITY = 16000.0
    DEFAULT_TPM_RATE = 16000.0 / 60.0

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._rpm_buckets: Dict[str, TokenBucket] = {}
        self._tpm_buckets: Dict[str, TokenBucket] = {}
        self._tenant_stats: Dict[str, Dict[str, Any]] = {}
        self._upstream_stats: Dict[str, Dict[str, Any]] = {
            "llm": {
                "requests_2xx": 0,
                "errors_429": 0,
                "errors_5xx": 0,
                "last_status_code": None,
                "last_error": None,
                "last_called_at": None,
            },
            "embedding": {
                "requests_2xx": 0,
                "errors_429": 0,
                "errors_5xx": 0,
                "last_status_code": None,
                "last_error": None,
                "last_called_at": None,
            },
        }

    def _get_buckets(
        self,
        tenant_id: str,
        rpm_limit: Optional[int] = None,
        tpm_limit: Optional[int] = None,
    ) -> Tuple[TokenBucket, TokenBucket]:
        """Lấy hoặc khởi tạo token bucket & stats cho Tenant, đồng bộ hạn mức cấu hình."""
        rpm_capacity = float(rpm_limit if rpm_limit is not None else self.DEFAULT_RPM_CAPACITY)
        tpm_capacity = float(tpm_limit if tpm_limit is not None else self.DEFAULT_TPM_CAPACITY)
        rpm_capacity = max(1.0, rpm_capacity)
        tpm_capacity = max(100.0, tpm_capacity)

        if tenant_id not in self._rpm_buckets:
            self._rpm_buckets[tenant_id] = TokenBucket(rpm_capacity, rpm_capacity / 60.0)
        elif self._rpm_buckets[tenant_id].capacity != rpm_capacity:
            self._rpm_buckets[tenant_id].configure(rpm_capacity, rpm_capacity / 60.0)

        if tenant_id not in self._tpm_buckets:
            self._tpm_buckets[tenant_id] = TokenBucket(tpm_capacity, tpm_capacity / 60.0)
        elif self._tpm_buckets[tenant_id].capacity != tpm_capacity:
            self._tpm_buckets[tenant_id].configure(tpm_capacity, tpm_capacity / 60.0)

        if tenant_id not in self._tenant_stats:
            self._tenant_stats[tenant_id] = {
                "total_requests": 0,
                "total_blocked_rpm": 0,
                "total_blocked_tpm": 0,
                "total_tokens_consumed": 0,
                "last_request_at": None,
            }
        return self._rpm_buckets[tenant_id], self._tpm_buckets[tenant_id]

    async def acquire(
        self,
        tenant_id: str,
        estimated_tokens: int = 500,
        rpm_limit: Optional[int] = None,
        tpm_limit: Optional[int] = None,
    ) -> Tuple[bool, Optional[int]]:
        """Xin cấp phát quyền gửi request tới LLM theo hạn mức hiệu lực của Tenant."""
        async with self._lock:
            rpm_bucket, tpm_bucket = self._get_buckets(tenant_id, rpm_limit, tpm_limit)
            stats = self._tenant_stats[tenant_id]
            stats["last_request_at"] = datetime.now(timezone.utc).isoformat()

            rpm_ok, rpm_wait = rpm_bucket.consume(1.0)
            if not rpm_ok:
                stats["total_blocked_rpm"] += 1
                return False, max(1, int(rpm_wait))

            tpm_ok, tpm_wait = tpm_bucket.consume(float(estimated_tokens))
            if not tpm_ok:
                rpm_bucket.tokens = min(rpm_bucket.capacity, rpm_bucket.tokens + 1.0)
                stats["total_blocked_tpm"] += 1
                return False, max(1, int(tpm_wait))

            stats["total_requests"] += 1
            stats["total_tokens_consumed"] += int(estimated_tokens)
            return True, None

    async def reconcile_tokens(
        self,
        tenant_id: str,
        reserved_tokens: int,
        actual_tokens: int,
        rpm_limit: Optional[int] = None,
        tpm_limit: Optional[int] = None,
    ) -> Tuple[bool, Optional[int]]:
        """Đối soát lại số token TPM thực tế sau bước nén ngữ cảnh."""
        delta = actual_tokens - reserved_tokens
        if delta == 0:
            return True, None

        async with self._lock:
            _, tpm_bucket = self._get_buckets(tenant_id, rpm_limit, tpm_limit)
            stats = self._tenant_stats[tenant_id]
            if delta < 0:
                refund = min(stats["total_tokens_consumed"], -delta)
                stats["total_tokens_consumed"] = max(0, stats["total_tokens_consumed"] - int(refund))
                tpm_bucket.tokens = min(tpm_bucket.capacity, tpm_bucket.tokens + float(-delta))
                return True, None

            ok, wait_sec = tpm_bucket.consume(float(delta))
            if not ok:
                stats["total_blocked_tpm"] += 1
                return False, max(1, int(wait_sec))
            stats["total_tokens_consumed"] += int(delta)
            return True, None

    async def charge_tokens(
        self,
        tenant_id: str,
        tokens: int,
        rpm_limit: Optional[int] = None,
        tpm_limit: Optional[int] = None,
    ) -> int:
        """
        Ghi nhận token đầu ra thực tế sau khi LLM sinh xong.
        Token Bucket được phép âm nhẹ để request kế tiếp bị trì hoãn tương ứng (không thể chặn response đã sinh).
        """
        actual = max(0, int(tokens))
        if actual == 0:
            return 0
        async with self._lock:
            _, tpm_bucket = self._get_buckets(tenant_id, rpm_limit, tpm_limit)
            stats = self._tenant_stats[tenant_id]
            tpm_bucket.current_available()
            tpm_bucket.tokens -= float(actual)
            stats["total_tokens_consumed"] += actual
        return actual

    def record_upstream_call(
        self,
        component: str,
        status_code: Optional[int] = None,
        error_summary: Optional[str] = None,
    ) -> None:
        """Ghi nhận kết quả gọi API ngoài (Google Gemma / Embedding) phục vụ Dashboard."""
        target = self._upstream_stats.get(component)
        if target is None:
            return
        target["last_called_at"] = datetime.now(timezone.utc).isoformat()
        target["last_status_code"] = status_code
        if status_code is not None and 200 <= status_code < 300:
            target["requests_2xx"] += 1
            target["last_error"] = None
        else:
            if status_code == 429:
                target["errors_429"] += 1
            elif status_code is None or status_code >= 500:
                target["errors_5xx"] += 1
            target["last_error"] = (error_summary or f"HTTP {status_code}")[:200]

    async def get_tenant_status(
        self,
        tenant_id: str,
        rpm_limit: Optional[int] = None,
        tpm_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Lấy ảnh chụp trạng thái Token Bucket thời gian thực theo hạn mức hiệu lực của Tenant."""
        async with self._lock:
            rpm_bucket, tpm_bucket = self._get_buckets(tenant_id, rpm_limit, tpm_limit)
            rpm_capacity = max(1.0, rpm_bucket.capacity)
            tpm_capacity = max(100.0, tpm_bucket.capacity)
            rpm_rem = round(rpm_bucket.current_available(), 2)
            tpm_rem = round(tpm_bucket.current_available(), 1)
            stats = self._tenant_stats[tenant_id]
            return {
                "rpm_capacity": int(rpm_capacity),
                "rpm_remaining": rpm_rem,
                "rpm_used_percent": round(min(100.0, max(0.0, (1.0 - rpm_rem / rpm_capacity) * 100.0)), 1),
                "tpm_capacity": int(tpm_capacity),
                "tpm_remaining": tpm_rem,
                "tpm_used_percent": round(min(100.0, max(0.0, (1.0 - tpm_rem / tpm_capacity) * 100.0)), 1),
                "total_requests": stats["total_requests"],
                "total_blocked_rpm": stats["total_blocked_rpm"],
                "total_blocked_tpm": stats["total_blocked_tpm"],
                "total_tokens_consumed": stats["total_tokens_consumed"],
                "last_request_at": stats["last_request_at"],
            }

    async def get_all_tenants_status(self) -> Dict[str, Dict[str, Any]]:
        """Lấy trạng thái quota của toàn bộ các Tenant đã có hoạt động."""
        async with self._lock:
            tenant_ids = list(self._rpm_buckets.keys())
        snapshot: Dict[str, Dict[str, Any]] = {}
        for tid in tenant_ids:
            snapshot[tid] = await self.get_tenant_status(tid)
        return snapshot

    def get_model_api_limits_snapshot(self) -> Dict[str, Any]:
        """Trả về thông số giới hạn của API Key & Models đang cấu hình (ẩn secret key)."""
        llm_key = settings.effective_llm_api_key
        embed_key = settings.effective_embedding_api_key
        return {
            "llm": {
                "provider": settings.LLM_PROVIDER,
                "model": settings.LLM_MODEL,
                "endpoint_configured": bool(settings.LLM_API_URL),
                "api_key_configured": bool(llm_key and not llm_key.startswith("your_")),
                "api_key_masked": mask_api_key(llm_key),
                "rate_limits": {
                    "rpm_limit": int(self.DEFAULT_RPM_CAPACITY),
                    "tpm_limit": int(self.DEFAULT_TPM_CAPACITY),
                    "max_context_tokens_default": 550,
                },
                "upstream_telemetry": dict(self._upstream_stats["llm"]),
            },
            "embedding": {
                "provider": settings.EMBEDDING_PROVIDER,
                "model": settings.EMBEDDING_MODEL,
                "endpoint_configured": bool(settings.EMBEDDING_API_URL),
                "api_key_configured": bool(embed_key and not embed_key.startswith("your_")),
                "api_key_masked": mask_api_key(embed_key),
                "limits": {
                    "dimension": int(settings.EMBEDDING_DIMENSION),
                    "batch_size_max": int(settings.EMBEDDING_BATCH_SIZE),
                },
                "upstream_telemetry": dict(self._upstream_stats["embedding"]),
            },
        }


quota_manager = QuotaManager()
