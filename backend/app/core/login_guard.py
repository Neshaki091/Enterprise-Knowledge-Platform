"""
MÔ TẢ KIẾN TRÚC:
Bộ chống dò mật khẩu (Brute-force Login Guard) chạy in-memory, không cần Redis.
Giới hạn cửa sổ trượt 5 phút:
- Tối đa 5 lần sai cho mỗi cặp (email + IP).
- Tối đa 10 lần sai cho mỗi email trên mọi IP (chống brute-force phân tán).
- Tối đa 30 lần sai cho mỗi IP.
Bộ nhớ được giới hạn cứng để không vi phạm ngân sách RAM 1.0 GB.
"""

import math
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple


class LoginAttemptGuard:
    """Theo dõi số lần đăng nhập thất bại và trả về thời gian chờ Retry-After."""

    WINDOW_SECONDS: float = 300.0
    MAX_FAILURES_PER_IDENTITY: int = 5
    MAX_FAILURES_PER_EMAIL: int = 10
    MAX_FAILURES_PER_IP: int = 30
    MAX_TRACKED_KEYS: int = 5000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._identity_failures: Dict[str, Deque[float]] = {}
        self._email_failures: Dict[str, Deque[float]] = {}
        self._ip_failures: Dict[str, Deque[float]] = {}

    @staticmethod
    def _identity_key(email: str, client_ip: str) -> str:
        return f"{email.strip().lower()}|{client_ip}"

    @staticmethod
    def _email_key(email: str) -> str:
        return email.strip().lower()

    def _prune(self, bucket: Deque[float], now: float) -> None:
        cutoff = now - self.WINDOW_SECONDS
        while bucket and bucket[0] < cutoff:
            bucket.popleft()

    @staticmethod
    def _retry_after(bucket: Deque[float], now: float, window: float) -> int:
        if not bucket:
            return 0
        return max(1, int(math.ceil(bucket[0] + window - now)))

    def check(self, email: str, client_ip: str) -> Optional[int]:
        """
        Kiểm tra xem cặp email/IP hoặc IP có đang bị khóa tạm thời không.

        Returns:
            Số giây phải chờ (Retry-After) hoặc None nếu được phép thử.
        """
        now = time.monotonic()
        identity = self._identity_key(email, client_ip)
        email_key = self._email_key(email)
        with self._lock:
            identity_bucket = self._identity_failures.get(identity)
            if identity_bucket is not None:
                self._prune(identity_bucket, now)
                if len(identity_bucket) >= self.MAX_FAILURES_PER_IDENTITY:
                    return self._retry_after(identity_bucket, now, self.WINDOW_SECONDS)

            email_bucket = self._email_failures.get(email_key)
            if email_bucket is not None:
                self._prune(email_bucket, now)
                if len(email_bucket) >= self.MAX_FAILURES_PER_EMAIL:
                    return self._retry_after(email_bucket, now, self.WINDOW_SECONDS)

            ip_bucket = self._ip_failures.get(client_ip)
            if ip_bucket is not None:
                self._prune(ip_bucket, now)
                if len(ip_bucket) >= self.MAX_FAILURES_PER_IP:
                    return self._retry_after(ip_bucket, now, self.WINDOW_SECONDS)
        return None

    def record_failure(self, email: str, client_ip: str) -> None:
        """Ghi nhận một lần đăng nhập thất bại (chỉ gọi khi sai thông tin xác thực)."""
        now = time.monotonic()
        identity = self._identity_key(email, client_ip)
        email_key = self._email_key(email)
        with self._lock:
            if len(self._identity_failures) >= self.MAX_TRACKED_KEYS:
                self._identity_failures.clear()
            if len(self._email_failures) >= self.MAX_TRACKED_KEYS:
                self._email_failures.clear()
            if len(self._ip_failures) >= self.MAX_TRACKED_KEYS:
                self._ip_failures.clear()

            identity_bucket = self._identity_failures.setdefault(identity, deque())
            self._prune(identity_bucket, now)
            identity_bucket.append(now)

            email_bucket = self._email_failures.setdefault(email_key, deque())
            self._prune(email_bucket, now)
            email_bucket.append(now)

            ip_bucket = self._ip_failures.setdefault(client_ip, deque())
            self._prune(ip_bucket, now)
            ip_bucket.append(now)

    def reset(self, email: str, client_ip: str) -> None:
        """Xóa bộ đếm của cặp email/IP sau khi đăng nhập thành công."""
        identity = self._identity_key(email, client_ip)
        with self._lock:
            self._identity_failures.pop(identity, None)
            self._email_failures.pop(self._email_key(email), None)

    def snapshot(self, email: str, client_ip: str) -> Tuple[int, int]:
        """Trả về số lần thất bại hiện tại của (email+IP, IP) phục vụ chẩn đoán nội bộ."""
        now = time.monotonic()
        identity = self._identity_key(email, client_ip)
        with self._lock:
            identity_bucket = self._identity_failures.get(identity)
            ip_bucket = self._ip_failures.get(client_ip)
            if identity_bucket is not None:
                self._prune(identity_bucket, now)
            if ip_bucket is not None:
                self._prune(ip_bucket, now)
            return (
                len(identity_bucket) if identity_bucket is not None else 0,
                len(ip_bucket) if ip_bucket is not None else 0,
            )


login_guard = LoginAttemptGuard()
