"""
MÔ TẢ KIẾN TRÚC:
Module Document Parsers thực hiện Ephemeral Ingestion (ADR-003).
Toàn bộ quá trình đọc nhị phân và trích xuất text diễn ra trong bộ nhớ (In-memory BytesIO),
không bao giờ ghi file nhị phân ra đĩa SSD. Buffer bị giải phóng ngay sau khi trích xuất text.
"""

import io
import re
import hashlib
import logging
import zipfile
from typing import Tuple, Dict, Any, Optional
from fastapi import HTTPException, status
import pypdf
from pypdf.errors import PyPdfError
import docx
import openpyxl

logger = logging.getLogger(__name__)


def calculate_sha256(data: bytes) -> str:
    """Tính mã băm SHA-256 dạng chuỗi hex từ dữ liệu nhị phân."""
    hasher = hashlib.sha256()
    hasher.update(data)
    return hasher.hexdigest()


def clean_extracted_text(text: str) -> str:
    """
    Chuẩn hóa văn bản bóc tách:
    - Loại bỏ ký tự NUL (\x00) vì PostgreSQL Text không chấp nhận byte 0.
    - Chuẩn hóa khoảng trắng Unicode.
    - Rút gọn các dòng trống liên tiếp.
    """
    if not text:
        return ""
    # 1. Loại bỏ NUL bytes
    clean = text.replace("\x00", "")
    # 2. Thay thế non-breaking spaces
    clean = clean.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    # 3. Thu gọn các dòng trống liên tiếp (>2 dòng -> 2 dòng)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    # 4. Thu gọn nhiều khoảng trắng liên tiếp trong một dòng
    clean = re.sub(r"[ \t]+", " ", clean)
    return clean.strip()


def parse_pdf(data: bytes) -> Tuple[str, Dict[str, Any]]:
    """Bóc tách văn bản từ file PDF trong RAM, có kiểm tra chữ ký file và bắt lỗi an toàn."""
    if b"%PDF" not in data[:1024]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File PDF không hợp lệ: Thiếu chữ ký định dạng %PDF.",
        )
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        pages_text = []
        total_pages = len(reader.pages)
        for idx, page in enumerate(reader.pages):
            page_content = page.extract_text() or ""
            if page_content.strip():
                pages_text.append(f"--- [Trang {idx + 1}] ---\n{page_content}")

        full_text = clean_extracted_text("\n\n".join(pages_text))
        metadata = {
            "format": "pdf",
            "total_pages": total_pages,
            "is_encrypted": reader.is_encrypted,
        }
        return full_text, metadata
    except (PyPdfError, ValueError, Exception) as exc:
        logger.warning("Lỗi bóc tách PDF: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể đọc file PDF: Cấu trúc tệp bị hỏng hoặc được bảo vệ bằng mật khẩu.",
        )


def parse_docx(data: bytes) -> Tuple[str, Dict[str, Any]]:
    """Bóc tách văn bản từ file DOCX (Word), bắt lỗi định dạng Zip/XML hỏng."""
    try:
        doc = docx.Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

        # Trích xuất dữ liệu trong bảng nếu có
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    paragraphs.append(row_text)

        full_text = clean_extracted_text("\n\n".join(paragraphs))
        metadata = {
            "format": "docx",
            "total_paragraphs": len(paragraphs),
            "total_tables": len(doc.tables)
        }
        return full_text, metadata
    except (zipfile.BadZipFile, KeyError, ValueError, Exception) as exc:
        logger.warning("Lỗi bóc tách DOCX: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể đọc file Word (.docx): Định dạng tệp không hợp lệ hoặc bị hỏng.",
        )


def parse_xlsx(data: bytes) -> Tuple[str, Dict[str, Any]]:
    """Bóc tách dữ liệu từ file Excel (XLSX), bắt lỗi định dạng Zip/Workbook hỏng."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        rows_text = []
        total_sheets = len(wb.sheetnames)

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            rows_text.append(f"=== Bảng tính: {sheet_name} ===")
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    rows_text.append(" | ".join(cells))

        full_text = clean_extracted_text("\n".join(rows_text))
        metadata = {
            "format": "xlsx",
            "sheets": wb.sheetnames,
            "total_sheets": total_sheets
        }
        return full_text, metadata
    except (zipfile.BadZipFile, KeyError, ValueError, Exception) as exc:
        logger.warning("Lỗi bóc tách XLSX: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể đọc file Excel (.xlsx): Định dạng tệp không hợp lệ hoặc bị hỏng.",
        )


def parse_text(data: bytes) -> Tuple[str, Dict[str, Any]]:
    """Bóc tách văn bản từ file thuần Text (TXT, MD, CSV, JSON)."""
    for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
        try:
            decoded = data.decode(enc)
            full_text = clean_extracted_text(decoded)
            return full_text, {"format": "text", "encoding": enc}
        except (UnicodeDecodeError, LookupError):
            continue
    # Fallback decode
    return clean_extracted_text(data.decode("utf-8", errors="replace")), {"format": "text", "encoding": "fallback"}


def extract_document_content(
    file_name: str,
    file_bytes: bytes,
    content_type: Optional[str] = None
) -> Tuple[str, Dict[str, Any]]:
    """
    Cổng phân phối parser dựa trên phần mở rộng hoặc content-type.
    Tự động giải phóng buffer sau khi hoàn thành.
    """
    ext = file_name.split(".")[-1].lower() if "." in file_name else ""

    if ext == "pdf" or content_type == "application/pdf":
        return parse_pdf(file_bytes)
    elif ext in ["docx", "doc"] or content_type in [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword"
    ]:
        return parse_docx(file_bytes)
    elif ext in ["xlsx", "xls"] or content_type in [
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel"
    ]:
        return parse_xlsx(file_bytes)
    elif ext in ["txt", "md", "csv", "json", "log", "rst"] or (content_type and content_type.startswith("text/")):
        return parse_text(file_bytes)
    else:
        # Thử đọc như plain text
        return parse_text(file_bytes)
