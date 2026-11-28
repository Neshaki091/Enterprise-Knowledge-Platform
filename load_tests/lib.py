"""Helper dùng chung cho các script load test EKP.

Chỉ dùng thư viện chuẩn + httpx (đã có trong requirements của backend).
Không chứa credential thật; config runtime nằm trong load_tests/.runtime/ và bị gitignore.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any, Optional

import httpx

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT / ".runtime" / "config.json"
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def enable_utf8_stdout() -> None:
    """Console Windows cp1258 đôi khi không in được tiếng Việt; chuyển stdout sang UTF-8."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_config(path: Optional[str] = None) -> dict[str, Any]:
    """Đọc config do seed_test_data.py tạo."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise SystemExit(
            f"Không thấy {config_path}. Chạy trước:\n"
            "  python load_tests/seed_test_data.py --base-url http://localhost:8000"
        )
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_config(config: dict[str, Any], path: Optional[str] = None) -> Path:
    """Ghi config runtime với quyền hạn chế (không commit)."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
    return config_path


def api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login(
    base_url: str,
    email: str,
    password: str,
    tenant_id: Optional[str] = None,
    client: Optional[httpx.Client] = None,
) -> dict[str, Any]:
    """Đăng nhập và trả payload token; raise nếu thất bại."""
    payload: dict[str, Any] = {"email": email, "password": password}
    if tenant_id:
        payload["tenant_id"] = tenant_id
    close_client = client is None
    http_client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, trust_env=False)
    try:
        response = http_client.post(api_url(base_url, "/api/v1/auth/login"), json=payload)
        if response.status_code != 200:
            raise RuntimeError(
                f"Login thất bại cho {email}: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json()
    finally:
        if close_client:
            http_client.close()


def login_user(base_url: str, user: dict[str, Any], client: Optional[httpx.Client] = None) -> dict[str, Any]:
    return login(base_url, user["email"], user["password"], user.get("tenant_id"), client)


def pick(items: list[Any]) -> Any:
    return random.choice(items)


def find_document_by_name(
    base_url: str, token: str, file_name: str, client: Optional[httpx.Client] = None
) -> Optional[dict[str, Any]]:
    """Tìm tài liệu theo tên trong tenant hiện tại (dùng cho seed idempotent)."""
    close_client = client is None
    http_client = client or httpx.Client(timeout=DEFAULT_TIMEOUT, trust_env=False)
    try:
        response = http_client.get(
            api_url(base_url, "/api/v1/documents"),
            params={"skip": 0, "limit": 100},
            headers=auth_headers(token),
        )
        if response.status_code != 200:
            return None
        for item in response.json():
            if item.get("file_name") == file_name:
                return item
        return None
    finally:
        if close_client:
            http_client.close()