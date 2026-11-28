"""Kiểm thử bộ lọc reasoning/self-check bị Gemma trả về không kèm cờ thought."""

from app.services.prompt_service import PromptService, ReasoningStreamSanitizer


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