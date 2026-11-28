"""Sinh fixture synthetic cho stress test ingest.

Không dùng tài liệu thật/PII. File sinh ra nằm trong load_tests/fixtures/generated/
và bị gitignore. Chạy:
    python load_tests/generate_fixtures.py
    python load_tests/generate_fixtures.py --large --oversize
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib import enable_utf8_stdout

ROOT = Path(__file__).resolve().parent
GENERATED_DIR = ROOT / "fixtures" / "generated"


def _marker(prefix: str) -> str:
    return f"EKP-{prefix}-{uuid.uuid4().hex}"


def _text_content(size_bytes: int, marker: str) -> bytes:
    """Sinh text có marker lặp lại, xấp xỉ kích thước yêu cầu."""
    sentence = (
        f"{marker} | Chính sách nội bộ giả lập: nhân viên được nghỉ phép 14 ngày mỗi năm. "
        "Tài liệu này chỉ dùng cho kiểm thử tải, không phải dữ liệu thật.\n"
    )
    block = sentence * max(1, size_bytes // max(1, len(sentence.encode("utf-8"))))
    data = block.encode("utf-8")
    if len(data) <= size_bytes:
        return data
    # Cắt theo byte có thể rơi vào giữa ký tự UTF-8; bỏ phần dở để parser không lỗi.
    return data[:size_bytes].decode("utf-8", errors="ignore").encode("utf-8")


def _write_txt(path: Path, size_bytes: int, marker: str) -> dict[str, Any]:
    data = _text_content(size_bytes, marker)
    path.write_bytes(data)
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "name": path.name,
        "content_type": "text/plain",
        "size": len(data),
        "marker": marker,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _write_docx(path: Path, marker: str) -> dict[str, Any] | None:
    try:
        from docx import Document
    except ImportError:
        print("[skip] python-docx chưa cài; bỏ qua DOCX fixture")
        return None

    document = Document()
    document.add_heading("Tài liệu kiểm thử tải EKP", level=1)
    document.add_paragraph(f"Mã kiểm thử: {marker}")
    for index in range(1, 6):
        document.add_heading(f"Phần {index}", level=2)
        document.add_paragraph(
            "Nội dung giả lập cho stress test. Không chứa thông tin khách hàng. "
            f"Marker: {marker}."
        )
    document.save(path)
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "name": path.name,
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size": len(data),
        "marker": marker,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _write_xlsx(path: Path, marker: str) -> dict[str, Any] | None:
    try:
        from openpyxl import Workbook
    except ImportError:
        print("[skip] openpyxl chưa cài; bỏ qua XLSX fixture")
        return None

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "LoadTest"
    sheet.append(["Mã kiểm thử", marker])
    sheet.append(["Chính sách", "Nghỉ phép 14 ngày"])
    for row in range(3, 30):
        sheet.append([f"Dòng {row}", f"Nội dung giả lập {row}"])
    workbook.save(path)
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "name": path.name,
        "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "size": len(data),
        "marker": marker,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _write_pdf(path: Path, marker: str) -> dict[str, Any]:
    """PDF tối giản, chỉ ký tự ASCII để pypdf trích xuất ổn định."""
    text = f"EKP load test PDF. Marker: {marker}. No customer data."
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
    stream_bytes = stream.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream_bytes)).encode("ascii") + b" >>\nstream\n"
        + stream_bytes + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(obj)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(payload))
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "name": path.name,
        "content_type": "application/pdf",
        "size": len(payload),
        "marker": marker,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _random_broken_docx(path: Path) -> dict[str, Any]:
    data = bytes(random.getrandbits(8) for _ in range(4096))
    path.write_bytes(data)
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "name": path.name,
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "size": len(data),
        "marker": "BROKEN-DOCX-NO-MARKER",
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def build_fixtures(
    with_large: bool = False,
    with_oversize: bool = False,
    ingest_count: int = 200,
) -> dict[str, Any]:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, Any]] = {}

    for key, size in (
        ("txt_1kb", 1024),
        ("txt_100kb", 100 * 1024),
        ("txt_1mb", 1024 * 1024),
        ("txt_5mb", 5 * 1024 * 1024),
    ):
        marker = _marker(key)
        files[key] = _write_txt(GENERATED_DIR / f"{key}.txt", size, marker)

    docx_info = _write_docx(GENERATED_DIR / "docx_small.docx", _marker("docx"))
    if docx_info:
        files["docx_small"] = docx_info
    xlsx_info = _write_xlsx(GENERATED_DIR / "xlsx_small.xlsx", _marker("xlsx"))
    if xlsx_info:
        files["xlsx_small"] = xlsx_info
    files["pdf_small"] = _write_pdf(GENERATED_DIR / "pdf_small.pdf", _marker("pdf"))
    files["empty_txt"] = _write_txt(GENERATED_DIR / "empty.txt", 0, "EMPTY")
    files["broken_docx"] = _random_broken_docx(GENERATED_DIR / "broken.docx")

    if with_large:
        files["txt_24mb"] = _write_txt(
            GENERATED_DIR / "txt_24mb.txt", 24 * 1024 * 1024, _marker("txt24")
        )
    if with_oversize:
        files["txt_26mb"] = _write_txt(
            GENERATED_DIR / "txt_26mb.txt", 26 * 1024 * 1024, _marker("txt26")
        )

    ingest_files: list[dict[str, Any]] = []
    for index in range(1, ingest_count + 1):
        marker = _marker(f"ingest-{index:03d}")
        path = GENERATED_DIR / f"ingest_{index:03d}.txt"
        info = _write_txt(path, 32 * 1024, marker)
        ingest_files.append(info)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_dir": str(GENERATED_DIR.relative_to(ROOT)).replace("\\", "/"),
        "files": files,
        "ingest": ingest_files,
        "duplicate_file": ingest_files[0] if ingest_files else None,
    }
    manifest_path = GENERATED_DIR / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description="Sinh fixture synthetic cho load test EKP")
    parser.add_argument("--large", action="store_true", help="Sinh thêm file TXT 24MB")
    parser.add_argument("--oversize", action="store_true", help="Sinh thêm file TXT 26MB")
    parser.add_argument("--ingest-count", type=int, default=200, help="Số file ingest unique")
    args = parser.parse_args()

    manifest = build_fixtures(
        with_large=args.large,
        with_oversize=args.oversize,
        ingest_count=args.ingest_count,
    )
    print(f"Đã sinh fixture tại {GENERATED_DIR}")
    print(f"  - {len(manifest['files'])} file kích thước/định dạng")
    print(f"  - {len(manifest['ingest'])} file ingest unique (32KB)")


if __name__ == "__main__":
    main()