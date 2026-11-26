# SPRINT 5: Tự viết Custom MCP Server & Tool Authorization

**Mục tiêu:** Tự tay xây dựng một máy chủ **Model Context Protocol (MCP)** chuẩn hóa từ đầu (sử dụng giao thức JSON-RPC 2.0) để các AI Agent bên ngoài (Claude Desktop, Antigravity, Custom Agents) có thể kết nối và tra cứu tri thức nội bộ một cách an toàn.  
**Ước tính thời gian:** 4 - 6 ngày  
**Trạng thái:** ✅ Đã hoàn thành Implementation - Chờ Developer Review & Defense  

---

## 1. PHÂN CHIA NHIỆM VỤ ĐÃ THỰC HIỆN

### AI Agent đã hoàn thành (Implementer):
1. **JSON-RPC 2.0 Protocol Engine ([backend/app/mcp/protocol.py](backend/app/mcp/protocol.py)):**
   - Bộ phân tích cú pháp và xử lý mã lỗi chuẩn: Parse error (-32700), Invalid request (-32600), Method not found (-32601), Invalid params (-32602), Unauthorized (-32001).
2. **MCP Core Engine & Tools Registry ([backend/app/mcp/server.py](backend/app/mcp/server.py)):**
   - Bắt tay giao thức `initialize` (Version `2024-11-05`), `ping`, `notifications/initialized`.
   - Cung cấp 2 Tools:
     - `search_knowledge_base`: Tra cứu tri thức nội bộ bằng Hybrid Search (FAISS mmap + FTS).
     - `get_document_context`: Lấy văn bản chi tiết và phân đoạn phân cấp theo `document_id`.
   - Kiểm soát quyền truy cập chặt chẽ (TenantContext) trước khi thực thi tool.
3. **Đa phương thức Truyền tải (Transports):**
   - **HTTP & SSE Transport ([backend/app/api/v1/endpoints/mcp.py](backend/app/api/v1/endpoints/mcp.py)):** Cổng `POST /api/v1/mcp` và `GET /api/v1/mcp/sse`.
   - **Stdio CLI Transport ([backend/app/mcp/cli.py](backend/app/mcp/cli.py)):** Chạy qua dòng lệnh stdin/stdout cho Desktop Agents.
   - File cấu hình mẫu: [claude_desktop_config.example.json](claude_desktop_config.example.json).
4. **Kiểm thử tự động ([backend/tests/test_mcp_server.py](backend/tests/test_mcp_server.py)):**
   - Đạt **28/28 Tests PASSED** trên toàn bộ hệ thống!

---

## 2. CHECKLIST NGHIỆM THU SPRINT 5
- [x] Khởi động MCP Server, gửi message JSON-RPC bắt tay (`initialize`) thành công.
- [x] Gọi tool `search_knowledge_base` trả về kết quả chính xác từ Hybrid Search.
- [x] Gọi tool `get_document_context` trả về cấu trúc Parent Chunks chi tiết.
- [x] Thử nghiệm gọi tool mà không có quyền: MCP Server từ chối và trả về lỗi bảo mật -32001.
- [x] Sẵn sàng file cấu hình kết nối Claude Desktop.
