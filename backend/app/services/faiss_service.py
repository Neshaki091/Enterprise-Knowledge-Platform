"""
MÔ TẢ KIẾN TRÚC:
Quản lý FAISS Vector Index theo mô hình cách ly đa khách thuê (Tenant Isolation).
Mỗi Tenant sở hữu một file index riêng: `backend/storage/indexes/{tenant_id}.index`.

Chiến lược lưu trữ thích ứng theo quy mô:
- Dưới `FAISS_IVF_MIN_VECTORS`: IndexFlatIP bọc IndexIDMap2 (chính xác tuyệt đối).
- Vượt ngưỡng: tự nâng cấp sang IndexIVFPQ (Inverted File + Product Quantization),
  nén vector từ 786*4 bytes xuống còn `FAISS_PQ_M` bytes/vector, giảm hàng chục lần
  RAM/đĩa và giữ độ trễ tìm kiếm ổn định trên e2-micro.

Mọi thao tác search đều đọc index qua mmap, không nạp toàn bộ vector vào heap:
- `IO_FLAG_MMAP_IFC`: mmap inverted file containers (bắt buộc cho IVF).
- `IO_FLAG_MMAP`: fallback cho index flat cũ / môi trường FAISS cũ.
Index object được cache theo tenant + mtime để không parse lại file trên mỗi query.
"""

import math
import os
import uuid
import asyncio
import logging
import threading
from collections import OrderedDict
from typing import List, Tuple, Dict, Any, Optional

import numpy as np
import faiss

from app.core.config import settings

logger = logging.getLogger(__name__)


class FaissService:
    """Quản lý các thao tác nạp, lưu trữ và tìm kiếm vector trên FAISS theo từng Tenant."""

    STORAGE_DIR: str = settings.FAISS_INDEX_STORAGE_PATH

    # Khóa theo từng Tenant cho chuỗi read-modify-write index (thread-safe trong to_thread).
    _tenant_locks: Dict[str, threading.Lock] = {}
    _locks_guard = threading.Lock()

    # Cache index object đã mmap theo tenant + (mtime_ns, size) để tránh read_index mỗi query.
    _index_cache: "OrderedDict[str, Tuple[int, int, faiss.Index]]" = OrderedDict()
    _index_cache_lock = threading.Lock()
    _INDEX_CACHE_MAX = 16

    # ------------------------------------------------------------------ helpers
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
        """Đọc số chiều vector logic từ cấu hình hiện hành."""
        return int(settings.EMBEDDING_DIMENSION)

    @classmethod
    def _padded_dimension(cls) -> int:
        """Số chiều sau khi pad để chia hết cho số subquantizer M của PQ."""
        m = max(1, int(settings.FAISS_PQ_M))
        base = cls.get_dimension()
        return int(math.ceil(base / m) * m)

    @staticmethod
    def _pad_matrix(vectors: np.ndarray, target_dim: int) -> np.ndarray:
        """Pad thêm 0 ở cuối vector (không đổi cosine similarity) và bảo đảm float32 contiguous."""
        arr = np.ascontiguousarray(vectors, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        if arr.shape[1] == target_dim:
            return arr
        if arr.shape[1] > target_dim:
            raise ValueError(f"Vector có {arr.shape[1]} chiều, vượt quá chiều index {target_dim}.")
        pad_width = target_dim - arr.shape[1]
        return np.ascontiguousarray(np.pad(arr, ((0, 0), (0, pad_width))), dtype=np.float32)

    @staticmethod
    def _is_ivf_index(index: faiss.Index) -> bool:
        """Nhận diện index IVF (trực tiếp hoặc bọc trong IndexIDMap2)."""
        if hasattr(index, "nprobe"):
            return True
        inner = getattr(index, "index", None)
        return inner is not None and hasattr(inner, "nprobe")

    @classmethod
    def _apply_search_params(cls, index: faiss.Index) -> None:
        """Gán nprobe cho index IVF sau khi nạp từ đĩa."""
        target = index if hasattr(index, "nprobe") else getattr(index, "index", None)
        if target is None or not hasattr(target, "nprobe"):
            return
        nlist = int(getattr(target, "nlist", settings.FAISS_IVF_NPROBE))
        target.nprobe = max(1, min(int(settings.FAISS_IVF_NPROBE), max(1, nlist)))

    @classmethod
    def _read_index_mmap(cls, path: str) -> faiss.Index:
        """
        Đọc index bằng mmap. IVF cần IO_FLAG_MMAP_IFC (mmap inverted lists);
        flat cũng đọc được bằng cờ này, fallback IO_FLAG_MMAP cho FAISS cũ.
        """
        ifc_flag = getattr(faiss, "IO_FLAG_MMAP_IFC", None)
        flags = [flag for flag in (ifc_flag, faiss.IO_FLAG_MMAP) if flag is not None]
        last_exc: Optional[Exception] = None
        for flag in flags:
            try:
                return faiss.read_index(path, flag)
            except RuntimeError as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Không thể đọc FAISS index '{path}'")

    @classmethod
    def _get_cached_index(cls, tenant_id: uuid.UUID, path: str) -> Optional[faiss.Index]:
        """Trả về index mmap đã cache nếu file không đổi; nạp mới nếu cần."""
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            return None

        signature = (int(stat.st_mtime_ns), int(stat.st_size))
        key = str(tenant_id)
        with cls._index_cache_lock:
            entry = cls._index_cache.get(key)
            if entry is not None and entry[0] == signature[0] and entry[1] == signature[1]:
                cls._index_cache.move_to_end(key)
                return entry[2]

            index = cls._read_index_mmap(path)
            cls._apply_search_params(index)
            cls._index_cache[key] = (signature[0], signature[1], index)
            cls._index_cache.move_to_end(key)
            while len(cls._index_cache) > cls._INDEX_CACHE_MAX:
                cls._index_cache.popitem(last=False)
            return index

    @classmethod
    def _invalidate_index_cache(cls, tenant_id: uuid.UUID) -> None:
        """Xóa cache sau khi ghi/xóa index để query tiếp theo thấy file mới."""
        with cls._index_cache_lock:
            cls._index_cache.pop(str(tenant_id), None)

    @classmethod
    def _get_index_path(cls, tenant_id: uuid.UUID) -> str:
        """Lấy đường dẫn file index của Tenant."""
        os.makedirs(cls.STORAGE_DIR, exist_ok=True)
        return os.path.join(cls.STORAGE_DIR, f"{tenant_id}.index")

    @classmethod
    def _create_or_load_index(cls, tenant_id: uuid.UUID) -> faiss.Index:
        """Nạp index ghi được (không mmap) hoặc tạo IndexFlatIP mới cho Tenant."""
        dim = cls.get_dimension()
        padded_dim = cls._padded_dimension()
        path = cls._get_index_path(tenant_id)
        if os.path.exists(path):
            index = faiss.read_index(path)
            if index.d in (dim, padded_dim):
                return index
            if index.ntotal == 0:
                return faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
            raise ValueError(
                f"FAISS index của tenant {tenant_id} có số chiều {index.d}, "
                f"không khớp cấu hình hiện tại {dim} (hoặc {padded_dim} sau pad)."
            )
        return faiss.IndexIDMap2(faiss.IndexFlatIP(dim))

    # ------------------------------------------------------------------ IVF-PQ
    @classmethod
    def _build_ivfpq_index(cls, vectors: np.ndarray, ids: np.ndarray) -> faiss.Index:
        """
        Huấn luyện và dựng IndexIVFPQ từ toàn bộ vector hiện có.

        Kích thước huấn luyện được chặn bởi `FAISS_PQ_TRAIN_MAX` để không vượt
        ngân sách RAM 350MB của container backend trên e2-micro.
        """
        padded_dim = cls._padded_dimension()
        padded = cls._pad_matrix(vectors, padded_dim)
        total = len(padded)
        if total == 0:
            return faiss.IndexIDMap2(faiss.IndexFlatIP(cls.get_dimension()))

        m = max(1, int(settings.FAISS_PQ_M))
        if padded_dim % m != 0:
            m = 1

        nlist_cap = max(4, int(settings.FAISS_IVF_NLIST_MAX))
        nlist = max(4, min(nlist_cap, int(2 * math.sqrt(total)), max(4, total // 39)))

        nbits = int(settings.FAISS_PQ_NBITS)
        max_bits_by_data = max(4, min(16, int(math.floor(math.log2(max(16, total))))))
        nbits = min(nbits, max_bits_by_data)

        train_max = max(1000, int(settings.FAISS_PQ_TRAIN_MAX))
        if total > train_max:
            rng = np.random.default_rng(42)
            sample_idx = rng.choice(total, size=train_max, replace=False)
            train_vectors = np.ascontiguousarray(padded[sample_idx])
        else:
            train_vectors = padded

        ivf = faiss.IndexIVFPQ(
            faiss.IndexFlatL2(padded_dim),
            padded_dim,
            nlist,
            m,
            nbits,
        )
        ivf.train(train_vectors)
        cls._apply_search_params(ivf)
        ivf.add_with_ids(padded, ids.astype(np.int64))
        logger.info(
            "FAISS tenant nâng cấp IVF-PQ: %d vectors, dim=%d, M=%d, nbits=%d, nlist=%d, nprobe=%d",
            total, padded_dim, m, nbits, nlist, ivf.nprobe,
        )
        return ivf

    # ------------------------------------------------------------------ write
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
            if vectors.dtype != np.float32:
                vectors = vectors.astype(np.float32)
            if ids.dtype != np.int64:
                ids = ids.astype(np.int64)

            if cls._is_ivf_index(index):
                # Index IVF đã nén; chỉ cần pad vector mới cho khớp chiều index.
                vectors = cls._pad_matrix(vectors, int(index.d))
                index.add_with_ids(vectors, ids)
            else:
                total_after = int(index.ntotal) + len(vectors)
                threshold = max(1, int(settings.FAISS_IVF_MIN_VECTORS))
                if total_after >= threshold:
                    if not hasattr(index, "index") or not hasattr(index, "id_map"):
                        raise ValueError("Index flat hiện tại không phải IndexIDMap2, không thể nâng cấp IVF-PQ.")
                    base = index.index
                    old_vectors = base.reconstruct_n(0, int(base.ntotal))
                    old_ids = faiss.vector_to_array(index.id_map).astype(np.int64)
                    all_vectors = (
                        np.vstack([old_vectors, vectors]) if len(old_vectors) else vectors
                    )
                    all_ids = np.concatenate([old_ids, ids])
                    index = cls._build_ivfpq_index(all_vectors, all_ids)
                else:
                    index.add_with_ids(vectors, ids)

            path = cls._get_index_path(tenant_id)
            cls._write_index_atomic(index, path)
            cls._invalidate_index_cache(tenant_id)

    @classmethod
    async def add_vectors(
        cls,
        tenant_id: uuid.UUID,
        vectors: np.ndarray,
        ids: List[int]
    ) -> None:
        """Thêm các vectors vào index của Tenant (chạy trong thread pool để không block asyncio)."""
        if len(vectors) == 0:
            return
        np_ids = np.array(ids, dtype=np.int64)
        await asyncio.to_thread(cls._add_vectors_sync, tenant_id, vectors, np_ids)

    # ------------------------------------------------------------------ search
    @classmethod
    def _search_mmap_sync(
        cls,
        tenant_id: uuid.UUID,
        query_vector: np.ndarray,
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """Tìm kiếm vector qua index mmap đã cache (IVF-PQ trả L2, đổi về similarity)."""
        path = cls._get_index_path(tenant_id)
        if not os.path.exists(path):
            return []

        index = cls._get_cached_index(tenant_id, path)
        if index is None or index.ntotal == 0:
            return []

        logical_dim = cls.get_dimension()
        padded_dim = cls._padded_dimension()
        if index.d not in (logical_dim, padded_dim):
            raise ValueError(
                f"FAISS index của tenant {tenant_id} có số chiều {index.d}, "
                f"không khớp cấu hình {logical_dim} (hoặc {padded_dim} sau pad)."
            )

        if query_vector.ndim == 1:
            query_vector = np.expand_dims(query_vector, axis=0)
        if query_vector.shape[1] != index.d:
            query_vector = cls._pad_matrix(query_vector, int(index.d))
        if query_vector.dtype != np.float32:
            query_vector = query_vector.astype(np.float32)

        actual_k = min(int(top_k), int(index.ntotal))
        scores, indices = index.search(np.ascontiguousarray(query_vector), actual_k)

        is_ivf = cls._is_ivf_index(index)
        results: List[Tuple[int, float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx == -1:
                continue
            value = float(score)
            if is_ivf:
                # IVF-PQ dùng L2 (càng nhỏ càng gần) -> đổi về similarity giảm dần.
                value = 1.0 / (1.0 + max(0.0, value))
            results.append((int(idx), value))
        results.sort(key=lambda item: item[1], reverse=True)
        return results

    @classmethod
    async def search_mmap(
        cls,
        tenant_id: uuid.UUID,
        query_vector: np.ndarray,
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """Tìm kiếm vector bất đồng bộ qua mmap (index object được cache theo tenant)."""
        return await asyncio.to_thread(cls._search_mmap_sync, tenant_id, query_vector, top_k)

    # ------------------------------------------------------------------ delete
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
            cls._invalidate_index_cache(tenant_id)
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
            cls._invalidate_index_cache(tenant_id)

    # ------------------------------------------------------------------ stats
    @classmethod
    def get_index_stats_sync(cls, tenant_id: uuid.UUID) -> Dict[str, Any]:
        """Đọc kích thước file .index và số vector qua index mmap đã cache."""
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
        try:
            index = cls._get_cached_index(tenant_id, path)
            if index is not None:
                vector_count = int(index.ntotal)
        except (RuntimeError, OSError, ValueError) as exc:
            logger.warning("Không thể đọc metadata FAISS index '%s': %s", path, exc)
        return {
            "index_exists": True,
            "file_size_bytes": int(file_size),
            "vector_count": vector_count,
            "dimension": cls.get_dimension(),
        }

    @classmethod
    async def get_index_stats(cls, tenant_id: uuid.UUID) -> Dict[str, Any]:
        """Lấy thống kê dung lượng file FAISS index và số lượng vector của một Tenant."""
        return await asyncio.to_thread(cls.get_index_stats_sync, tenant_id)
