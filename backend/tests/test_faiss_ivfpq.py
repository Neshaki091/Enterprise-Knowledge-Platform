"""Kiểm thử FAISS tự nâng cấp IndexFlatIP lên IndexIVFPQ và vẫn search/add/remove đúng."""

import uuid

import faiss
import numpy as np
import pytest

from app.core.config import settings
from app.services.faiss_service import FaissService


def _clear_index_cache() -> None:
    with FaissService._index_cache_lock:
        FaissService._index_cache.clear()


@pytest.mark.asyncio
async def test_faiss_auto_upgrades_to_ivfpq(tmp_path, monkeypatch):
    """Vượt ngưỡng vector phải chuyển sang IVF-PQ, nén dung lượng và giữ kết quả search."""
    monkeypatch.setattr(FaissService, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "EMBEDDING_DIMENSION", 30)
    monkeypatch.setattr(settings, "FAISS_PQ_M", 4)
    monkeypatch.setattr(settings, "FAISS_PQ_NBITS", 4)
    monkeypatch.setattr(settings, "FAISS_IVF_MIN_VECTORS", 100)
    monkeypatch.setattr(settings, "FAISS_IVF_NPROBE", 4)
    _clear_index_cache()

    tenant_id = uuid.uuid4()
    rng = np.random.default_rng(42)
    vectors = rng.random((150, 30)).astype(np.float32)
    faiss.normalize_L2(vectors)

    await FaissService.add_vectors(tenant_id, vectors, list(range(150)))

    path = FaissService._get_index_path(tenant_id)
    index = faiss.read_index(path)
    assert isinstance(index, faiss.IndexIVFPQ)
    assert index.d == 32  # 30 chiều logic được pad lên 32 để chia hết cho M=4
    assert index.ntotal == 150

    results = await FaissService.search_mmap(tenant_id, vectors[42], top_k=20)
    assert 42 in [chunk_id for chunk_id, _score in results]

    more = rng.random((10, 30)).astype(np.float32)
    faiss.normalize_L2(more)
    await FaissService.add_vectors(tenant_id, more, list(range(150, 160)))
    assert faiss.read_index(path).ntotal == 160

    removed = await FaissService.remove_vectors(tenant_id, [0, 1, 2])
    assert removed == 3
    assert faiss.read_index(path).ntotal == 157

    stats = await FaissService.get_index_stats(tenant_id)
    assert stats["vector_count"] == 157
    assert stats["dimension"] == 30
    # IVF-PQ nén mạnh: file phải nhỏ hơn nửa dung lượng flat thô (157 * 30 * 4 bytes).
    assert stats["file_size_bytes"] < (160 * 30 * 4) // 2

    _clear_index_cache()
