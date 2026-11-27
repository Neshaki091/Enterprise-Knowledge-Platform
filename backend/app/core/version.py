"""
Nguồn duy nhất cho phiên bản ứng dụng.

`backend/app/core/version.py` là nơi duy nhất cần sửa khi phát hành phiên bản mới.
FastAPI, health endpoint, MCP server và test đều import `APP_VERSION`, còn CI/CD
kiểm tra hằng số này khớp với git tag `v*` trước khi build/push image.
"""

APP_VERSION = "2.3.3"
