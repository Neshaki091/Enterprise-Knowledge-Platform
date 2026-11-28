"""Đo TTFB/stream SSE cho dashboard realtime hoặc chat RAG.

k6 buffer response nên không đo chính xác từng event SSE; script này dùng
httpx.stream để đo event đầu tiên và tổng thời gian.

Ví dụ:
    python load_tests/sse_probe.py --mode dashboard --duration 30
    python load_tests/sse_probe.py --mode chat --duration 30 --query "Chính sách nghỉ phép?"
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from lib import (
    api_url,
    auth_headers,
    enable_utf8_stdout,
    load_config,
    login_user,
)

ROOT = Path(__file__).resolve().parent


def _stream(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None,
    duration: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    first_event_at: float | None = None
    events = 0
    byte_count = 0
    status_code = 0

    with client.stream(method, url, headers=headers, json=json_body) as response:
        status_code = response.status_code
        if response.status_code >= 400:
            body = response.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"HTTP {response.status_code}: {body}")
        for line in response.iter_lines():
            if not line:
                continue
            byte_count += len(line.encode("utf-8")) + 1
            if line.startswith("data:") or line.startswith("event:"):
                events += 1
                if first_event_at is None:
                    first_event_at = time.perf_counter()
            if time.perf_counter() - started >= duration:
                break

    total_ms = (time.perf_counter() - started) * 1000.0
    return {
        "status_code": status_code,
        "first_event_ms": (
            (first_event_at - started) * 1000.0 if first_event_at is not None else None
        ),
        "total_ms": round(total_ms, 2),
        "events": events,
        "bytes": byte_count,
        "duration_limit_s": duration,
    }


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description="Đo SSE TTFB cho EKP")
    parser.add_argument("--config", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--mode", choices=["dashboard", "chat"], default="dashboard")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--interval", type=int, default=2)
    parser.add_argument("--query", default="Chính sách nghỉ phép của nhân viên là gì?")
    parser.add_argument("--chat-mode", default="mock")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    base_url = (args.base_url or config["base_url"]).rstrip("/")
    owner = config["tenants"]["a"]["owner"]
    token = login_user(base_url, owner)["access_token"]
    headers = auth_headers(token)

    if args.mode == "dashboard":
        url = api_url(
            base_url,
            f"/api/v1/dashboards/tenant/realtime?interval_sec={args.interval}",
        )
        method = "GET"
        body = None
    else:
        url = api_url(base_url, "/api/v1/chat/stream")
        method = "POST"
        body = {"query": args.query, "top_k": 3, "mode": args.chat_mode}
        headers = {**headers, "Content-Type": "application/json"}

    client_timeout = httpx.Timeout(10.0, read=None)
    with httpx.Client(timeout=client_timeout, trust_env=False) as client:
        result = _stream(
            client,
            method,
            url,
            headers=headers,
            json_body=body,
            duration=args.duration,
        )

    payload = {
        "mode": args.mode,
        "base_url": base_url,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    output = Path(args.output) if args.output else (
        ROOT / "reports" / f"sse_{args.mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"Đã ghi: {output}")


if __name__ == "__main__":
    main()