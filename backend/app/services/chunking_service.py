"""
MÔ TẢ KIẾN TRÚC:
Service phân đoạn văn bản phân cấp (Hierarchical Chunking - ADR-005).
Không nạp mô hình tokenizer nặng vào RAM để bảo vệ ngân sách 1.0 GB RAM của GCP e2-micro.
Sử dụng thuật toán phân cấp dựa trên Heading / Đoạn văn (Parent) và Cửa sổ trượt có gối đầu (Child).
"""

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class RawChildChunk:
    """Cấu trúc dữ liệu tạm của Child Chunk."""
    index: int
    content: str
    token_count: int


@dataclass
class RawParentChunk:
    """Cấu trúc dữ liệu tạm của Parent Chunk chứa danh sách Child Chunks."""
    index: int
    heading: Optional[str]
    content: str
    token_count: int
    children: List[RawChildChunk]


def estimate_tokens(text: str) -> int:
    """
    Ước lượng số token nhanh mà không cần nạp mô hình tokenizer vào RAM.
    Quy chuẩn: ~4 ký tự hoặc ~0.75 từ trên 1 token cho văn bản tiếng Việt/Anh có dấu.
    """
    if not text:
        return 0
    words = len(text.split())
    chars = len(text)
    # Trung bình cộng giữa ước lượng theo từ và theo ký tự
    return max(1, int((words * 1.3 + chars / 4.0) / 2))


def _split_into_sentences(text: str) -> List[str]:
    """Tách văn bản thành các câu dựa trên dấu chấm câu, dấu xuống dòng."""
    # Tách theo dấu chấm, chấm hỏi, chấm than, hoặc xuống dòng kép
    raw_sentences = re.split(r"(?<=[.!?\n])\s+", text)
    sentences = [s.strip() for s in raw_sentences if s.strip()]
    return sentences if sentences else [text]


def _create_child_chunks(
    parent_text: str,
    target_child_tokens: int = 200,
    overlap_tokens: int = 40
) -> List[RawChildChunk]:
    """Chia nhỏ Parent Chunk thành các Child Chunks có độ dài ~200 tokens với gối đầu (overlap)."""
    sentences = _split_into_sentences(parent_text)
    child_chunks: List[RawChildChunk] = []
    current_sentences: List[str] = []
    current_tokens = 0
    child_idx = 0

    for sent in sentences:
        sent_tokens = estimate_tokens(sent)
        if current_tokens + sent_tokens > target_child_tokens and current_sentences:
            content = " ".join(current_sentences)
            child_chunks.append(
                RawChildChunk(
                    index=child_idx,
                    content=content,
                    token_count=current_tokens
                )
            )
            child_idx += 1

            # Giữ lại các câu cuối cùng để làm overlap
            overlap_accum = 0
            overlap_sentences = []
            for prev_sent in reversed(current_sentences):
                p_tokens = estimate_tokens(prev_sent)
                if overlap_accum + p_tokens <= overlap_tokens:
                    overlap_sentences.insert(0, prev_sent)
                    overlap_accum += p_tokens
                else:
                    break

            current_sentences = overlap_sentences + [sent]
            current_tokens = sum(estimate_tokens(s) for s in current_sentences)
        else:
            current_sentences.append(sent)
            current_tokens += sent_tokens

    if current_sentences:
        content = " ".join(current_sentences)
        child_chunks.append(
            RawChildChunk(
                index=child_idx,
                content=content,
                token_count=current_tokens
            )
        )

    return child_chunks


def hierarchical_chunk(
    text: str,
    target_parent_tokens: int = 1000,
    target_child_tokens: int = 200,
    child_overlap_tokens: int = 40
) -> List[RawParentChunk]:
    """
    Thuật toán phân cấp:
    1. Tách văn bản thành các khối Parent dựa trên Headings (#, ##, Trang) hoặc đoạn văn.
    2. Mỗi Parent Chunk được kiểm soát trong khoảng ~800 - 1200 tokens.
    3. Từng Parent Chunk được chia nhỏ thành các Child Chunks ~200 tokens.
    """
    if not text.strip():
        return []

    # 1. Phát hiện các đoạn văn / section
    sections = re.split(r"\n(?=#{1,4}\s+|---\s*\[Trang\s+\d+\]\s*---|===\s*Bảng tính:)", text)
    if len(sections) <= 1:
        sections = text.split("\n\n")

    parent_chunks: List[RawParentChunk] = []
    current_section_texts: List[str] = []
    current_parent_tokens = 0
    current_heading: Optional[str] = None
    parent_idx = 0

    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue

        sec_tokens = estimate_tokens(sec)

        # Phát hiện heading trong section
        first_line = sec.split("\n")[0].strip()
        if first_line.startswith("#") or first_line.startswith("---") or first_line.startswith("==="):
            heading_candidate = re.sub(r"^[#\-\=\s]+|[#\-\=\s]+$", "", first_line)
        else:
            heading_candidate = None

        # Chỉ tách Parent Chunk mới ở ranh giới heading khi khối hiện tại đã đủ lớn (>= 200 tokens)
        # để không băm nhỏ các tiêu đề phụ ngắn trong cùng một điều khoản, hoặc khi vượt trần target_parent_tokens
        should_split_on_heading = bool(heading_candidate and current_section_texts and current_parent_tokens >= 200)
        if should_split_on_heading or (current_parent_tokens + sec_tokens > target_parent_tokens and current_section_texts):
            # Hoàn thiện 1 Parent Chunk giữ nguyên heading của chính nó
            parent_content = "\n\n".join(current_section_texts)
            children = _create_child_chunks(
                parent_content,
                target_child_tokens=target_child_tokens,
                overlap_tokens=child_overlap_tokens
            )
            parent_chunks.append(
                RawParentChunk(
                    index=parent_idx,
                    heading=current_heading,
                    content=parent_content,
                    token_count=current_parent_tokens,
                    children=children
                )
            )
            parent_idx += 1
            current_section_texts = [sec]
            current_parent_tokens = sec_tokens
            current_heading = heading_candidate
        else:
            current_section_texts.append(sec)
            current_parent_tokens += sec_tokens
            if heading_candidate and not current_heading:
                current_heading = heading_candidate

    if current_section_texts:
        parent_content = "\n\n".join(current_section_texts)
        children = _create_child_chunks(
            parent_content,
            target_child_tokens=target_child_tokens,
            overlap_tokens=child_overlap_tokens
        )
        parent_chunks.append(
            RawParentChunk(
                index=parent_idx,
                heading=current_heading,
                content=parent_content,
                token_count=current_parent_tokens,
                children=children
            )
        )

    return parent_chunks
