"""
MÔ TẢ KIẾN TRÚC:
Quản lý FAISS Vector Index theo mô hình cách ly đa khách thuê (Tenant Isolation) với cờ mmap (ADR-004).
Mỗi Tenant sở hữu một file index riêng: `backend/storage/indexes/{tenant_id}.index`.
Mở index bằng cờ `faiss.IO_FLAG_MMAP` để hệ điều hành ánh xạ bộ nhớ ảo qua SSD, Heap RAM chiếm < 50MB.
"""

import os
import uuid
import asyncio
import logging
import threading
from typing import List, Tuple, Dict, Any
import numpy as np
import faiss
from app.core.config import settings

logger = logging.getLogger(__name__)


class FaissService:
    """Quản lý các thao tác nạp, lưu trữ và tìm kiếm vector trên FAISS mmap theo từng Tenant."""

    STORAGE_DIR: str = settings.FAISS_INDEX_STORAGE_PATH

    # Khóa theo từng Tenant cho chuỗi read-modify-write index (thread-safe trong to_thread).
    _tenant_locks: Dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    @classmethod
    def _get_tenant_lock(cls, tenant_id: uuid.UUID) -> threading.Lock:
        """Lấy khóa độc quyền của Tenant, bảo đảm không ghi đè index song song."""
        key = str(tenant_id)
        with cls._locks_guard:
            lock = cls._tenant_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                cls._tenant_locks[key] = lock
            return lock

    @classmethod
    def _write_index_atomic(cls, index: faiss.Index, path: str) -> None:
        """Ghi index ra file tạm rồi os.replace để tránh file .index bị hỏng giữa chừng."""
        tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        try:
            faiss.write_index(index, tmp_path)
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    @classmethod
    def get_dimension(cls) -> int:
        """Đọc số chiều vector động từ settings (hỗ trợ thay đổi 786/768/1024 linh hoạt)."""
        return int(settings.EMBEDDING_DIMENSION)

    @classmethod
    def _get_index_path(cls, tenant_id: uuid.UUID) -> str:
        """Lấy đường dẫn file index của Tenant."""
        os.makedirs(cls.STORAGE_DIR, exist_ok=True)
        return os.path.join(cls.STORAGE_DIR, f"{tenant_id}.index")

    @classmethod
    def _create_or_load_index(cls, tenant_id: uuid.UUID) -> faiss.IndexIDMap2:
        """
        Nạp index từ đĩa nếu đã tồn tại, hoặc tạo index mới nếu chưa có.
        Sử dụng IndexFlatIP (Inner Product = Cosine Similarity khi vector đã chuẩn hóa L2).
        Bọc ngoài bằng IndexIDMap2 để gán ID số nguyên (faiss_id) tương ứng với Child Chunk.
        """
        dim = cls.get_dimension()
        path = cls._get_index_path(tenant_id)
        if os.path.exists(path):
            index = faiss.read_index(path)
            if index.d == dim:
                return index
            if index.ntotal == 0:
                return faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
            raise ValueError(
                f"FAISS index của tenant {tenant_id} có số chiều {index.d}, không khớp cấu hình hiện tại {dim}."
            )
        base_index = faiss.IndexFlatIP(dim)
        return faiss.IndexIDMap2(base_index)

    @classmethod
    def _add_vectors_sync(
        cls,
        tenant_id: uuid.UUID,
        vectors: np.ndarray,
        ids: np.ndarray
    ) -> None:
        """Hàm đồng bộ ghi vectors và IDs vào file index (khóa theo Tenant + ghi atomic)."""
        if len(vectors) != len(ids):
            raise ValueError("Số lượng vectors và IDs không khớp nhau")
        with cls._get_tenant_lock(tenant_id):
            index = cls._create_or_load_index(tenant_id)
            # Đảm bảo vector đúng định dạng float32
            if vectors.dtype != np.float32:
                vectors = vectors.astype(np.float32)
            if ids.dtype != np.int64:
                ids = ids.astype(np.int64)

            index.add_with_ids(vectors, ids)
            path = cls._get_index_path(tenant_id)
            cls._write_index_atomic(index, path)

    @classmethod
    async def add_vectors(
        cls,
        tenant_id: uuid.UUID,
        vectors: np.ndarray,
        ids: List[int]
    ) -> None:
        """Thêm các vectors vào index của Tenant (Chạy trong thread pool để không block asyncio)."""
        if len(vectors) == 0:
            return
        np_ids = np.array(ids, dtype=np.int64)
        await asyncio.to_thread(cls._add_vectors_sync, tenant_id, vectors, np_ids)

    @classmethod
    def _search_mmap_sync(
        cls,
        tenant_id: uuid.UUID,
        query_vector: np.ndarray,
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """
        Tìm kiếm vector bằng cờ IO_FLAG_MMAP (Siêu tiết kiệm RAM).
        Hệ điều hành chỉ tải các trang bộ nhớ cần thiết từ đĩa SSD vào OS Page Cache.
        """
        path = cls._get_index_path(tenant_id)
        if not os.path.exists(path):
            return []

        # Đọc index bằng cờ mmap để không nạp file vào Heap RAM của Python
        index = faiss.read_index(path, faiss.IO_FLAG_MMAP)
        if index.ntotal == 0:
            return []

        expected_dim = cls.get_dimension()
        if index.d != expected_dim:
            raise ValueError(
                f"FAISS index của tenant {tenant_id} có số chiều {index.d}, không khớp cấu hình {expected_dim}."
            )

        if query_vector.ndim == 1:
            query_vector = np.expand_dims(query_vector, axis=0)
        if query_vector.dtype != np.float32:
            query_vector = query_vector.astype(np.float32)

        actual_k = min(top_k, index.ntotal)
        scores, indices = index.search(query_vector, actual_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx != -1:  # -1 biểu thị không tìm thấy
                results.append((int(idx), float(score)))
        return results

    @classmethod
    async def search_mmap(
        cls,
        tenant_id: uuid.UUID,
        query_vector: np.ndarray,
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """Tìm kiếm vector bất đồng bộ qua mmap."""
        return await asyncio.to_thread(cls._search_mmap_sync, tenant_id, query_vector, top_k)

    @classmethod
    def _remove_vectors_sync(cls, tenant_id: uuid.UUID, ids: np.ndarray) -> int:
        """Hàm đồng bộ xóa các vectors theo IDs khỏi file index của Tenant."""
        path = cls._get_index_path(tenant_id)
        if not os.path.exists(path) or len(ids) == 0:
            return 0
        with cls._get_tenant_lock(tenant_id):
            if not os.path.exists(path):
                return 0
            index = faiss.read_index(path)
            if index.ntotal == 0:
                return 0
            removed_count = int(index.remove_ids(ids.astype(np.int64)))
            cls._write_index_atomic(index, path)
            return removed_count

    @classmethod
    async def remove_vectors(cls, tenant_id: uuid.UUID, ids: List[int]) -> int:
        """Xóa các vectors mồ côi khi tài liệu bị xóa khỏi hệ thống."""
        if not ids:
            return 0
        np_ids = np.array(ids, dtype=np.int64)
        return await asyncio.to_thread(cls._remove_vectors_sync, tenant_id, np_ids)

    @classmethod
    def delete_index(cls, tenant_id: uuid.UUID) -> None:
        """Xóa file index khi Tenant bị xóa khỏi hệ thống."""
        with cls._get_tenant_lock(tenant_id):
            path = cls._get_index_path(tenant_id)
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError as exc:
                    logger.warning("Không thể xóa FAISS index '%s': %s", path, exc)

    @classmethod
    def get_index_stats_sync(cls, tenant_id: uuid.UUID) -> Dict[str, Any]:
        """Đọc kích thước file .index trên đĩa và tổng số vector qua cờ IO_FLAG_MMAP."""
        path = cls._get_index_path(tenant_id)
        if not os.path.exists(path):
            return {
                "index_exists": False,
                "file_size_bytes": 0,
                "vector_count": 0,
                "dimension": cls.get_dimension(),
            }
        file_size = os.path.getsize(path)
        vector_count = 0
        dimension = cls.get_dimension()
        try:
            index = faiss.read_index(path, faiss.IO_FLAG_MMAP)
            vector_count = int(index.ntotal)
            dimension = int(index.d)
        except (RuntimeError, OSError) as exc:
            logger.warning("Không thể đọc metadata FAISS index '%s': %s", path, exc)
            vector_count = 0
        return {
            "index_exists": True,
            "file_size_bytes": int(file_size),
            "vector_count": vector_count,
            "dimension": dimension,
        }

    @classmethod
    async def get_index_stats(cls, tenant_id: uuid.UUID) -> Dict[str, Any]:
        """Lấy thống kê dung lượng file FAISS index và số lượng vector của một Tenant."""
        return await asyncio.to_thread(cls.get_index_stats_sync, tenant_id)
