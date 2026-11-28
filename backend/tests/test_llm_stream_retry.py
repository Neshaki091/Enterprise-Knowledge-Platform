"""Hồi quy: stream không được phát lại toàn bộ câu trả lời sau khi đã gửi token."""

import json
from typing import AsyncIterator

import httpx
import pytest

from app.services.llm_service import LLMService


class _PartialThenFailStream(httpx.AsyncByteStream):
    """Gửi một dòng SSE hợp lệ rồi ngắt kết nối giữa chừng."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._payload
        raise httpx.ReadError("stream dropped after partial output")


@pytest.mark.asyncio
async def test_stream_does_not_replay_after_partial_output(monkeypatch):
    answer = "Câu trả lời đã được phát một lần và không được lặp lại khi kết nối đứt."
    event = {"candidates": [{"content": {"parts": [{"text": answer}]}}]}
    body = f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=_PartialThenFailStream(body))

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(LLMService, "_is_real_configured", classmethod(lambda cls: True))
    monkeypatch.setattr(LLMService, "_is_google_provider", classmethod(lambda cls: True))
    monkeypatch.setattr(
        LLMService,
        "_build_request_spec",
        classmethod(lambda cls, prompt, stream=False: ("https://fake.test/generateContent", {}, {})),
    )

    pieces = [
        piece
        async for piece in LLMService.stream_answer(
            "Câu hỏi",
            "Ngữ cảnh",
            mode="real",
            transport=transport,
        )
    ]

    output = "".join(pieces)
    assert output.count(answer) == 1
    assert calls == 1