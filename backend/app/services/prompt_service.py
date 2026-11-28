"""
MÔ TẢ KIẾN TRÚC:
Prompt Engineering & Anti-Hallucination Grounding cho Gemini 3.5 Flash Lite (SEC-006).
Tối ưu hóa độ dài chỉ thị hệ thống để tiết kiệm hạn mức 16K TPM cấu hình cho LLM,
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
        "1. CHỈ dùng thông tin trong thẻ <context> và <attachments>. Cấm dùng kiến thức ngoài hay suy diễn. "
        "Session memory chỉ để hiểu câu hỏi nối tiếp, KHÔNG phải nguồn chứng cứ.\n"
        "2. Mọi luận điểm/con số phải trích dẫn: [Nguồn: <Tên tài liệu>, Phân mục: <Tên phân mục>].\n"
        "3. Nếu <context>/<attachments> không đủ thông tin, BẮT BUỘC trả lời đúng câu sau: "
        "'Xin lỗi, tôi không tìm thấy thông tin này trong tài liệu nội bộ của doanh nghiệp.'\n"
        "4. Sau câu trả lời, BẮT BUỘC thêm đúng một khối <session_memory>...</session_memory> "
        "chứa tóm tắt ≤ 250 token về thực thể, con số, ràng buộc, mục tiêu người dùng và điều đã trả lời. "
        "Khối này là metadata cho lượt sau, KHÔNG hiển thị cho người dùng, KHÔNG thêm kiến thức ngoài."
    )

    SESSION_MEMORY_OPEN = "<session_memory>"
    SESSION_MEMORY_CLOSE = "</session_memory>"
    _SESSION_MEMORY_RE = re.compile(
        r"<session_memory>(.*?)</session_memory>",
        re.IGNORECASE | re.DOTALL,
    )
    _SESSION_MEMORY_OPEN_RE = re.compile(r"<session_memory>", re.IGNORECASE)
    _SESSION_MEMORY_MAX_BUFFER = 20000

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
    def build_rag_prompt(
        cls,
        query: str,
        context: str,
        history: str = "",
        session_memory: str = "",
        attachment_context: str = "",
    ) -> str:
        """
        Ghép session memory, context đã nén, tài liệu đính kèm và câu hỏi.

        Session memory là bản tóm tắt do chính lượt LLM trước trả về; history là
        fallback cho session cũ chưa có memory. Cả hai đều không phải chứng cứ.
        """
        history = (history or "").strip()
        session_memory = (session_memory or "").strip()
        attachment_context = (attachment_context or "").strip()

        history_block = f"<conversation>\n{history}\n</conversation>\n\n" if history else ""
        memory_block = (
            f"<previous_session_memory>\n{session_memory}\n</previous_session_memory>\n\n"
            if session_memory
            else ""
        )
        attachment_block = (
            f"<attachments>\n{attachment_context}\n</attachments>\n\n"
            if attachment_context
            else ""
        )
        return (
            f"{memory_block}{history_block}{attachment_block}<context>\n{context}\n</context>\n\n"
            f"Câu hỏi: {query}\n"
            f"Trả lời trực tiếp (không lặp lại hướng dẫn), ngắn gọn, kèm trích dẫn.\n"
            f"Sau câu trả lời, thêm đúng một khối <session_memory>...</session_memory> "
            f"tóm tắt ≤ 250 token cho lượt sau:"
        )

    @classmethod
    def estimate_total_input_tokens(
        cls,
        query: str,
        context: str,
        history: str = "",
        session_memory: str = "",
        attachment_context: str = "",
    ) -> int:
        """Ước lượng tổng token đầu vào (System Prompt + memory/history + context + query)."""
        rag_prompt = cls.build_rag_prompt(
            query,
            context,
            history,
            session_memory=session_memory,
            attachment_context=attachment_context,
        )
        return estimate_tokens(cls.SYSTEM_PROMPT) + estimate_tokens(rag_prompt)

    @classmethod
    def truncate_session_memory(cls, text: str, max_tokens: int = 300) -> str:
        """Chuẩn hóa và cắt session memory theo ngân sách token cứng."""
        cleaned = re.sub(r"\s+", " ", (text or "").strip())
        cleaned = re.sub(r"</?session_memory>", "", cleaned, flags=re.IGNORECASE).strip()
        if not cleaned:
            return ""
        if estimate_tokens(cleaned) <= max_tokens:
            return cleaned
        words = cleaned.split()
        kept: List[str] = []
        for word in words:
            candidate = " ".join([*kept, word])
            if estimate_tokens(candidate) > max_tokens:
                break
            kept.append(word)
        clipped = " ".join(kept).rstrip(" ,;:-")
        with_ellipsis = clipped + "..."
        if estimate_tokens(with_ellipsis) <= max_tokens:
            return with_ellipsis
        return clipped

    @classmethod
    def split_answer_and_memory(
        cls,
        text: str,
        max_memory_tokens: int = 300,
    ) -> tuple[str, str]:
        """
        Tách câu trả lời hiển thị khỏi khối <session_memory> (nếu model trả kèm).

        Nếu model mở thẻ nhưng không đóng, coi phần sau thẻ là memory để tránh lộ
        nội dung metadata ra UI.
        """
        raw = text or ""
        memory = ""
        match = cls._SESSION_MEMORY_RE.search(raw)
        if match:
            memory = match.group(1)
            raw = raw[: match.start()] + raw[match.end():]
        else:
            unclosed = cls._SESSION_MEMORY_OPEN_RE.search(raw)
            if unclosed:
                memory = raw[unclosed.end():]
                raw = raw[: unclosed.start()]
        return (
            cls.sanitize_answer(raw),
            cls.truncate_session_memory(memory, max_memory_tokens),
        )

    @classmethod
    def build_fallback_memory(
        cls,
        query: str,
        answer: str,
        max_tokens: int = 300,
    ) -> str:
        """
        Memory dự phòng khi model không trả khối session_memory (mock/retrieval_only
        hoặc model không tuân thủ). Extractive, không gọi thêm LLM.
        """
        cleaned = cls.sanitize_answer(answer)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if s.strip()]
        key_sentences = sentences[:3]
        memory = f"Câu hỏi trước: {query.strip()}\nĐã trả lời: " + " ".join(key_sentences)
        return cls.truncate_session_memory(memory, max_tokens)

    @classmethod
    def sanitize_answer(cls, text: str) -> str:
        """
        Loại bỏ phần model diễn lại prompt/quá trình self-check nhưng không gắn cờ `thought`.

        Model đôi khi trả về một "reasoning block" dạng:
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
        """Xoá đoạn trả lời bị lặp y hệt (model hay lặp bản nháp + bản cuối)."""
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


class SessionMemoryStreamFilter:
    """
    Tách khối <session_memory> khỏi luồng token trước khi gửi UI.

    Model được yêu cầu trả câu trả lời trước, rồi mới đến memory. Filter giữ lại
    tối đa một hậu tố có thể là phần đầu của thẻ mở để bắt thẻ bị chia qua nhiều
    token; khi đã vào memory thì không phát token ra UI nữa.
    """

    OPEN = PromptService.SESSION_MEMORY_OPEN
    CLOSE = PromptService.SESSION_MEMORY_CLOSE
    _MAX_BUFFER = PromptService._SESSION_MEMORY_MAX_BUFFER

    def __init__(self) -> None:
        self._pending = ""
        self._memory = ""
        self._capturing = False
        self._finished = False

    @staticmethod
    def _partial_suffix_length(text: str, tag: str) -> int:
        """Độ dài hậu tố của `text` có thể là phần đầu của `tag` (để chờ token tiếp)."""
        lower_text = text.lower()
        lower_tag = tag.lower()
        for length in range(min(len(lower_tag) - 1, len(lower_text)), 0, -1):
            if lower_text.endswith(lower_tag[:length]):
                return length
        return 0

    def feed(self, piece: str) -> List[str]:
        if not piece or self._finished:
            return []
        self._pending += piece
        emitted: List[str] = []

        if not self._capturing:
            open_idx = self._pending.lower().find(self.OPEN)
            if open_idx < 0:
                keep = self._partial_suffix_length(self._pending, self.OPEN)
                if keep:
                    emitted.append(self._pending[:-keep])
                    self._pending = self._pending[-keep:]
                else:
                    emitted.append(self._pending)
                    self._pending = ""
                return [item for item in emitted if item]

            if open_idx > 0:
                emitted.append(self._pending[:open_idx])
            self._pending = self._pending[open_idx + len(self.OPEN):]
            self._capturing = True

        if self._capturing:
            close_idx = self._pending.lower().find(self.CLOSE)
            if close_idx < 0:
                if len(self._pending) > self._MAX_BUFFER:
                    # Fail-safe: model không đóng thẻ; giữ phần đầu làm memory và dừng.
                    self._memory = self._pending[: self._MAX_BUFFER]
                    self._pending = ""
                    self._finished = True
                return [item for item in emitted if item]
            self._memory = (self._memory + self._pending[:close_idx]).strip()
            self._pending = self._pending[close_idx + len(self.CLOSE):]
            self._finished = True
            return [item for item in emitted if item]

        return [item for item in emitted if item]

    def finish(self) -> List[str]:
        if self._finished:
            return []
        if not self._capturing:
            out = self._pending
            self._pending = ""
            self._finished = True
            return [out] if out else []
        # Thẻ mở nhưng không đóng: coi phần còn lại là memory, không phát ra UI.
        self._memory = (self._memory + self._pending).strip()
        self._pending = ""
        self._finished = True
        return []

    @property
    def memory_text(self) -> str:
        return self._memory.strip()
