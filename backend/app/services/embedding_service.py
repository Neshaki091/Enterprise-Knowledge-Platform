"""
MÔ TẢ KIẾN TRÚC:
Service sinh vector embedding đại diện cho văn bản (ADR-004).
- Khi cấu hình `EMBEDDING_API_URL` (mặc định Google `gemini-embedding-001` hoặc OpenAI-compatible):
  Tự động gom nhóm tối đa `EMBEDDING_BATCH_SIZE` (mặc định 10) chunks/request,
  gắn `outputDimensionality` (786) + `taskType` (`RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY`),
  gọi API kèm Exponential Backoff và chuẩn hóa L2.
- Khi không cấu hình `EMBEDDING_API_URL` (CI/offline unit tests $0 RAM):
  Dùng bộ chiếu feature hashing (từ khóa + character 3-gram) chuẩn hóa L2 xác định.
"""

import re
import asyncio
import hashlib
import logging
from typing import List, Optional
import httpx
import numpy as np
from fastapi import HTTPException, status
from app.core.config import settings
from app.services.quota_service import quota_manager

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Sinh vector nhúng (Embedding) chuẩn hóa L2 cho Child Chunks và Search Query."""

    @classmethod
    def get_dimension(cls) -> int:
        """Đọc số chiều động từ cấu hình hiện hành."""
        return int(settings.EMBEDDING_DIMENSION)

    @staticmethod
    def _l2_normalize(vec: np.ndarray) -> np.ndarray:
        """Chuẩn hóa vector L2 (an toàn khi norm = 0)."""
        norm = np.linalg.norm(vec)
        return (vec / norm) if norm > 0 else vec

    @classmethod
    def _generate_deterministic_vector(cls, text: str, dim: Optional[int] = None) -> np.ndarray:
        """
        Fallback offline siêu nhẹ (0MB RAM):
        Chiếu đặc trưng theo từ khóa (weighted word tokens) và character trigram vào không gian `dim` chiều.
        """
        target_dim = dim or cls.get_dimension()
        vec = np.zeros(target_dim, dtype=np.float32)
        cleaned = re.sub(r"[^\w\s]", " ", text.lower())
        words = [w for w in cleaned.split() if len(w) >= 2]

        for idx, word in enumerate(words):
            digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % target_dim
            sign = 1.0 if (digest[4] % 2 == 0) else -1.0
            weight = 2.0 / (1.0 + 0.05 * idx)
            vec[bucket] += sign * weight

            if len(word) >= 3:
                for i in range(len(word) - 2):
                    tri = word[i : i + 3]
                    tri_digest = hashlib.blake2b(tri.encode("utf-8"), digest_size=5).digest()
                    tri_bucket = int.from_bytes(tri_digest[:4], "big") % target_dim
                    tri_sign = 1.0 if (tri_digest[4] % 2 == 0) else -1.0
                    vec[tri_bucket] += tri_sign * 0.5

        if np.linalg.norm(vec) == 0:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            vec[int.from_bytes(h[:4], "big") % target_dim] = 1.0

        return cls._l2_normalize(vec)

    @classmethod
    def _is_google_provider(cls) -> bool:
        """Xác định có đang dùng Google Gemini Embedding API hay không."""
        url = (settings.EMBEDDING_API_URL or "").lower()
        return settings.EMBEDDING_PROVIDER.lower() == "google" or "generativelanguage.googleapis.com" in url

    @classmethod
    async def _call_single_batch(
        cls,
        batch_texts: List[str],
        task_type: str,
        dim: int,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> np.ndarray:
        """Gọi API Embedding thật cho tối đa 10 chunks/request kèm Exponential Backoff."""
        url = (settings.EMBEDDING_API_URL or "").strip().rstrip("/")
        api_key = settings.effective_embedding_api_key
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Thiếu GOOGLE_API_KEY / EMBEDDING_API_KEY để gọi Embedding API.",
            )

        headers = {"Content-Type": "application/json"}
        is_google = cls._is_google_provider()

        if is_google:
            headers["x-goog-api-key"] = api_key
            if url.endswith(":batchEmbedContents"):
                endpoint = url
            elif "/models/" in url:
                endpoint = f"{url}:batchEmbedContents"
            else:
                endpoint = f"{url}/v1beta/models/{settings.EMBEDDING_MODEL}:batchEmbedContents"
            payload = {
                "requests": [
                    {
                        "model": f"models/{settings.EMBEDDING_MODEL}",
                        "content": {"parts": [{"text": t}]},
                        "taskType": task_type,
                        "outputDimensionality": dim,
                    }
                    for t in batch_texts
                ]
            }
        else:
            headers["Authorization"] = f"Bearer {api_key}"
            endpoint = url if url.endswith("/embeddings") else f"{url}/embeddings"
            payload = {"model": settings.EMBEDDING_MODEL, "input": batch_texts}

        last_exc: Optional[Exception] = None
        for attempt in range(4):
            try:
                async with httpx.AsyncClient(timeout=30.0, transport=transport) as http_client:
                    resp = await http_client.post(endpoint, headers=headers, json=payload)
                    if resp.status_code in (429, 502, 503, 504):
                        quota_manager.record_upstream_call("embedding", status_code=resp.status_code, error_summary=resp.text[:120])
                        if attempt < 3:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                    resp.raise_for_status()
                    quota_manager.record_upstream_call("embedding", status_code=resp.status_code)
                    data = resp.json()

                    if is_google:
                        raw_vecs = [item["values"] for item in data["embeddings"]]
                    else:
                        items = sorted(data["data"], key=lambda x: x.get("index", 0))
                        raw_vecs = [item["embedding"] for item in items]

                    arr = np.array(raw_vecs, dtype=np.float32)
                    norms = np.linalg.norm(arr, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0
                    return arr / norms
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError, KeyError, IndexError) as exc:
                last_exc = exc
                status_val = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                quota_manager.record_upstream_call("embedding", status_code=status_val, error_summary=str(exc))
                if attempt < 3:
                    await asyncio.sleep(0.5 * (2 ** attempt))

        logger.error("Lỗi gọi Embedding API sau 4 lần thử: %s", last_exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Không thể kết nối tới Embedding Inference API.",
        )

    @classmethod
    async def _call_remote_embeddings(
        cls,
        texts: List[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> np.ndarray:
        """Chia danh sách văn bản thành các batch tối đa 10 chunks/request để tối ưu API và tránh timeout."""
        dim = cls.get_dimension()
        batch_size = max(1, min(int(settings.EMBEDDING_BATCH_SIZE), 10))
        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]

        results = []
        for batch in batches:
            batch_vecs = await cls._call_single_batch(
                batch_texts=batch,
                task_type=task_type,
                dim=dim,
                transport=transport,
            )
            results.append(batch_vecs)

        return np.vstack(results).astype(np.float32)

    @classmethod
    async def get_embedding(
        cls,
        text: str,
        task_type: str = "RETRIEVAL_QUERY",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> np.ndarray:
        """Sinh vector cho một truy vấn tìm kiếm hoặc đoạn văn bản đơn lẻ."""
        if settings.EMBEDDING_API_URL:
            arr = await cls._call_remote_embeddings([text], task_type=task_type, transport=transport)
            return arr[0]
        return cls._generate_deterministic_vector(text, cls.get_dimension())

    @classmethod
    async def get_embeddings(
        cls,
        texts: List[str],
        task_type: str = "RETRIEVAL_DOCUMENT",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> np.ndarray:
        """Sinh vector hàng loạt (batching tối đa 10 chunks/call) cho danh sách Child + Parent context."""
        dim = cls.get_dimension()
        if not texts:
            return np.empty((0, dim), dtype=np.float32)

        if settings.EMBEDDING_API_URL:
            return await cls._call_remote_embeddings(texts, task_type=task_type, transport=transport)

        vectors = [cls._generate_deterministic_vector(t, dim) for t in texts]
        return np.vstack(vectors).astype(np.float32)
