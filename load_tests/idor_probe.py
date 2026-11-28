"""Kiểm tra IDOR/BOLA cơ bản giữa 2 tenant synthetic.

Chỉ dùng GET/POST đọc, không xoá/sửa dữ liệu. Chạy sau seed:
    python load_tests/idor_probe.py
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

from lib import (
    api_url,
    auth_headers,
    enable_utf8_stdout,
    load_config,
    login_user,
)

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _get(client: httpx.Client, url: str, token: str) -> httpx.Response:
    return client.get(url, headers=auth_headers(token))


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description="IDOR/BOLA probe cho EKP")
    parser.add_argument("--config", default=None)
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    base_url = (args.base_url or config["base_url"]).rstrip("/")
    tenant_a = config["tenants"]["a"]
    tenant_b = config["tenants"]["b"]
    if not tenant_b["documents"]:
        raise SystemExit("Tenant B chưa có tài liệu. Chạy seed_test_data.py trước.")

    token_a = login_user(base_url, tenant_a["owner"])["access_token"]
    token_b = login_user(base_url, tenant_b["owner"])["access_token"]
    b_doc = tenant_b["documents"][0]
    b_marker = b_doc["marker"]

    checks: list[dict[str, Any]] = []
    with httpx.Client(timeout=TIMEOUT, trust_env=False) as client:
        doc_response = _get(
            client,
            api_url(base_url, f"/api/v1/documents/{b_doc['id']}"),
            token_a,
        )
        checks.append(
            {
                "name": "A không đọc được tài liệu B",
                "http_status": doc_response.status_code,
                "pass": doc_response.status_code in (403, 404),
            }
        )

        tenant_response = _get(
            client,
            api_url(base_url, f"/api/v1/tenants/{tenant_b['id']}"),
            token_a,
        )
        checks.append(
            {
                "name": "A không đọc được tenant B",
                "http_status": tenant_response.status_code,
                "pass": tenant_response.status_code in (403, 404),
            }
        )

        search_response = client.post(
            api_url(base_url, "/api/v1/search/hybrid"),
            headers={**auth_headers(token_a), "Content-Type": "application/json"},
            json={"query": b_marker, "top_k": 10},
        )
        leaked = False
        if search_response.status_code == 200:
            for item in search_response.json().get("results", []):
                if item.get("document_id") == b_doc["id"] or b_doc["file_name"] in item.get("document_title", ""):
                    leaked = True
                    break
        checks.append(
            {
                "name": "Search của A không trả tài liệu B",
                "http_status": search_response.status_code,
                "pass": search_response.status_code == 200 and not leaked,
                "leaked": leaked,
            }
        )

        # B vẫn đọc được tài liệu của chính mình (positive control).
        own_response = _get(
            client,
            api_url(base_url, f"/api/v1/documents/{b_doc['id']}"),
            token_b,
        )
        checks.append(
            {
                "name": "B vẫn đọc được tài liệu của B",
                "http_status": own_response.status_code,
                "pass": own_response.status_code == 200,
            }
        )

    print(json.dumps(checks, ensure_ascii=False, indent=2))
    failed = [item for item in checks if not item["pass"]]
    if failed:
        raise SystemExit(f"IDOR/BOLA probe FAIL: {len(failed)} mục")
    print("IDOR/BOLA probe PASS")


if __name__ == "__main__":
    main()