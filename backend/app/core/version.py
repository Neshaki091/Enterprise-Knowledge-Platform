"""
Nguồn duy nhất cho phiên bản ứng dụng.

`backend/app/core/version.py` là nơi duy nhất cần sửa khi phát hành phiên bản mới.
FastAPI, health endpoint, MCP server và test đều import `APP_VERSION`; workflow
build kiểm tra hằng số này khớp tag image trước khi build/push. Không có biến
version backend riêng ở chỗ khác.
"""

APP_VERSION = "2.4.1"
