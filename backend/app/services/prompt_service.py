"""
MÔ TẢ KIẾN TRÚC:
Prompt Engineering & Anti-Hallucination Grounding cho Gemma 4 26B (SEC-006).
Tối ưu hóa độ dài chỉ thị hệ thống để tiết kiệm hạn mức 16K TPM của Gemma 4 26B,
đồng thời giữ nguyên quy tắc Fail-Closed (bắt buộc trích dẫn, cấm bịa đặt).
"""

import re
from typing import List

from app.services.chunking_service import estimate_tokens


class PromptService:
    """Tạo cấu trúc Prompt nghiêm ngặt nhưng tinh gọn cho mô hình ngôn ngữ lớn."""

    SYSTEM_PROMPT = (
        "Bạn là Trợ lý Tri thức Doanh nghiệp. Trả lời bằng tiếng Việt trang trọng, ngắn gọn.\n"
        "QUY TẮC BẮT BUỘC (FAIL-CLOSED):\n"
        "0. CHỈ xuất câu trả lời cuối cùng. KHÔNG lặp lại câu hỏi, KHÔNG liệt kê context/constraints, "
        "KHÔNG mô tả quá trình suy luận hay tự đánh giá định dạng. KHÔNG lặp lại cùng một câu hoặc cùng một ý.\n"
        "1. CHỈ dùng thông tin trong thẻ <context>. Cấm dùng kiến thức ngoài hay suy diễn.\n"
        "2. Mọi luận điểm/con số phải trích dẫn: [Nguồn: <Tên tài liệu>, Phân mục: <Tên phân mục>].\n"
        "3. Nếu <context> không đủ thông tin, BẮT BUỘC trả lời đúng câu sau: "
        "'Xin lỗi, tôi không tìm thấy thông tin này trong tài liệu nội bộ của doanh nghiệp.'"
    )

    # Các nhãn xuất hiện khi model tự diễn lại prompt/self-check thay vì trả lời.
    _REASONING_START = re.compile(
        r"^\s*(?:[•*\-–]+\s*)*"
        r"(?:user question|context provided|constraints?\b|câu hỏi|ngữ cảnh|ràng buộc)\s*[:?]",
        re.IGNORECASE,
    )
    _SELF_CHECK_LINE = re.compile(
        r"^\s*(?:[•*\-–]+\s*)*"
        r"(?:missing info check|only context used|citations included|format\?|concise\?|"
        r"kiểm tra thiếu thông tin|chỉ dùng ngữ cảnh|trích dẫn\?|định dạng\?|ngắn gọn\?)",
        re.IGNORECASE,
    )
    _FINAL_MARKER = re.compile(
        r"(?im)^\s*(?:final answer|answer|câu trả lời|trả lời)\s*:\s*"
    )

    @classmethod
    def build_rag_prompt(cls, query: str, context: str) -> str:
        """Ghép nối query và context đã nén vào cấu trúc chuẩn tiết kiệm token."""
        return (
            f"<context>\n{context}\n</context>\n\n"
            f"Câu hỏi: {query}\n"
            f"Trả lời trực tiếp (không lặp lại hướng dẫn), ngắn gọn, kèm trích dẫn:"
        )

    @classmethod
    def estimate_total_input_tokens(cls, query: str, context: str) -> int:
        """Ước lượng tổng số token đầu vào (System Prompt + Context + Query wrapper)."""
        rag_prompt = cls.build_rag_prompt(query, context)
        return estimate_tokens(cls.SYSTEM_PROMPT) + estimate_tokens(rag_prompt)

    @classmethod
    def sanitize_answer(cls, text: str) -> str:
        """
        Loại bỏ phần model diễn lại prompt/quá trình self-check nhưng không gắn cờ `thought`.

        Gemma đôi khi trả về một "reasoning block" dạng:
          • User Question: ...
          • Context provided: ...
          • Constraints: ...
          • Format? Yes. ...
        rồi mới đến câu trả lời cuối. Hàm này cắt block đó và giữ câu trả lời.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return cleaned

        # 1. Nếu model có in nhãn "Answer:"/"Final Answer:"/"Trả lời:" thì lấy phần sau nhãn cuối.
        markers = list(cls._FINAL_MARKER.finditer(cleaned))
        if markers:
            tail = cleaned[markers[-1].end():].strip()
            if tail:
                cleaned = tail

        # 2. Block reasoning ở đầu bài + có ít nhất một dòng self-check.
        starts_with_reasoning = bool(cls._REASONING_START.match(cleaned))
        if starts_with_reasoning:
            lines = cleaned.splitlines()
            last_self_check = -1
            for idx, line in enumerate(lines):
                if cls._SELF_CHECK_LINE.match(line):
                    last_self_check = idx
            if last_self_check >= 0:
                cleaned = "\n".join(lines[last_self_check + 1:]).strip()
            else:
                cleaned = cls._drop_leading_reasoning_lines(cleaned)

        cleaned = cls._dedupe_paragraphs(cleaned)
        cleaned = cls._collapse_adjacent_sentences(cleaned)
        return cls._collapse_repeated_answer(cleaned)

    @classmethod
    def _drop_leading_reasoning_lines(cls, text: str) -> str:
        """Dự phòng khi reasoning block không có dòng self-check: bỏ các dòng nhãn/bullet đầu."""
        lines = text.splitlines()
        last_reasoning = -1
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            is_bullet = stripped.startswith(("•", "-", "*", "–"))
            is_label = bool(cls._REASONING_START.match(stripped)) or bool(cls._SELF_CHECK_LINE.match(stripped))
            if is_bullet or is_label:
                last_reasoning = idx
                continue
            break
        if last_reasoning >= 0:
            candidate = "\n".join(lines[last_reasoning + 1:]).strip()
            if candidate:
                return candidate
        return text

    @staticmethod
    def _dedupe_paragraphs(text: str) -> str:
        """Xoá đoạn trả lời bị lặp y hệt (Gemma hay lặp bản nháp + bản cuối)."""
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        seen = set()
        unique: List[str] = []
        for paragraph in paragraphs:
            key = re.sub(r"\s+", " ", paragraph).strip().lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(paragraph)
        return "\n\n".join(unique).strip()

    @staticmethod
    def _collapse_adjacent_sentences(text: str) -> str:
        """Xoá câu liền sau trùng y hệt câu trước, kể cả khi không có khoảng trắng sau dấu chấm."""
        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) < 40:
            return text
        pieces = re.split(r"(?<=[.!?])\s*(?=[A-ZÀ-Ỹ])", compact)
        if len(pieces) < 2:
            return text
        unique: List[str] = []
        previous_key = ""
        for piece in pieces:
            stripped = piece.strip()
            key = re.sub(r"\s+", " ", stripped).lower()
            if key and key == previous_key:
                continue
            unique.append(stripped)
            previous_key = key
        if len(unique) == len(pieces):
            return text
        return " ".join(unique).strip()

    @staticmethod
    def _collapse_repeated_answer(text: str) -> str:
        """Xoá câu trả lời bị lặp nguyên khối 2–3 lần (thường do retry stream)."""
        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) < 80:
            return text
        for parts in (3, 2):
            if len(compact) % parts:
                continue
            unit = compact[: len(compact) // parts]
            if unit and unit * parts == compact:
                return unit
        return text


class ReasoningStreamSanitizer:
    """
    Bộ lọc streaming: giữ buffer ngắn để phát hiện reasoning block ở đầu câu trả lời.

    Không thể "thu hồi" token đã gửi, nên phải nén phần đầu đến khi biết chắc
    model đang trả lời sạch hay đang diễn lại prompt.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._mode = "probe"  # probe -> suppress -> pass

    def feed(self, piece: str) -> List[str]:
        if not piece:
            return []
        if self._mode == "pass":
            return [piece]

        self._buffer += piece
        if self._mode == "probe":
            if not PromptService._REASONING_START.match(self._buffer.lstrip()):
                # Câu trả lời sạch: nhả ngay khi có newline hoặc đủ 48 ký tự
                # (đủ để nhận diện nhãn reasoning ở đầu mà không làm tăng TTFT đáng kể).
                if "\n" in self._buffer or len(self._buffer) >= 48:
                    out = self._buffer
                    self._buffer = ""
                    self._mode = "pass"
                    return [out]
                return []
            self._mode = "suppress"

        # Đang suppress: chờ dòng self-check cuối rồi mới nhả phần còn lại.
        if PromptService._SELF_CHECK_LINE.search(self._buffer):
            lines = self._buffer.splitlines(keepends=True)
            last_self_check = -1
            for idx, line in enumerate(lines):
                if PromptService._SELF_CHECK_LINE.match(line):
                    last_self_check = idx
            if last_self_check >= 0:
                tail = "".join(lines[last_self_check + 1:]).lstrip("\n")
                self._buffer = ""
                self._mode = "pass"
                return [tail] if tail.strip() else []

        if len(self._buffer) > 8000:
            # Fail-safe: không để buffer phình vô hạn nếu model không theo mẫu quen thuộc.
            out = PromptService.sanitize_answer(self._buffer)
            self._buffer = ""
            self._mode = "pass"
            return [out] if out else []
        return []

    def finish(self) -> List[str]:
        if self._mode == "pass":
            return []
        out = PromptService.sanitize_answer(self._buffer)
        self._buffer = ""
        self._mode = "pass"
        return [out] if out else []
