"""
MÔ TẢ KIẾN TRÚC:
LLM Client & Inference Engine cho Gemma 4 26B A4B (`gemma-4-26b-a4b-it`) / Google Gemini / OpenAI-Compatible APIs.
Hỗ trợ 4 chế độ runtime:
- 'mock': Sinh câu trả lời giả lập có trích dẫn chuẩn dựa trên chunks đã lọc ($0, ~10ms).
- 'retrieval_only': Chỉ trả về context trích xuất mà không gọi mô hình.
- 'real': Gọi HTTP thật tới Cloud Inference API (dùng chung GOOGLE_API_KEY) kèm Exponential Backoff,
          tự động lọc bỏ internal thinking tokens (`thought: True`) của Gemma 4 26B.
- 'hybrid': Tự động chọn 'real' nếu đã cấu hình đủ URL + API Key, ngược lại fallback có kiểm soát sang 'mock'.
"""

import json
import asyncio
import logging
from typing import Tuple, AsyncGenerator, Dict, Any, Optional, List
import httpx
from fastapi import HTTPException, status
from app.core.config import settings
from app.core.http_client import acquire_http_client
from app.services.prompt_service import PromptService
from app.services.quota_service import quota_manager

logger = logging.getLogger(__name__)


class LLMService:
    """Điều phối suy luận và streaming từ mô hình ngôn ngữ lớn."""

    @classmethod
    def _is_real_configured(cls) -> bool:
        """Kiểm tra đã có đủ thông số URL & API Key để gọi LLM thật chưa."""
        return bool(settings.LLM_API_URL and settings.effective_llm_api_key)

    @classmethod
    def _is_google_provider(cls) -> bool:
        """Xác định provider là Google Generative AI (Gemini/Gemma) hay OpenAI-compatible."""
        url = (settings.LLM_API_URL or "").lower()
        return settings.LLM_PROVIDER.lower() == "google" or "generativelanguage.googleapis.com" in url

    @classmethod
    def _extract_google_answer(cls, parts: List[Dict[str, Any]]) -> str:
        """
        Trích xuất nội dung câu trả lời cuối cùng từ mảng `parts` của Google Gemma 4 / Gemini.
        Tự động lọc bỏ các phần suy luận nội bộ (`thought: True`) để chỉ trả câu trả lời sạch cho người dùng.
        """
        final_pieces = [
            p.get("text", "")
            for p in parts
            if p.get("text") and not p.get("thought")
        ]
        if final_pieces:
            return "".join(final_pieces).strip()
        # Fallback hiếm gặp nếu model chỉ trả về 1 part duy nhất
        return "".join(p.get("text", "") for p in parts if p.get("text")).strip()

    @classmethod
    def _build_request_spec(
        cls,
        prompt: str,
        stream: bool = False,
    ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        """Chuẩn hóa URL endpoint, headers và body theo từng nhà cung cấp Cloud LLM."""
        base_url = (settings.LLM_API_URL or "").strip().rstrip("/")
        api_key = settings.effective_llm_api_key or ""

        if cls._is_google_provider():
            action = "streamGenerateContent?alt=sse" if stream else "generateContent"
            if f":{action.split('?')[0]}" in base_url:
                endpoint = base_url
            elif "/models/" in base_url:
                endpoint = f"{base_url}:{action}"
            else:
                endpoint = f"{base_url}/v1beta/models/{settings.LLM_MODEL}:{action}"
            headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
            payload = {
                "systemInstruction": {"parts": [{"text": PromptService.SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2},
            }
            return endpoint, headers, payload

        endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        payload = {
            "model": settings.LLM_MODEL,
            "messages": [
                {"role": "system", "content": PromptService.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "stream": stream,
        }
        return endpoint, headers, payload

    @classmethod
    def _generate_mock_response(cls, query: str, context: str) -> str:
        """Sinh câu trả lời giả lập có trích dẫn chính xác dựa trên ngữ cảnh đã lọc."""
        if not context.strip():
            return "Xin lỗi, tôi không tìm thấy thông tin này trong tài liệu nội bộ của doanh nghiệp."

        first_line = context.split("\n")[0].strip()
        if first_line.startswith("[Nguồn:") and "]" in first_line:
            source_name = first_line[len("[Nguồn:"):first_line.index("]")].strip()
        elif "[Tài liệu: " in first_line:
            source_name = first_line.replace("[Tài liệu: ", "").replace("]", "").strip()
        else:
            source_name = "Tài liệu nội bộ"

        return (
            f"Dựa trên tài liệu quy chuẩn được cung cấp, câu hỏi '{query}' được giải đáp như sau:\n\n"
            f"Nội dung quy định nêu rõ trong hồ sơ: {context[:300].strip()}...\n\n"
            f"📌 Trích dẫn chứng cứ: [Nguồn: {source_name}]."
        )

    @classmethod
    async def generate_answer(
        cls,
        query: str,
        context: str,
        mode: str = "hybrid",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> Tuple[str, int]:
        """Sinh câu trả lời đầy đủ (Non-streaming) theo chế độ yêu cầu."""
        prompt = PromptService.build_rag_prompt(query, context)
        estimated_input_tokens = len(prompt) // 4

        if mode == "retrieval_only":
            return context, estimated_input_tokens

        elif mode == "mock":
            await asyncio.sleep(0.01)
            answer = cls._generate_mock_response(query, context)
            output_tokens = len(answer) // 4
            return answer, estimated_input_tokens + output_tokens

        elif mode == "hybrid":
            if not cls._is_real_configured():
                return await cls.generate_answer(query, context, mode="mock", transport=transport)
            try:
                return await cls.generate_answer(query, context, mode="real", transport=transport)
            except HTTPException as exc:
                if exc.status_code in (502, 503, 504):
                    logger.warning("Cloud LLM tạm thời quá tải (%s), chuyển sang fallback an toàn từ ngữ cảnh đã lọc.", exc.detail)
                    return await cls.generate_answer(query, context, mode="mock", transport=transport)
                raise

        elif mode == "real":
            if not cls._is_real_configured():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Chế độ 'real' chưa khả dụng: Thiếu cấu hình LLM_API_URL hoặc GOOGLE_API_KEY/LLM_API_KEY.",
                )

            endpoint, headers, payload = cls._build_request_spec(prompt, stream=False)
            last_exc: Optional[Exception] = None
            is_google = cls._is_google_provider()

            for attempt in range(4):
                try:
                    async with acquire_http_client(transport=transport) as http_client:
                        resp = await http_client.post(endpoint, headers=headers, json=payload, timeout=45.0)
                        if resp.status_code in (429, 502, 503, 504):
                            quota_manager.record_upstream_call("llm", status_code=resp.status_code, error_summary=resp.text[:120])
                            if attempt < 3:
                                await asyncio.sleep(0.5 * (2 ** attempt))
                                continue
                        resp.raise_for_status()
                        quota_manager.record_upstream_call("llm", status_code=resp.status_code)
                        data = resp.json()

                        if is_google:
                            parts = data["candidates"][0]["content"]["parts"]
                            answer = cls._extract_google_answer(parts)
                        else:
                            answer = data["choices"][0]["message"]["content"]

                        output_tokens = len(answer) // 4
                        return answer, estimated_input_tokens + output_tokens
                except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as exc:
                    last_exc = exc
                    status_val = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    quota_manager.record_upstream_call("llm", status_code=status_val, error_summary=str(exc))
                    if attempt < 3:
                        await asyncio.sleep(0.5 * (2 ** attempt))

            logger.error("Lỗi gọi Cloud LLM Inference API sau 4 lần thử: %s", last_exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Không thể kết nối tới Cloud LLM Inference API (High demand / Timeout).",
            )

        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Chế độ suy luận '{mode}' không hợp lệ. Chỉ hỗ trợ: mock, retrieval_only, real, hybrid.",
            )

    @classmethod
    async def stream_answer(
        cls,
        query: str,
        context: str,
        mode: str = "hybrid",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> AsyncGenerator[str, None]:
        """Async generator truyền từng token từ Cloud LLM API (kèm retry 503/429) hoặc Mock grounded stream."""
        prompt = PromptService.build_rag_prompt(query, context)

        if mode == "retrieval_only":
            return

        requested_hybrid = mode == "hybrid"
        if requested_hybrid:
            mode = "real" if cls._is_real_configured() else "mock"

        if mode == "mock":
            answer = cls._generate_mock_response(query, context)
            for word in answer.split():
                yield word + " "
                await asyncio.sleep(0.01)
            return

        if mode == "real":
            if not cls._is_real_configured():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Chế độ 'real' chưa khả dụng: Thiếu cấu hình LLM_API_URL hoặc GOOGLE_API_KEY/LLM_API_KEY.",
                )

            endpoint, headers, payload = cls._build_request_spec(prompt, stream=True)
            is_google = cls._is_google_provider()
            last_exc: Optional[Exception] = None

            for attempt in range(4):
                try:
                    async with acquire_http_client(transport=transport) as http_client:
                        async with http_client.stream("POST", endpoint, headers=headers, json=payload, timeout=60.0) as resp:
                            if resp.status_code in (429, 502, 503, 504):
                                quota_manager.record_upstream_call("llm", status_code=resp.status_code, error_summary=f"Stream HTTP {resp.status_code}")
                                if attempt < 3:
                                    await asyncio.sleep(0.5 * (2 ** attempt))
                                    continue
                            resp.raise_for_status()
                            quota_manager.record_upstream_call("llm", status_code=resp.status_code)
                            async for raw_line in resp.aiter_lines():
                                line = raw_line.strip()
                                if not line or not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                    if is_google:
                                        parts = chunk["candidates"][0]["content"]["parts"]
                                        text_part = cls._extract_google_answer(parts)
                                    else:
                                        text_part = chunk["choices"][0].get("delta", {}).get("content", "")
                                    if text_part:
                                        yield text_part
                                except (json.JSONDecodeError, KeyError, IndexError):
                                    continue
                            return
                except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as exc:
                    last_exc = exc
                    if attempt < 3:
                        await asyncio.sleep(0.5 * (2 ** attempt))

            # Fallback có kiểm soát: nếu endpoint :streamGenerateContent bị 503 tạm thời,
            # thử gọi :generateContent (non-streaming); nếu là mode 'hybrid' sẽ bảo đảm luôn trả lời.
            logger.warning("Stream endpoint gặp lỗi (%s), thử fallback sang generateContent...", last_exc)
            fallback_mode = "hybrid" if requested_hybrid else "real"
            full_answer, _ = await cls.generate_answer(
                query=query,
                context=context,
                mode=fallback_mode,
                transport=transport,
            )
            for word in full_answer.split(" "):
                if word:
                    yield word + " "
                    await asyncio.sleep(0.01)
            return

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Chế độ suy luận '{mode}' không hợp lệ.",
        )
