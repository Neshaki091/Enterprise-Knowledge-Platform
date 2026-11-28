"""Kiểm thử bộ lọc reasoning/self-check bị Gemma trả về không kèm cờ thought."""

from app.services.prompt_service import (
    PromptService,
    ReasoningStreamSanitizer,
    SessionMemoryStreamFilter,
)


LEAKED_GEMMA_ANSWER = """• User Question: “huỳnh công luyện là ai”
• Context provided: [Nguồn: CV (8), Phân mục: [Trang 1]] HUỲNH CÔNG LUYỆN sinh viên CNTT.
• Constraints: 1.Use ONLY provided context. 2.Cite every point/number. 4.Language: Formal Vietnamese, concise.
• Name: Huỳnh Công Luyện.
• Experience: Xây dựng hệ thống RAG, Chatbot AI, tích hợp.
• Source: CV (8), Phân mục: [Trang 1].
• Huỳnh Công Luyện là sinh viên Công nghệ Thông tin đã hoàn thành chương trình học.
• Format? Yes.
• Concise? Yes. *Only context used? Yes.
• Citations included? Yes.
• Missing info check? Info is present.

Huỳnh Công Luyện là sinh viên Công nghệ Thông tin đã hoàn thành chương trình học với GPA 3.34/4.0 [Nguồn: CV (8), Phân mục: [Trang 1]].

Huỳnh Công Luyện là sinh viên Công nghệ Thông tin đã hoàn thành chương trình học với GPA 3.34/4.0 [Nguồn: CV (8), Phân mục: [Trang 1]]."""


def test_sanitize_answer_strips_reasoning_and_duplicate_paragraphs():
    cleaned = PromptService.sanitize_answer(LEAKED_GEMMA_ANSWER)

    assert cleaned.startswith("Huỳnh Công Luyện là sinh viên Công nghệ Thông tin")
    assert "User Question" not in cleaned
    assert "Context provided" not in cleaned
    assert "Missing info check" not in cleaned
    assert "Format?" not in cleaned
    assert cleaned.count("Huỳnh Công Luyện là sinh viên Công nghệ Thông tin") == 1
    assert "[Nguồn: CV (8), Phân mục: [Trang 1]]" in cleaned


def test_sanitize_answer_keeps_clean_answer_unchanged():
    clean = "Nhân viên được nghỉ 14 ngày phép [Nguồn: Policy, Phân mục: Nghỉ phép]."
    assert PromptService.sanitize_answer(clean) == clean


def test_stream_sanitizer_removes_leaked_prefix_across_chunks():
    sanitizer = ReasoningStreamSanitizer()
    chunks = [
        LEAKED_GEMMA_ANSWER[i:i + 17]
        for i in range(0, len(LEAKED_GEMMA_ANSWER), 17)
    ]

    pieces = []
    for chunk in chunks:
        pieces.extend(sanitizer.feed(chunk))
    pieces.extend(sanitizer.finish())
    streamed = "".join(pieces)

    assert "User Question" not in streamed
    assert "Missing info check" not in streamed
    assert "Format?" not in streamed
    assert "Huỳnh Công Luyện là sinh viên Công nghệ Thông tin" in streamed


def test_stream_sanitizer_passes_clean_answer():
    sanitizer = ReasoningStreamSanitizer()
    pieces = sanitizer.feed("Nhân viên được nghỉ 14 ngày phép [Nguồn: Policy].")
    pieces.extend(sanitizer.finish())

    assert "".join(pieces) == "Nhân viên được nghỉ 14 ngày phép [Nguồn: Policy]."


SCREENSHOT_ANSWER = (
    "Huỳnh Công Luyện là sinh viên Công nghệ Thông tin đã hoàn thành chương trình học "
    "(GPA 3.34/4.0), có kinh nghiệm thực hành xây dựng hệ thống RAG, Chatbot AI và tích hợp "
    "[Nguồn: CV (8), Phân mục: [Trang 1]]."
)


def test_sanitize_answer_collapses_inline_duplicate_answer():
    """Ảnh thực tế: cùng một câu bị model trả về hai lần, dính liền không có khoảng trắng."""
    cleaned = PromptService.sanitize_answer(SCREENSHOT_ANSWER + SCREENSHOT_ANSWER)
    assert cleaned == SCREENSHOT_ANSWER


def test_sanitize_answer_collapses_duplicate_sentences_with_space():
    cleaned = PromptService.sanitize_answer(f"{SCREENSHOT_ANSWER} {SCREENSHOT_ANSWER}")
    assert cleaned == SCREENSHOT_ANSWER


def test_build_rag_prompt_includes_session_memory():
    prompt = PromptService.build_rag_prompt(
        "Còn điều kiện nào nữa?",
        "Ngữ cảnh tài liệu",
        "Người dùng: Chính sách nghỉ phép?\nTrợ lý: 14 ngày phép.",
    )

    assert "<conversation>" in prompt
    assert "Chính sách nghỉ phép?" in prompt
    assert "<context>" in prompt
    assert "Còn điều kiện nào nữa?" in prompt


def test_build_rag_prompt_includes_llm_memory_and_attachment_block():
    prompt = PromptService.build_rag_prompt(
        "Còn điều kiện nào nữa?",
        "Ngữ cảnh doanh nghiệp",
        session_memory="Người dùng hỏi về nghỉ phép; đã trả lời 14 ngày.",
        attachment_context="[Nguồn: Tài liệu đính kèm: hop_dong.pdf]\nThời hạn hợp đồng 12 tháng.",
    )

    assert "<previous_session_memory>" in prompt
    assert "đã trả lời 14 ngày" in prompt
    assert "<attachments>" in prompt
    assert "hop_dong.pdf" in prompt
    assert "<session_memory>" in prompt  # instruction buộc model trả memory ở cuối


def test_split_answer_and_memory_hides_metadata_block():
    raw = (
        "Nhân viên được 14 ngày phép [Nguồn: Policy, Phân mục: Nghỉ phép].\n"
        "<session_memory>Đã trả lời: 14 ngày phép; người dùng hỏi về nghỉ phép.</session_memory>"
    )
    answer, memory = PromptService.split_answer_and_memory(raw)

    assert "14 ngày phép" in answer
    assert "session_memory" not in answer
    assert "14 ngày phép" in memory
    assert "<session_memory>" not in memory


def test_split_answer_and_memory_handles_unclosed_tag():
    raw = "Câu trả lời ngắn.\n<session_memory>Thực thể: hợp đồng 12 tháng"
    answer, memory = PromptService.split_answer_and_memory(raw)

    assert answer == "Câu trả lời ngắn."
    assert "hợp đồng 12 tháng" in memory


def test_stream_memory_filter_hides_block_split_across_chunks():
    sanitizer = SessionMemoryStreamFilter()
    raw = (
        "Câu trả lời có trích dẫn [Nguồn: Policy].\n"
        "<session_memory>Thực thể: 14 ngày phép; đã trả lời đầy đủ.</session_memory>"
    )
    pieces = []
    for index in range(0, len(raw), 5):
        pieces.extend(sanitizer.feed(raw[index:index + 5]))
    pieces.extend(sanitizer.finish())

    visible = "".join(pieces)
    assert "Câu trả lời có trích dẫn" in visible
    assert "session_memory" not in visible
    assert "Thực thể" not in visible
    assert "14 ngày phép" in sanitizer.memory_text


def test_stream_memory_filter_passes_clean_answer():
    sanitizer = SessionMemoryStreamFilter()
    pieces = sanitizer.feed("Không có memory tag, chỉ có câu trả lời.")
    pieces.extend(sanitizer.finish())

    assert "".join(pieces) == "Không có memory tag, chỉ có câu trả lời."
    assert sanitizer.memory_text == ""


def test_truncate_session_memory_respects_300_token_budget():
    from app.services.chunking_service import estimate_tokens

    raw = "Người dùng hỏi về hợp đồng lao động. " * 200
    memory = PromptService.truncate_session_memory(raw, 300)

    assert estimate_tokens(memory) <= 300
    assert "hợp đồng lao động" in memory


def test_build_fallback_memory_is_extractive_and_query_aware():
    memory = PromptService.build_fallback_memory(
        "Nhân viên được bao nhiêu ngày phép?",
        "Nhân viên được 14 ngày phép mỗi năm. Đơn từ 3 ngày phải nộp trước 7 ngày.",
    )

    assert "Nhân viên được bao nhiêu ngày phép?" in memory
    assert "14 ngày phép" in memory
    assert "session_memory" not in memory