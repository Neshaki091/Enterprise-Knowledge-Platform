"""
MÔ TẢ KIẾN TRÚC:
Prompt Engineering & Anti-Hallucination Grounding cho Gemma 4 26B (SEC-006).
Tối ưu hóa độ dài chỉ thị hệ thống để tiết kiệm hạn mức 16K TPM của Gemma 4 26B,
đồng thời giữ nguyên quy tắc Fail-Closed (bắt buộc trích dẫn, cấm bịa đặt).
"""

from app.services.chunking_service import estimate_tokens


class PromptService:
    """Tạo cấu trúc Prompt nghiêm ngặt nhưng tinh gọn cho mô hình ngôn ngữ lớn."""

    SYSTEM_PROMPT = (
        "Bạn là Trợ lý Tri thức Doanh nghiệp. Trả lời bằng tiếng Việt trang trọng, ngắn gọn.\n"
        "QUY TẮC BẮT BUỘC (FAIL-CLOSED):\n"
        "1. CHỈ dùng thông tin trong thẻ <context>. Cấm dùng kiến thức ngoài hay suy diễn.\n"
        "2. Mọi luận điểm/con số phải trích dẫn: [Nguồn: <Tên tài liệu>, Phân mục: <Tên phân mục>].\n"
        "3. Nếu <context> không đủ thông tin, BẮT BUỘC trả lời đúng câu sau: "
        "'Xin lỗi, tôi không tìm thấy thông tin này trong tài liệu nội bộ của doanh nghiệp.'"
    )

    @classmethod
    def build_rag_prompt(cls, query: str, context: str) -> str:
        """Ghép nối query và context đã nén vào cấu trúc chuẩn tiết kiệm token."""
        return (
            f"<context>\n{context}\n</context>\n\n"
            f"Câu hỏi: {query}\n"
            f"Trả lời ngắn gọn kèm trích dẫn:"
        )

    @classmethod
    def estimate_total_input_tokens(cls, query: str, context: str) -> int:
        """Ước lượng tổng số token đầu vào (System Prompt + Context + Query wrapper)."""
        rag_prompt = cls.build_rag_prompt(query, context)
        return estimate_tokens(cls.SYSTEM_PROMPT) + estimate_tokens(rag_prompt)
