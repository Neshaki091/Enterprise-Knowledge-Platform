"""Seed tenant, user synthetic và tài liệu mẫu cho load test.

Chạy trên staging/local, không chạy trên production có khách hàng:
    python load_tests/seed_test_data.py --base-url http://localhost:8000

Script idempotent tương đối: tenant/user đã tồn tại thì đăng nhập lại; tài liệu
trùng tên/hash thì lấy bản ghi cũ. Không xoá dữ liệu.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from generate_fixtures import build_fixtures
from lib import (
    DEFAULT_CONFIG_PATH,
    api_url,
    auth_headers,
    enable_utf8_stdout,
    find_document_by_name,
    login,
    save_config,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_PASSWORD = "LoadTest!2026"
TIMEOUT = httpx.Timeout(90.0, connect=10.0)


def _register_or_login(
    client: httpx.Client,
    base_url: str,
    *,
    email: str,
    password: str,
    full_name: str,
    tenant_name: str,
    tenant_slug: str,
) -> dict[str, Any]:
    response = client.post(
        api_url(base_url, "/api/v1/auth/register"),
        json={
            "email": email,
            "password": password,
            "full_name": full_name,
            "tenant_name": tenant_name,
            "tenant_slug": tenant_slug,
        },
    )
    if response.status_code not in (201, 400, 409):
        raise RuntimeError(f"Register {email} lỗi HTTP {response.status_code}: {response.text[:300]}")
    return login(base_url, email, password, client=client)


def _add_members(
    client: httpx.Client,
    base_url: str,
    owner_token: str,
    members: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    added: list[dict[str, Any]] = []
    for member in members:
        response = client.post(
            api_url(base_url, "/api/v1/tenants/members"),
            headers=auth_headers(owner_token),
            json=member,
        )
        if response.status_code == 201:
            added.append(member)
            continue
        if response.status_code in (400, 409):
            # Thành viên đã tồn tại từ lần seed trước.
            added.append(member)
            continue
        print(
            f"[warn] Không thêm được {member['email']}: "
            f"HTTP {response.status_code} {response.text[:180]}"
        )
    return added


def _upload_seed_document(
    client: httpx.Client,
    base_url: str,
    token: str,
    *,
    file_name: str,
    marker: str,
    title: str,
) -> dict[str, Any]:
    content = (
        f"# Tài liệu kiểm thử tải\n\n"
        f"Mã kiểm thử: {marker}\n\n"
        "Chính sách giả lập: nhân viên được nghỉ phép 14 ngày mỗi năm. "
        "Tài liệu này chỉ dùng cho load test, không phải dữ liệu khách hàng.\n"
    ).encode("utf-8")
    response = client.post(
        api_url(base_url, "/api/v1/documents/upload"),
        headers=auth_headers(token),
        files={"file": (file_name, content, "text/plain")},
        data={"title": title},
    )
    if response.status_code == 201:
        payload = response.json()
        return {
            "id": payload["id"],
            "file_name": payload["file_name"],
            "marker": marker,
        }
    if response.status_code == 409:
        existing = find_document_by_name(base_url, token, file_name, client=client)
        if existing:
            return {
                "id": existing["id"],
                "file_name": existing["file_name"],
                "marker": marker,
            }
    raise RuntimeError(
        f"Upload seed {file_name} lỗi HTTP {response.status_code}: {response.text[:300]}"
    )


def _tenant_config(
    token_payload: dict[str, Any],
    owner: dict[str, Any],
    users: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    name: str,
    slug: str,
) -> dict[str, Any]:
    return {
        "id": token_payload["tenant_id"],
        "name": name,
        "slug": slug,
        "owner": owner,
        "users": users,
        "documents": documents,
    }


def seed(
    base_url: str,
    prefix: str,
    password: str,
    refresh_fixtures: bool,
    with_large: bool,
) -> Path:
    base_url = base_url.rstrip("/")
    prefix = prefix.strip().lower()
    if not prefix:
        raise SystemExit("--prefix không được rỗng")

    manifest_path = ROOT / "fixtures" / "generated" / "manifest.json"
    if refresh_fixtures or not manifest_path.exists():
        manifest = build_fixtures(with_large=with_large)
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    with httpx.Client(timeout=TIMEOUT, trust_env=False) as client:
        owner_a = {
            "email": f"{prefix}.owner.a@example.com",
            "password": password,
            "full_name": "Load Test Owner A",
            "tenant_name": "EKP Load Test A",
            "tenant_slug": f"{prefix}-a",
        }
        token_a = _register_or_login(client, base_url, **owner_a)

        employee_specs: list[dict[str, Any]] = []
        for index in range(1, 10):
            employee_specs.append(
                {
                    "email": f"{prefix}.emp{index:02d}.a@example.com",
                    "password": password,
                    "full_name": f"Load Test Employee {index:02d}",
                    "role": "EMPLOYEE",
                }
            )
        employees = _add_members(client, base_url, token_a["access_token"], employee_specs)

        owner_b = {
            "email": f"{prefix}.owner.b@example.com",
            "password": password,
            "full_name": "Load Test Owner B",
            "tenant_name": "EKP Load Test B",
            "tenant_slug": f"{prefix}-b",
        }
        token_b = _register_or_login(client, base_url, **owner_b)

        docs_a = [
            _upload_seed_document(
                client,
                base_url,
                token_a["access_token"],
                file_name=f"{prefix}_policy_a.txt",
                marker=f"EKP-LOADTEST-A-{prefix}",
                title="Load test policy A",
            ),
            _upload_seed_document(
                client,
                base_url,
                token_a["access_token"],
                file_name=f"{prefix}_handbook_a.txt",
                marker=f"EKP-LOADTEST-A-HANDBOOK-{prefix}",
                title="Load test handbook A",
            ),
        ]
        docs_b = [
            _upload_seed_document(
                client,
                base_url,
                token_b["access_token"],
                file_name=f"{prefix}_secret_b.txt",
                marker=f"EKP-LOADTEST-B-SECRET-{prefix}",
                title="Load test secret B",
            )
        ]

    config = {
        "base_url": base_url,
        "prefix": prefix,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "password_hint": "Mật khẩu synthetic nằm trong file .runtime/config.json (đã gitignore)",
        "tenants": {
            "a": _tenant_config(
                token_a,
                {"email": owner_a["email"], "password": password, "role": "OWNER"},
                [
                    {"email": item["email"], "password": password, "role": "EMPLOYEE"}
                    for item in employees
                ],
                docs_a,
                owner_a["tenant_name"],
                owner_a["tenant_slug"],
            ),
            "b": _tenant_config(
                token_b,
                {"email": owner_b["email"], "password": password, "role": "OWNER"},
                [],
                docs_b,
                owner_b["tenant_name"],
                owner_b["tenant_slug"],
            ),
        },
        "fixtures": manifest,
    }
    config_path = save_config(config, str(DEFAULT_CONFIG_PATH))
    print(f"Đã ghi config: {config_path}")
    print(f"  Tenant A: {token_a['tenant_id']} ({len(employees) + 1} user)")
    print(f"  Tenant B: {token_b['tenant_id']} (1 user)")
    print(f"  Tài liệu A: {[doc['file_name'] for doc in docs_a]}")
    print(f"  Tài liệu B: {[doc['file_name'] for doc in docs_b]}")
    return config_path


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description="Seed dữ liệu synthetic cho load test EKP")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--prefix", default="loadtest", help="Tiền tố email/tenant slug")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Mật khẩu synthetic")
    parser.add_argument("--refresh-fixtures", action="store_true")
    parser.add_argument("--large", action="store_true", help="Sinh thêm fixture 24MB")
    args = parser.parse_args()
    seed(
        base_url=args.base_url,
        prefix=args.prefix,
        password=args.password,
        refresh_fixtures=args.refresh_fixtures,
        with_large=args.large,
    )


if __name__ == "__main__":
    main()