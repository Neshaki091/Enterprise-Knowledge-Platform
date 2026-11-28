"""
MÔ TẢ KIẾN TRÚC:
Context Filtering & Compression Pipeline 3 tầng (ADR-006).
Tối ưu đặc thù cho hạn mức 16K TPM (Tokens Per Minute) cấu hình cho LLM:
- Tầng 1: Dynamic Thresholding + Chunk Cap - Lọc bỏ các chunk dưới ngưỡng RRF tuyệt đối/tương đối
  và giới hạn tối đa số chunk đưa vào LLM (mặc định tối đa 3 chunks).
- Tầng 2: Structural Cleanup & Cross-chunk Deduplication - Làm sạch Markdown nhiễu,
  tách câu/gạch đầu dòng chính xác và khử trùng lặp liên phân đoạn.
- Tầng 3: Query & Child-Guided Sentence Compression + Hard Token Budget - Chấm điểm câu
  dựa trên từ khóa query, bigram, neo child_chunk và số liệu; cắt gọt nghiêm ngặt theo
  ngân sách token tối đa (mặc định <= 550 context tokens) để giữ tổng input luôn nhẹ.
"""

import re
from typing import List, Dict, Any, Tuple, Optional
from app.schemas.search import SearchResultItem
from app.services.chunking_service import estimate_tokens
from app.services.prompt_service import PromptService


# Từ dừng phổ biến tiếng Việt & tiếng Anh được loại khỏi từ khóa khớp câu để tránh nhiễu điểm
_STOPWORDS = frozenset({
    "là", "của", "và", "có", "được", "trong", "về", "cho", "các", "những", "một",
    "không", "này", "đó", "khi", "nào", "gì", "sao", "với", "từ", "đến", "tại",
    "bởi", "theo", "bao", "nhiêu", "mấy", "hãy", "vui", "lòng", "cho", "biết",
    "the", "is", "are", "was", "were", "what", "how", "why", "when", "where",
    "in", "on", "at", "of", "to", "for", "and", "or", "a", "an", "by", "with",
})

_QUANTITATIVE_HINTS = (
    "bao nhiêu", "mấy", "số lượng", "tối đa", "tối thiểu", "bao lâu",
    "khi nào", "hạn", "ngày", "giờ", "tháng", "năm", "%", "phần trăm",
    "tiền", "mức", "chi phí", "giới hạn",
)


def _normalize_text(text: str) -> str:
    """Chuẩn hóa chữ thường và khoảng trắng để so sánh trùng lặp."""
    cleaned = re.sub(r"[#*_`~>|]+", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _clean_sentence_markdown(sentence: str) -> str:
    """Loại bỏ ký tự định dạng Markdown dư thừa nhưng giữ nguyên nội dung/số liệu."""
    cleaned = re.sub(r"^\s{0,3}(?:#{1,6}|[-*•]+)\s+", "", sentence)
    cleaned = re.sub(r"\*{2,}|__|`+", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _extract_keywords_and_bigrams(query: str) -> Tuple[set[str], set[str], bool]:
    """Trích xuất tập từ khóa nội dung, cụm 2 từ (bigrams) và cờ hỏi số liệu từ query."""
    lower_q = query.lower()
    is_quant = any(hint in lower_q for hint in _QUANTITATIVE_HINTS) or bool(re.search(r"\d+", lower_q))

    raw_tokens = [t for t in re.findall(r"[^\W_]+", lower_q) if len(t) >= 2 or t.isdigit()]
    content_tokens = [t for t in raw_tokens if t not in _STOPWORDS]
    if not content_tokens:
        content_tokens = raw_tokens

    bigrams = {f"{raw_tokens[i]} {raw_tokens[i + 1]}" for i in range(len(raw_tokens) - 1)}
    return set(content_tokens), bigrams, is_quant


def _truncate_to_token_budget(text: str, max_tokens: int) -> str:
    """Cắt chuỗi theo ranh giới từ sao cho ước lượng token không vượt ngưỡng."""
    if max_tokens <= 0 or not text:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text

    words = text.split()
    kept: List[str] = []
    for w in words:
        candidate = " ".join([*kept, w])
        if estimate_tokens(candidate) > max_tokens:
            break
        kept.append(w)

    if not kept and words:
        # Bảo đảm tối thiểu 1 phần câu quan trọng đầu tiên khi ngân sách quá eo hẹp
        char_cap = max(24, max_tokens * 3)
        return words[0][:char_cap]
    return " ".join(kept) + ("..." if len(kept) < len(words) else "")


class ContextFilterPipeline:
    """Bộ lọc ngữ cảnh 3 tầng tối ưu hóa cho giới hạn 16K TPM cấu hình cho LLM."""

    MIN_RRF_THRESHOLD: float = 0.005
    RELATIVE_RRF_RATIO: float = 0.55
    DEFAULT_MAX_CHUNKS: int = 3
    DEFAULT_MAX_CONTEXT_TOKENS: int = 550
    MIN_SENTENCE_CHARS: int = 12

    @classmethod
    def _tier1_dynamic_thresholding(
        cls,
        candidates: List[SearchResultItem],
        min_threshold: float = MIN_RRF_THRESHOLD,
        relative_ratio: float = RELATIVE_RRF_RATIO,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
    ) -> List[SearchResultItem]:
        """Tầng 1: Cắt tỉa ứng viên dưới ngưỡng tuyệt đối/tương đối và giới hạn số chunk."""
        if not candidates:
            return []

        sorted_candidates = sorted(candidates, key=lambda c: c.rrf_score, reverse=True)
        top_score = sorted_candidates[0].rrf_score
        dynamic_floor = max(min_threshold, top_score * relative_ratio)

        filtered = [c for c in sorted_candidates if c.rrf_score >= dynamic_floor][:max(1, max_chunks)]
        return filtered if filtered else [sorted_candidates[0]]

    @classmethod
    def _tier2_cross_chunk_deduplication(
        cls,
        candidates: List[SearchResultItem],
    ) -> List[Dict[str, Any]]:
        """
        Tầng 2: Làm sạch Markdown nhiễu, tách câu + dòng liệt kê và khử trùng lặp liên phân đoạn.
        """
        seen_sentences: set[str] = set()
        deduped_candidates: List[Dict[str, Any]] = []

        for c in candidates:
            # Tách theo dấu kết thúc câu HOẶC xuống dòng để không gộp cả danh sách thành 1 câu dài
            raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", c.parent_content)
            unique_sentences: List[str] = []

            for raw_sent in raw_sentences:
                cleaned = _clean_sentence_markdown(raw_sent)
                if not cleaned:
                    continue
                # Giữ câu có chứa số liệu ngắn (ví dụ: "14 ngày phép"), lọc dòng trang trí quá ngắn
                has_digit = any(ch.isdigit() for ch in cleaned)
                if len(cleaned) < cls.MIN_SENTENCE_CHARS and not (has_digit and len(cleaned) >= 5):
                    continue

                norm_key = _normalize_text(cleaned)
                if not norm_key or norm_key in seen_sentences:
                    continue

                # Khử trùng lặp khi một câu dài đã bao hàm toàn bộ câu ngắn vừa xét
                if any(norm_key in existing for existing in seen_sentences if len(existing) > len(norm_key) + 12):
                    continue

                seen_sentences.add(norm_key)
                unique_sentences.append(cleaned)

            if unique_sentences:
                deduped_candidates.append({
                    "item": c,
                    "sentences": unique_sentences,
                })

        return deduped_candidates

    @classmethod
    def _score_sentence(
        cls,
        sentence: str,
        query_words: set[str],
        query_bigrams: set[str],
        is_quant_query: bool,
        child_norm: str,
        heading_norm: str,
    ) -> Tuple[float, bool]:
        """
        Chấm điểm mức độ trọng tâm của từng câu.
        Returns:
            (tổng điểm xếp hạng, cờ đánh dấu câu có khớp trực tiếp nội dung query)
        """
        norm_s = _normalize_text(sentence)
        s_words = set(re.findall(r"[^\W_]+", norm_s))

        keyword_hits = len(query_words.intersection(s_words))
        bigram_hits = sum(1 for bg in query_bigrams if bg in norm_s)
        heading_hits = len(query_words.intersection(set(re.findall(r"[^\W_]+", heading_norm))))

        score = float(keyword_hits) + (2.0 * float(bigram_hits))
        if child_norm and norm_s and norm_s in child_norm:
            score += 1.5
        if heading_hits > 0:
            score += 0.5
        if is_quant_query and re.search(r"\d+", sentence):
            score += 1.0

        if len(query_words) >= 3:
            # Tránh giữ các câu rác chỉ trùng ngẫu nhiên 1 âm tiết đơn lẻ (ví dụ: "chính" trong "hành chính")
            has_direct_match = (bigram_hits >= 1) or (keyword_hits >= 2)
        else:
            has_direct_match = (keyword_hits > 0) or (bigram_hits > 0)
        return score, has_direct_match

    @classmethod
    def _tier3_sentence_compression(
        cls,
        query: str,
        deduped_candidates: List[Dict[str, Any]],
        raw_original_tokens: int,
        total_input_candidates: int,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Tầng 3: Nén ngữ cảnh ở mức độ câu có neo Child Chunk và kiểm soát cứng ngân sách token.
        """
        query_words, query_bigrams, is_quant_query = _extract_keywords_and_bigrams(query)
        compressed_blocks: List[str] = []
        retained_chunk_ids: List[str] = []
        remaining_tokens = max(60, max_context_tokens)

        for entry_idx, entry in enumerate(deduped_candidates):
            if remaining_tokens < 25:
                break

            c: SearchResultItem = entry["item"]
            sentences: List[str] = entry["sentences"]
            child_norm = _normalize_text(c.child_content or "")
            heading_norm = _normalize_text(c.heading or "")

            scored: List[Tuple[int, str, float, bool]] = []
            for idx, s in enumerate(sentences):
                s_score, direct_match = cls._score_sentence(
                    sentence=s,
                    query_words=query_words,
                    query_bigrams=query_bigrams,
                    is_quant_query=is_quant_query,
                    child_norm=child_norm,
                    heading_norm=heading_norm,
                )
                scored.append((idx, s, s_score, direct_match))

            # Chỉ giữ các câu thật sự khớp nội dung query; loại bỏ câu râu ria kể cả khi đoạn <= 3 câu
            query_matched = [(idx, s, sc) for idx, s, sc, direct in scored if direct]
            if query_matched:
                top_chunk_score = max(sc for _, _, sc in query_matched)
                if top_chunk_score >= 4.0:
                    min_keep_score = max(2.0, top_chunk_score * 0.25)
                    query_matched = [(idx, s, sc) for idx, s, sc in query_matched if sc >= min_keep_score]
                # Xếp hạng theo điểm giảm dần để ưu tiên câu đắt giá nhất khi cắt theo ngân sách token
                ranked = sorted(query_matched, key=lambda x: (x[2], -x[0]), reverse=True)
            else:
                # Nếu không khớp từ khóa trực tiếp (semantic search), chỉ lấy tối đa 2 câu tốt nhất
                ranked = sorted([(idx, s, sc) for idx, s, sc, _ in scored], key=lambda x: (x[2], -x[0]), reverse=True)[:2]

            heading_part = f", Phân mục: {c.heading}" if c.heading else ""
            header = f"[Nguồn: {c.document_title}{heading_part}]"
            header_tokens = estimate_tokens(header) + (2 if compressed_blocks else 0)
            body_budget = remaining_tokens - header_tokens

            if body_budget <= 10:
                if entry_idx == 0 and ranked:
                    body_budget = max(25, remaining_tokens - 6)
                else:
                    break

            chosen_items: List[Tuple[int, str]] = []
            used_body_tokens = 0

            for idx, s, _ in ranked:
                s_tokens = estimate_tokens(s)
                if used_body_tokens + s_tokens <= body_budget:
                    chosen_items.append((idx, s))
                    used_body_tokens += s_tokens
                elif entry_idx == 0 and not chosen_items:
                    # Bảo đảm chunk #1 luôn đóng góp ít nhất 1 câu đã cắt gọn hợp lệ
                    clipped = _truncate_to_token_budget(s, body_budget)
                    if clipped:
                        chosen_items.append((idx, clipped))
                        used_body_tokens += estimate_tokens(clipped)
                    break

            if not chosen_items:
                continue

            # Khôi phục đúng thứ tự câu gốc trong tài liệu để LLM đọc mạch lạc
            chosen_items.sort(key=lambda x: x[0])
            block_text = " ".join(s for _, s in chosen_items)
            block_formatted = f"{header}\n{block_text}"
            block_total_tokens = estimate_tokens(block_formatted) + (2 if compressed_blocks else 0)

            compressed_blocks.append(block_formatted)
            retained_chunk_ids.append(str(c.child_chunk_id))
            remaining_tokens = max(0, remaining_tokens - block_total_tokens)

        final_context = "\n\n".join(compressed_blocks)
        compressed_tokens = estimate_tokens(final_context) if final_context else 0
        prompt_input_tokens = PromptService.estimate_total_input_tokens(query, final_context) if final_context else PromptService.estimate_total_input_tokens(query, "")
        savings_percent = round(
            max(0.0, (1.0 - (compressed_tokens / max(1, raw_original_tokens))) * 100.0),
            1,
        )

        stats = {
            "original_tokens": raw_original_tokens,
            "compressed_tokens": compressed_tokens,
            "prompt_input_tokens": prompt_input_tokens,
            "savings_percent": savings_percent,
            "retained_chunks": len(compressed_blocks),
            "dropped_chunks": max(0, total_input_candidates - len(compressed_blocks)),
            "max_context_tokens": max_context_tokens,
            "retained_chunk_ids": retained_chunk_ids,
        }
        return final_context, stats

    @classmethod
    def filter_and_compress(
        cls,
        query: str,
        candidates: List[SearchResultItem],
        max_chunks: Optional[int] = None,
        max_context_tokens: Optional[int] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Điều phối toàn bộ Pipeline 3 tầng theo ngân sách token cấu hình cho LLM."""
        cap_chunks = max_chunks if max_chunks is not None else cls.DEFAULT_MAX_CHUNKS
        cap_tokens = max_context_tokens if max_context_tokens is not None else cls.DEFAULT_MAX_CONTEXT_TOKENS

        if not candidates:
            return "", {
                "original_tokens": 0,
                "compressed_tokens": 0,
                "prompt_input_tokens": PromptService.estimate_total_input_tokens(query, ""),
                "savings_percent": 0.0,
                "retained_chunks": 0,
                "dropped_chunks": 0,
                "max_context_tokens": cap_tokens,
                "retained_chunk_ids": [],
            }

        raw_original_tokens = max(1, sum(estimate_tokens(c.parent_content) for c in candidates))

        # Tầng 1: Cắt tỉa theo ngưỡng RRF tuyệt đối/tương đối và giới hạn số chunk
        t1_results = cls._tier1_dynamic_thresholding(
            candidates,
            min_threshold=cls.MIN_RRF_THRESHOLD,
            relative_ratio=cls.RELATIVE_RRF_RATIO,
            max_chunks=cap_chunks,
        )

        # Tầng 2: Làm sạch định dạng + Khử trùng lặp giữa các chunks
        t2_results = cls._tier2_cross_chunk_deduplication(t1_results)

        # Tầng 3: Nén câu trọng tâm và ép trần ngân sách token đầu vào
        return cls._tier3_sentence_compression(
            query=query,
            deduped_candidates=t2_results,
            raw_original_tokens=raw_original_tokens,
            total_input_candidates=len(candidates),
            max_context_tokens=cap_tokens,
        )
