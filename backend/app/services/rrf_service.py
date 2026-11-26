"""
MÔ TẢ KIẾN TRÚC:
Thuật toán Hợp nhất Thứ hạng Tương hỗ (Reciprocal Rank Fusion - RRF).
Kết hợp kết quả từ Dense Vector Search (FAISS mmap) và Sparse Keyword Search (PostgreSQL FTS)
thành một bảng xếp hạng thống nhất mà không cần chuẩn hóa thang điểm (Score Normalization).
Công thức: RRF_Score(d) = sum_{m in M} [ w_m / (k + rank_m(d)) ] với k = 60.
"""

from typing import List, Dict, Any, Tuple
import uuid


class RRFService:
    """Thuật toán Reciprocal Rank Fusion kết hợp nhiều nguồn tìm kiếm."""

    K_CONSTANT: int = 60  # Hằng số chuẩn công nghiệp (Cormack et al.)

    @classmethod
    def fuse(
        cls,
        dense_results: List[Tuple[uuid.UUID, float]],  # List[(chunk_id, score)] theo thứ tự rank 1..N
        sparse_results: List[Tuple[uuid.UUID, float]],  # List[(chunk_id, score)] theo thứ tự rank 1..N
        dense_weight: float = 0.6,
        sparse_weight: float = 0.4,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Hợp nhất 2 danh sách kết quả dựa trên vị trí xếp hạng (Rank).
        
        Args:
            dense_results: Kết quả từ FAISS (đã sắp xếp giảm dần theo Cosine Similarity).
            sparse_results: Kết quả từ PostgreSQL FTS (đã sắp xếp giảm dần theo ts_rank).
            dense_weight: Trọng số của tìm kiếm ngữ nghĩa (mặc định 0.6).
            sparse_weight: Trọng số của tìm kiếm từ khóa chính xác (mặc định 0.4).
            top_k: Số lượng kết quả hợp nhất cần lấy.
            
        Returns:
            Danh sách các ứng viên tốt nhất đã sắp xếp giảm dần theo RRF Score.
        """
        scores: Dict[uuid.UUID, float] = {}
        detail_map: Dict[uuid.UUID, Dict[str, Any]] = {}

        # 1. Chấm điểm từ Dense Results
        for rank, (chunk_id, score) in enumerate(dense_results, start=1):
            rrf_val = dense_weight / (cls.K_CONSTANT + rank)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + rrf_val
            detail_map[chunk_id] = {
                "chunk_id": chunk_id,
                "dense_rank": rank,
                "dense_score": score,
                "sparse_rank": None,
                "sparse_score": None,
            }

        # 2. Chấm điểm từ Sparse Results
        for rank, (chunk_id, score) in enumerate(sparse_results, start=1):
            rrf_val = sparse_weight / (cls.K_CONSTANT + rank)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + rrf_val
            if chunk_id in detail_map:
                detail_map[chunk_id]["sparse_rank"] = rank
                detail_map[chunk_id]["sparse_score"] = score
            else:
                detail_map[chunk_id] = {
                    "chunk_id": chunk_id,
                    "dense_rank": None,
                    "dense_score": None,
                    "sparse_rank": rank,
                    "sparse_score": score,
                }

        # 3. Sắp xếp giảm dần theo RRF Score tổng hợp
        sorted_chunks = sorted(scores.items(), key=lambda item: item[1], reverse=True)

        final_results = []
        for chunk_id, rrf_score in sorted_chunks[:top_k]:
            meta = detail_map[chunk_id]
            meta["rrf_score"] = round(rrf_score, 6)
            final_results.append(meta)

        return final_results
