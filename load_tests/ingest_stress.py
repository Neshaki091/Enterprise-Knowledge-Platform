"""Stress luồng ingest EKP: concurrency, size ramp, duplicate race và failure.

Chạy với backend staging/local đã seed:
    python load_tests/ingest_stress.py --mode concurrency --levels 1,2,4,8 --duration 30

Không chạy trên production đang có khách hàng. Script chỉ tạo tài liệu synthetic;
không xoá dữ liệu. Dùng mock embedding server để không đốt quota Google.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from lib import (
    api_url,
    auth_headers,
    enable_utf8_stdout,
    load_config,
    login_user,
)

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"


@dataclass
class Fixture:
    path: Path
    name: str
    content_type: str
    marker: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)


def _load_fixtures(config: dict[str, Any]) -> tuple[dict[str, Fixture], list[Fixture]]:
    manifest = config["fixtures"]
    base = ROOT
    files: dict[str, Fixture] = {}
    for key, item in manifest["files"].items():
        path = base / item["path"]
        files[key] = Fixture(
            path=path,
            name=item["name"],
            content_type=item["content_type"],
            marker=item["marker"],
            data=path.read_bytes(),
        )
    ingest: list[Fixture] = []
    for item in manifest["ingest"]:
        path = base / item["path"]
        ingest.append(
            Fixture(
                path=path,
                name=item["name"],
                content_type=item["content_type"],
                marker=item["marker"],
                data=path.read_bytes(),
            )
        )
    return files, ingest


def _unique_payload(fixture: Fixture, label: str) -> bytes:
    return fixture.data + f"\nEKP-UNIQUE-{label}-{uuid.uuid4().hex}\n".encode("utf-8")


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((percentile / 100.0) * len(ordered) - 1))))
    return ordered[index]


def _parse_mem_mib(raw: str) -> Optional[float]:
    match = re.search(r"([\d.]+)\s*(MiB|GiB|KiB)", raw)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "GiB":
        return value * 1024.0
    if unit == "KiB":
        return value / 1024.0
    return value


async def _memory_sampler(
    container: str, stop: asyncio.Event, samples: list[float], interval: float = 5.0
) -> None:
    while not stop.is_set():
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.MemUsage}}",
                container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await process.communicate()
            value = _parse_mem_mib(stdout.decode("utf-8", errors="ignore").strip())
            if value is not None:
                samples.append(value)
        except (FileNotFoundError, OSError) as exc:
            print(f"[warn] Không đọc được docker stats: {exc}")
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue


async def _upload(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    fixture: Fixture,
    label: str,
    *,
    unique: bool = True,
) -> tuple[int, float, str]:
    data = _unique_payload(fixture, label) if unique else fixture.data
    suffix = fixture.path.suffix or ".txt"
    file_name = f"{fixture.path.stem}_{label}_{uuid.uuid4().hex[:8]}{suffix}"
    started = time.perf_counter()
    try:
        response = await client.post(
            api_url(base_url, "/api/v1/documents/upload"),
            headers=auth_headers(token),
            files={"file": (file_name, data, fixture.content_type)},
            data={"title": f"Load test {label}"},
        )
        return response.status_code, (time.perf_counter() - started) * 1000.0, response.text[:200]
    except httpx.HTTPError as exc:
        return 0, (time.perf_counter() - started) * 1000.0, str(exc)[:200]


def _summarize(
    level: str,
    statuses: list[int],
    latencies: list[float],
    mem_samples: list[float],
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for status in statuses:
        key = str(status)
        counts[key] = counts.get(key, 0) + 1
    return {
        "level": level,
        "requests": len(statuses),
        "status_counts": counts,
        "success": counts.get("201", 0),
        "duplicates": counts.get("409", 0),
        "rate_limited": counts.get("429", 0),
        "overloaded": counts.get("503", 0),
        "client_errors": sum(count for code, count in counts.items() if 400 <= int(code) < 500),
        "server_errors": sum(count for code, count in counts.items() if int(code) >= 500 or code == "0"),
        "p50_ms": round(_percentile(latencies, 50), 1),
        "p95_ms": round(_percentile(latencies, 95), 1),
        "max_ms": round(max(latencies), 1) if latencies else 0.0,
        "rss_peak_mib": round(max(mem_samples), 1) if mem_samples else None,
        "rss_samples": len(mem_samples),
    }


async def run_concurrency(
    base_url: str,
    token: str,
    fixtures: list[Fixture],
    levels: list[int],
    duration_s: float,
    container: Optional[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for level in levels:
        statuses: list[int] = []
        latencies: list[float] = []
        mem_samples: list[float] = []
        stop_sampler = asyncio.Event()
        sampler_task = None
        if container:
            sampler_task = asyncio.create_task(
                _memory_sampler(container, stop_sampler, mem_samples)
            )

        await asyncio.sleep(1.5)
        deadline = time.perf_counter() + duration_s

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(max_connections=level + 2),
            trust_env=False,
        ) as client:
            async def worker(worker_id: int) -> None:
                index = worker_id
                while time.perf_counter() < deadline:
                    fixture = fixtures[index % len(fixtures)]
                    status, latency, _body = await _upload(
                        client,
                        base_url,
                        token,
                        fixture,
                        f"c{level}w{worker_id}i{index}",
                    )
                    statuses.append(status)
                    latencies.append(latency)
                    index += max(1, level)
                    await asyncio.sleep(0.5)

            await asyncio.gather(*(worker(worker_id) for worker_id in range(level)))

        if sampler_task:
            stop_sampler.set()
            await sampler_task
        summary = _summarize(f"concurrency={level}", statuses, latencies, mem_samples)
        results.append(summary)
        print(json.dumps(summary, ensure_ascii=False))
        await asyncio.sleep(2.0)
    return results


async def run_sizes(
    base_url: str,
    token: str,
    files: dict[str, Fixture],
    keys: list[str],
    container: Optional[str],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    mem_samples: list[float] = []
    stop_sampler = asyncio.Event()
    sampler_task = None
    if container:
        sampler_task = asyncio.create_task(_memory_sampler(container, stop_sampler, mem_samples))
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0), trust_env=False) as client:
        for key in keys:
            fixture = files[key]
            status, latency, body = await _upload(
                client, base_url, token, fixture, f"size-{key}", unique=True
            )
            summary = _summarize(
                f"size={key} ({fixture.size} bytes)",
                [status],
                [latency],
                mem_samples.copy(),
            )
            summary["last_body"] = body
            results.append(summary)
            print(json.dumps(summary, ensure_ascii=False))
            await asyncio.sleep(1.0)
    if sampler_task:
        stop_sampler.set()
        await sampler_task
    return results


async def run_duplicate(
    base_url: str,
    token: str,
    fixture: Fixture,
    concurrency: int = 10,
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0), trust_env=False) as client:
        tasks = [
            _upload(client, base_url, token, fixture, "duplicate", unique=False)
            for _ in range(concurrency)
        ]
        responses = await asyncio.gather(*tasks)
    statuses = [item[0] for item in responses]
    latencies = [item[1] for item in responses]
    summary = _summarize(f"duplicate concurrency={concurrency}", statuses, latencies, [])
    summary["pass"] = summary["success"] == 1 and summary["duplicates"] == concurrency - 1
    print(json.dumps(summary, ensure_ascii=False))
    return [summary]


async def run_failure(base_url: str, token: str, fixture: Fixture) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0), trust_env=False) as client:
        status, latency, body = await _upload(
            client, base_url, token, fixture, "failure-injected", unique=True
        )
        summary = _summarize("failure injection", [status], [latency], [])
        summary["last_body"] = body
        summary["expected_failure"] = status >= 500 or status in (413, 422)
        print(json.dumps(summary, ensure_ascii=False))
        return [summary]


async def verify(
    base_url: str,
    token_a: str,
    token_b: str,
    fixture: Fixture,
    *,
    expect_found: bool = True,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), trust_env=False) as client:
        list_response = await client.get(
            api_url(base_url, "/api/v1/documents?skip=0&limit=100"),
            headers=auth_headers(token_a),
        )
        count = len(list_response.json()) if list_response.status_code == 200 else None

        search_response = await client.post(
            api_url(base_url, "/api/v1/search/hybrid"),
            headers={**auth_headers(token_a), "Content-Type": "application/json"},
            json={"query": fixture.marker, "top_k": 5},
        )
        found = (
            search_response.status_code == 200
            and search_response.json().get("total_found", 0) > 0
        )
        search_ok = found if expect_found else not found

        cross_response = await client.post(
            api_url(base_url, "/api/v1/search/hybrid"),
            headers={**auth_headers(token_b), "Content-Type": "application/json"},
            json={"query": fixture.marker, "top_k": 5},
        )
        cross_ok = (
            cross_response.status_code == 200
            and cross_response.json().get("total_found", 0) == 0
        )

    return {
        "documents_visible_first_100": count,
        "marker_found_in_tenant_a": found,
        "search_expectation_ok": search_ok,
        "tenant_b_cannot_see_marker": cross_ok,
        "pass": bool(search_ok and cross_ok),
    }


def _write_report(payload: dict[str, Any], output: Optional[str]) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = Path(output) if output else (
        REPORTS_DIR / f"ingest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description="Stress ingest EKP")
    parser.add_argument("--config", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--mode", choices=["concurrency", "size", "duplicate", "failure"], default="concurrency")
    parser.add_argument("--levels", default="1,2,4,8", help="VD: 1,2,4,8,16")
    parser.add_argument("--duration", type=float, default=30.0, help="Giây mỗi mức concurrency")
    parser.add_argument("--size-keys", default="txt_1kb,txt_100kb,txt_1mb,txt_5mb,docx_small,pdf_small")
    parser.add_argument("--duplicate-concurrency", type=int, default=10)
    parser.add_argument("--container", default="saas_fastapi_backend", help="Tên container backend; rỗng để tắt đo RSS")
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    base_url = (args.base_url or config["base_url"]).rstrip("/")
    files, ingest = _load_fixtures(config)
    owner_a = config["tenants"]["a"]["owner"]
    owner_b = config["tenants"]["b"]["owner"]
    token_a = login_user(base_url, owner_a)["access_token"]
    token_b = login_user(base_url, owner_b)["access_token"]

    container = args.container or None
    if args.mode == "concurrency":
        levels = [int(item.strip()) for item in args.levels.split(",") if item.strip()]
        results = asyncio.run(
            run_concurrency(base_url, token_a, ingest, levels, args.duration, container)
        )
    elif args.mode == "size":
        requested = [item.strip() for item in args.size_keys.split(",") if item.strip()]
        keys = [key for key in requested if key in files]
        skipped = [key for key in requested if key not in files]
        if not keys:
            raise SystemExit(f"Không có fixture hợp lệ. Thiếu: {skipped}")
        if skipped:
            print(f"[warn] Bỏ qua fixture chưa sinh: {skipped}")
        results = asyncio.run(run_sizes(base_url, token_a, files, keys, container))
    elif args.mode == "duplicate":
        results = asyncio.run(
            run_duplicate(base_url, token_a, ingest[0], args.duplicate_concurrency)
        )
    else:
        base_fixture = files["txt_1kb"]
        failure_marker = f"EKP-FAILURE-{uuid.uuid4().hex}"
        failure_fixture = Fixture(
            path=base_fixture.path,
            name=base_fixture.name,
            content_type=base_fixture.content_type,
            marker=failure_marker,
            data=base_fixture.data + f"\n{failure_marker}\n".encode("utf-8"),
        )
        results = asyncio.run(run_failure(base_url, token_a, failure_fixture))

    if args.mode == "size":
        verify_fixture = files[keys[0]]
        expect_found = True
    elif args.mode == "failure":
        verify_fixture = failure_fixture
        expect_found = False
    else:
        verify_fixture = ingest[0]
        expect_found = True
    integrity = asyncio.run(
        verify(base_url, token_a, token_b, verify_fixture, expect_found=expect_found)
    )
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "mode": args.mode,
        "levels": results,
        "integrity": integrity,
    }
    report_path = _write_report(payload, args.report)
    print(json.dumps({"integrity": integrity, "report": str(report_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()