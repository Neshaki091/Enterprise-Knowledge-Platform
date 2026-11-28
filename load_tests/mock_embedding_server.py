"""Mock Embedding API cho stress test ingest (không gọi Google, không đốt quota).

Hỗ trợ format Google batchEmbedContents/embedContent và OpenAI-compatible /embeddings.
Vector deterministic theo SHA-256 của text, chuẩn hoá L2.

Chạy:
    python load_tests/mock_embedding_server.py --port 8099 --delay-ms 200

Sau đó trỏ backend:
    EMBEDDING_API_URL=http://127.0.0.1:8099
    GOOGLE_API_KEY=mock-key (dummy, không phải key thật)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from lib import enable_utf8_stdout

DIMENSION = 786
DELAY_MS = 0
FAIL_AFTER = 0
FAIL_STATUS = 500
REQUEST_COUNT = 0
COUNT_LOCK = threading.Lock()


def _vector(text: str, dimension: int) -> list[float]:
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    counter = 0
    while len(values) < dimension:
        block = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        for byte in block:
            values.append((byte / 127.5) - 1.0)
            if len(values) >= dimension:
                break
        counter += 1
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _extract_texts(payload: dict[str, Any], path: str) -> list[str]:
    if "requests" in payload:
        texts: list[str] = []
        for item in payload["requests"]:
            parts = item.get("content", {}).get("parts", [])
            texts.append("".join(part.get("text", "") for part in parts))
        return texts
    if "content" in payload:
        parts = payload["content"].get("parts", [])
        return ["".join(part.get("text", "") for part in parts)]
    if "input" in payload:
        value = payload["input"]
        return [value] if isinstance(value, str) else list(value)
    raise ValueError(f"Không nhận diện được payload embedding tại {path}")


class Handler(BaseHTTPRequestHandler):
    server_version = "EKPMockEmbedding/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        if getattr(self.server, "verbose", False):
            super().log_message(fmt, *args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - HTTP handler API
        if self.path.rstrip("/") in ("/health", "/healthz"):
            self._send_json(200, {"status": "ok", "requests": REQUEST_COUNT})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - HTTP handler API
        global REQUEST_COUNT
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
            texts = _extract_texts(payload, self.path)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return

        with COUNT_LOCK:
            REQUEST_COUNT += 1
            count = REQUEST_COUNT

        if DELAY_MS:
            time.sleep(DELAY_MS / 1000.0)
        if FAIL_AFTER and count > FAIL_AFTER:
            self._send_json(FAIL_STATUS, {"error": "mock embedding failure injected"})
            return

        vectors = [_vector(text, DIMENSION) for text in texts]
        if self.path.endswith(":batchEmbedContents") or "requests" in payload:
            self._send_json(200, {"embeddings": [{"values": vector} for vector in vectors]})
        elif self.path.endswith(":embedContent") or "content" in payload:
            self._send_json(200, {"embedding": {"values": vectors[0]}})
        else:
            self._send_json(
                200,
                {
                    "data": [
                        {"index": index, "embedding": vector}
                        for index, vector in enumerate(vectors)
                    ]
                },
            )


def main() -> None:
    enable_utf8_stdout()
    global DIMENSION, DELAY_MS, FAIL_AFTER, FAIL_STATUS
    parser = argparse.ArgumentParser(description="Mock embedding API cho load test EKP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--dimension", type=int, default=786)
    parser.add_argument("--delay-ms", type=int, default=200)
    parser.add_argument("--fail-after", type=int, default=0, help="0 = không inject lỗi")
    parser.add_argument("--fail-status", type=int, default=500)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    DIMENSION = args.dimension
    DELAY_MS = args.delay_ms
    FAIL_AFTER = args.fail_after
    FAIL_STATUS = args.fail_status

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.verbose = args.verbose  # type: ignore[attr-defined]
    print(
        f"Mock embedding: http://{args.host}:{args.port} "
        f"dim={DIMENSION} delay={DELAY_MS}ms fail_after={FAIL_AFTER}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()