# DEFENSE_PLAN.md --- Kế hoạch Kiểm tra, Thẩm định & Bảo vệ Dự án

**Dự án:** Enterprise AI Knowledge Platform (SaaS Multi-tenant)  
**Mục đích:** Khung đánh giá năng lực toàn diện giúp bạn tự tin bảo vệ đồ án tốt nghiệp, vượt qua các vòng phỏng vấn kỹ thuật khắt khe nhất (Senior / Lead AI Engineer) và nghiệm thu thành công trước khách hàng doanh nghiệp.  
**Phiên bản:** 2.1  
**Ngày thiết lập:** 2026-11-23  

---

## 1. BA VAI TRÒ GIÁM KHẢO THẨM ĐỊNH (THE 3 EXAMINER PERSONAS)

Khi bạn hoàn thành từng sprint hoặc toàn bộ dự án, AI Agent sẽ lần lượt đóng 3 vai trò độc lập để chất vấn và "bắn phá" hệ thống của bạn:

```
                  +----------------------------------------------+
                  |           BẠN (DỰ THI / PHỎNG VẤN)           |
                  +----------------------------------------------+
                         ▲               ▲               ▲
                         │               │               │
        ┌────────────────┴─┐     ┌───────┴────────┐     ┌┴─────────────────┐
        │  1. GIẢNG VIÊN   │     │ 2. NHÀ TUYỂN   │     │  3. KHÁCH HÀNG   │
        │    KIẾN TRÚC     │     │      DỤNG      │     │   DOANH NGHIỆP   │
        └──────────────────┘     └────────────────┘     └──────────────────┘
        - Bản chất thuật toán    - Tư duy Trade-off     - Rò rỉ dữ liệu?
        - Cấu trúc dữ liệu       - Khả năng chịu tải    - SLA & Độ trễ?
        - Tính khoa học          - Chi phí vận hành     - Xóa dữ liệu thật?
```

### Vai trò 1: Giảng viên Kiến trúc (Academic & System Design Examiner)
- **Tập trung vào:** Tính khoa học, bản chất thuật toán, và cấu trúc dữ liệu.
- **Tiêu chí hỏi:** *Tại sao công thức RRF lại dùng hằng số 60? Cơ chế mmap của Linux hoạt động ở tầng trang nhớ (Page) thế nào? Tại sao Hierarchical Chunking giải quyết được trade-off ngữ cảnh?*

### Vai trò 2: Nhà tuyển dụng / Tech Lead Khắt khe (Hiring Manager)
- **Tập trung vào:** Tư duy đánh đổi (Trade-offs), khả năng mở rộng (Scalability), và kinh nghiệm thực chiến.
- **Tiêu chí hỏi:** *Tại sao không dùng SQLite hay Qdrant? Khi 1.000 người cùng bấm chat thì hệ thống nghẽn ở đâu? Làm sao chứng minh bạn không bịa số liệu 10.000 người dùng ảo?*

### Vai trò 3: Khách hàng Doanh nghiệp / CISO (Enterprise Security Officer)
- **Tập trung vào:** Bảo mật dữ liệu, quyền riêng tư, và tính liên tục trong kinh doanh (Business Continuity).
- **Tiêu chí hỏi:** *Công ty tôi là ngân hàng, dữ liệu có bị lẫn sang công ty khác không? Tại sao hệ thống của bạn không lưu file gốc, nếu tôi cần kiểm toán thì sao? Khi LLM của Google bị sập thì hệ thống phản ứng thế nào?*

---

## 2. BỘ 25 CÂU HỎI PHẢN BIỆN KINH ĐIỂN (DEFENSIVE DEFENSE QUESTIONS)

Dưới đây là 25 câu hỏi bạn **chắc chắn sẽ gặp** khi đi phỏng vấn hoặc bảo vệ đồ án. Bạn cần nắm vững câu trả lời để đạt điểm tuyệt đối:

### Chuyên đề 1: Tối ưu Hạ tầng & Bộ nhớ RAM (GCP 1GB RAM)
1. **Câu hỏi 1 (Giảng viên):** *Làm sao một hệ thống AI gồm cả Backend, Database và Vector Engine có thể chạy được trên máy chủ chỉ có 1.0 GB RAM của GCP Always Free?*
   - *Gợi ý trả lời:* Nhờ 3 đòn bẩy: (1) Cắt bỏ Redis/MinIO, chỉ dùng 1 PostgreSQL 16 cấu hình Low-Memory (128MB shared_buffers); (2) Dùng FAISS mmap để đẩy vector ra đĩa, RAM heap tốn < 50MB; (3) Bật Swapfile 4GB trên đĩa SSD để hấp thụ spike tải.
2. **Câu hỏi 2 (Nhà tuyển dụng):** *Tại sao không dùng Docker compose mặc định mà phải cấu hình `resources.limits.memory`?*
   - *Gợi ý trả lời:* Tránh hiện tượng 1 container ngốn hết RAM làm sập cả hệ điều hành. Phân bổ rõ ràng: Backend max 350MB, PostgreSQL max 350MB, phần còn lại cho OS và buffer cache.
3. **Câu hỏi 3 (Khách hàng):** *Dùng Swapfile trên đĩa có làm hệ thống bị chậm chạp kinh khủng không?*
   - *Gợi ý trả lời:* Không, vì các dữ liệu truy vấn thường xuyên vẫn nằm trên RAM vật lý. Swapfile chỉ giữ các trang nhớ tĩnh ít dùng (Cold memory) và đóng vai trò "tấm lưới bảo vệ an toàn" chống OOM khi tải tăng đột biến.
4. **Câu hỏi 4 (Giảng viên):** *Tại sao bạn lại chọn Python FastAPI thay vì Go hay Rust nếu mục tiêu là tiết kiệm RAM?*
   - *Gợi ý trả lời:* Vì hơn 85-90% hệ sinh thái và nhu cầu tuyển dụng AI Solution yêu cầu Python. Bằng cách tinh chỉnh 1 worker và dùng FAISS mmap, ta đã đưa mức tiêu thụ RAM của Python về chỉ ~150MB, hoàn toàn thỏa mãn ràng buộc phần cứng mà không phải đánh đổi hệ sinh thái thư viện AI phong phú của Python.
5. **Câu hỏi 5 (Nhà tuyển dụng):** *Khi có 50 file lớn được tải lên đồng thời, RAM của bạn có bị bùng nổ không?*
   - *Gợi ý trả lời:* Không, vì ta áp dụng Streaming Ingestion: Đọc file theo chunk bộ đệm (Chunked buffer) 64KB, tính hash SHA-256 ngay trong stream và hủy buffer ngay sau khi bóc tách text; không bao giờ load toàn bộ file 50MB vào RAM cùng một lúc.

---

### Chuyên đề 2: Multi-Tenancy & Bảo mật CSDL (RLS vs SQLite)
6. **Câu hỏi 6 (Khách hàng):** *Làm thế nào bạn đảm bảo 100% nhân viên Công ty A không bao giờ nhìn thấy tài liệu của Công ty B?*
   - *Gợi ý trả lời:* Áp dụng cơ chế bảo vệ 3 lớp: (1) Lớp Network Middleware đối soát token và trích xuất `TenantContext` server-side; (2) Lớp CSDL áp dụng **PostgreSQL Row-Level Security (RLS)** bắt buộc lọc theo `tenant_id`; (3) Lớp Vector Store tách biệt vật lý thành từng file index `indexes/{tenant_id}.index`.
7. **Câu hỏi 7 (Nhà tuyển dụng):** *Tại sao không dùng SQLite cho nhẹ RAM hơn mà lại nhất quyết dùng PostgreSQL?*
   - *Gợi ý trả lời:* SQLite chỉ hỗ trợ Single-Writer lock. Khi test tải 10.000 VUs, hàng chục thao tác ghi log audit, session và chat diễn ra cùng lúc sẽ lập tức gây lỗi `database is locked`. Ngoài ra, SQLite không có Row-Level Security (RLS) chuẩn Enterprise.
8. **Câu hỏi 8 (Giảng viên):** *Khi dùng Connection Pooling với PostgreSQL RLS, bạn xử lý nguy cơ rò rỉ Tenant Context giữa các kết nối tái sử dụng như thế nào?*
   - *Gợi ý trả lời:* Sử dụng cấu hình Transaction-Local: `SELECT set_config('app.current_tenant_id', :id, true)`. Tham số `true` đảm bảo biến tenant ID bị hủy ngay lập tức khi transaction commit hoặc rollback, không thể rò rỉ sang request tiếp theo trong pool.
9. **Câu hỏi 9 (Khách hàng):** *Tại sao hệ thống của bạn không lưu file gốc PDF/Word, nếu tôi cần tải lại tài liệu thì sao?*
   - *Gợi ý trả lời:* Nền tảng là **AI Knowledge & Search Engine**, không phải Cloud Storage sao lưu. Doanh nghiệp đã lưu file gốc trên Google Drive/SharePoint. Việc không lưu file gốc (Zero Raw Storage) giúp tiết kiệm 50% đĩa cứng và tăng cường bảo mật tối đa (hacker thâm nhập server không thể lấy cắp file nhị phân của khách hàng).
10. **Câu hỏi 10 (Nhà tuyển dụng):** *Nếu không lưu file gốc, sau này muốn đổi mô hình Embedding khác thì làm thế nào để re-index?*
    - *Gợi ý trả lời:* Chúng ta đã lưu toàn bộ văn bản thuần sạch (`raw_text TEXT`) trong PostgreSQL. Với 25.000 tài liệu, text thuần chỉ nặng ~2.5GB. Khi cần đổi model embedding, ta chỉ việc đọc `raw_text` từ DB để re-chunk và re-embed mà không cần file PDF gốc.

---

### Chuyên đề 3: Advanced RAG Pipeline (FAISS mmap, Parent-Child & Context Filtering)
11. **Câu hỏi 11 (Giảng viên):** *Kỹ thuật FAISS `IO_FLAG_MMAP` hoạt động như thế nào ở tầng hệ điều hành?*
    - *Gợi ý trả lời:* Hàm `mmap()` của Linux tạo một vùng nhớ ảo trỏ trực tiếp tới file index trên đĩa SSD. Không có dữ liệu nào được copy vào RAM heap của ứng dụng trước. Khi tìm kiếm vector, CPU chạm vào địa chỉ nào, OS Kernel sẽ sinh ngắt `Page Fault` để nạp đúng 4KB trang đĩa đó vào Page Cache, rồi giải phóng khi xong.
12. **Câu hỏi 12 (Nhà tuyển dụng):** *Hierarchical Chunking (Parent-Child) giải quyết bài toán gì mà chunking thông thường không làm được?*
    - *Gợi ý trả lời:* Giải quyết nghịch lý RAG: Chunk nhỏ (200 tokens) cho vector search chính xác nhưng thiếu ngữ cảnh cho LLM; chunk lớn (1.000 tokens) đủ ngữ cảnh nhưng vector bị loãng. Bằng cách index Child Chunk vào FAISS và bốc Parent Chunk tương ứng cho LLM đọc, ta đạt được cả độ chính xác tìm kiếm cao nhất lẫn ngữ cảnh trọn vẹn nhất.
13. **Câu hỏi 13 (Giảng viên):** *Tại sao công thức Reciprocal Rank Fusion (RRF) lại dùng hằng số $k = 60$?*
    - *Gợi ý trả lời:* $k=60$ là hằng số thực nghiệm được chứng minh bởi Cormack et al. Nó giúp giảm sự ảnh hưởng thái quá của các kết quả có thứ hạng quá cao ở một phía (Dense hoặc Sparse), tạo sự cân bằng tối ưu giữa tìm kiếm ngữ nghĩa và tìm kiếm từ khóa chính xác.
14. **Câu hỏi 14 (Khách hàng):** *Tại sao bạn không dùng LLM để chunking theo ngữ nghĩa cho chính xác nhất?*
    - *Gợi ý trả lời:* Vì vi phạm hạn mức 30 RPM của Gemma 4 26B và chi phí suy luận quá lớn. Thay vào đó, ta dùng **Semantic Distance Chunking** đo độ tụt khoảng cách Cosine giữa các câu kết hợp **Header-Aware**, chạy hoàn toàn trên CPU, tốc độ nhanh gấp 100 lần và chi phí bằng 0.
15. **Câu hỏi 15 (Nhà tuyển dụng):** *Context Filtering Pipeline của bạn hoạt động thế nào để bảo vệ hạn ngạch 16K TPM của Gemma 4 26B?*
    - *Gợi ý trả lời:* Qua 3 tầng lọc: (1) Cắt tỉa Top-K theo điểm liên quan động (> 0.65); (2) Khử trùng lặp nội dung giữa các chunk liền kề do overlap; (3) Nén câu ở cấp độ thông tin trọng tâm. Giảm 70% số token đầu vào, tăng tốc TTFT < 1.5s và phục vụ được nhiều user đồng thời hơn gấp 3 lần.

---

### Chuyên đề 4: Model Context Protocol (MCP) & Tích hợp AI Agent
16. **Câu hỏi 16 (Giảng viên):** *Giao thức Model Context Protocol (MCP) là gì và khác gì so với REST API thông thường?*
    - *Gợi ý trả lời:* REST API chỉ cung cấp các endpoints tĩnh cho lập trình viên gọi. MCP là chuẩn giao thức (dựa trên JSON-RPC 2.0) giúp mô hình ngôn ngữ lớn (LLM/Agent) tự động khám phá (Discovery), hiểu định dạng tham số (Tool Schema) và tự động gọi các công cụ (Tool Calling) để truy xuất tài nguyên máy tính một cách an toàn.
17. **Câu hỏi 17 (Nhà tuyển dụng):** *Bạn tự tay viết MCP Server hay dùng thư viện có sẵn? Cấu trúc của một gói tin MCP ra sao?*
    - *Gợi ý trả lời:* Tôi tự tay viết MCP Server để hiểu sâu cơ chế hoạt động. Gói tin tuân thủ chuẩn JSON-RPC 2.0 với 3 phương thức cốt lõi: `initialize` (bắt tay thỏa thuận capabilities), `tools/list` (liệt kê danh sách tools và schema), và `tools/call` (thực thi tool kèm tham số).
18. **Câu hỏi 18 (Khách hàng):** *Nếu một AI Agent bên ngoài (như Claude Desktop) kết nối vào MCP Server, làm sao ngăn Agent đọc trộm tài liệu của phòng ban khác?*
    - *Gợi ý trả lời:* Khi Agent gọi tool `search_knowledge_base`, MCP Server không chạy trực tiếp mà bắt buộc giải mã `TenantContext` của người dùng ủy quyền cho Agent. Mọi truy vấn bên trong Tool đều bị ép đi qua bộ lọc Tenant và Document-level ACL như một API thông thường.
19. **Câu hỏi 19 (Giảng viên):** *MCP hỗ trợ những phương thức truyền tải (Transports) nào trong dự án của bạn?*
    - *Gợi ý trả lời:* Hỗ trợ 2 transports: (1) `StdioTransport` (truyền thông điệp qua stdin/stdout phục vụ kết nối cục bộ với Desktop App như Claude Desktop); (2) `SSETransport` (Server-Sent Events qua HTTP phục vụ AI Agent trên môi trường web phân tán).
20. **Câu hỏi 20 (Nhà tuyển dụng):** *Làm sao xử lý lỗi khi AI Agent gửi tham số sai vào Tool của MCP Server?*
    - *Gợi ý trả lời:* Trả về mã lỗi JSON-RPC chuẩn: `-32602 (Invalid params)` kèm chi tiết lỗi validation từ Pydantic; không làm crash server và giúp LLM tự sửa sai tham số ở lần gọi tiếp theo.

---

### Chuyên đề 5: Kiểm thử Tải thực tế, Đo lường & Vận hành Sự cố
21. **Câu hỏi 21 (Nhà tuyển dụng):** *Hệ thống của bạn chịu được bao nhiêu người dùng thật? Có phải bạn "chém gió" 10.000 VU không?*
    - *Gợi ý trả lời:* Không chạy 10.000 VU trên hạ tầng free — đó là con số lý thuyết. 10.000 user/ngày × 300–400 request = 3–4 triệu request/ngày; riêng HTTP header (~300 B/response) đã tốn 27–36 GB/tháng, vượt 1 GB egress của Always Free 27–36 lần; quota 30 RPM/tenant chỉ chịu ~43.200 request/ngày. Bài test thực tế là 20 user/ngày, 3–5 phiên đồng thời, 200 request/giờ/user (4.000–8.000 request/ngày), trần 25 RPM; soak gộp 5 ngày làm việc với chính workload này; stress test tăng tải đến khi gặp 429 để xác định điểm bão hòa thật (30 RPM và ~10–11 lượt chat/phút do trần 16K TPM). Con số 10.000 chỉ dùng để phân biệt Configured VUs, Concurrent Sessions, RPS và số lượt gọi LLM thực tế — không dùng để tuyên bố người dùng thật.
22. **Câu hỏi 22 (Giảng viên):** *Tại sao không chạy k6 ngay trên VPS GCP mà phải chạy từ laptop bắn qua Internet?*
    - *Gợi ý trả lời:* Theo nguyên tắc kiểm thử chuẩn công nghiệp, Load Generator không bao giờ được đặt chung máy chủ với SUT (System Under Test) vì nó sẽ tự tranh chấp CPU/RAM với server. Bắn tải qua Internet giúp đo lường được cả độ trễ mạng thực tế và năng lực xử lý kết nối đồng thời của Nginx/FastAPI.
23. **Câu hỏi 23 (Khách hàng):** *Điều gì xảy ra nếu API của Gemma 4 26B bị sập hoặc trả lỗi 429 quá nhiều?*
    - *Gợi ý trả lời:* Hệ thống kích hoạt **Circuit Breaker**: Sau 5 lần lỗi liên tiếp, mạch ngắt trong 60 giây, tự động trả về thông báo thân thiện cho người dùng ("Dịch vụ AI đang bảo trì, vui lòng tra cứu tài liệu tạm thời qua tìm kiếm toàn văn"). Khi upstream phục hồi, mạch tự động đóng lại.
24. **Câu hỏi 24 (Nhà tuyển dụng):** *Làm sao bạn đo lường được RTO và RPO trong bài tập Diễn tập Khôi phục CSDL?*
    - *Gợi ý trả lời:* Dùng kịch bản mô phỏng ghi dữ liệu liên tục ➔ Giả lập xóa nhầm database ➔ Chạy lệnh khôi phục `pg_restore` từ bản sao lưu gần nhất. RTO đo bằng đồng hồ bấm giờ (thời gian khôi phục dịch vụ thực tế: ~12–30 phút với DB nhỏ); RPO phụ thuộc lịch backup — ≤ 24 giờ với dump đêm, hoặc ≤ 1 giờ nếu tăng tần suất dump mỗi giờ. Sau restore phải xác minh số bản ghi và tìm kiếm FAISS hoạt động.
25. **Câu hỏi 25 (Khách hàng):** *Khi một nhân viên bị đuổi việc, làm sao đảm bảo người đó không thể tiếp tục gọi API bằng token cũ?*
    - *Gợi ý trả lời:* Dù JWT Access Token còn hạn (15 phút), khi Admin bấm Khóa tài khoản, ID của user lập tức được đưa vào bảng `revoked_tokens` (`UNLOGGED` table) trong PostgreSQL. Middleware kiểm tra bảng này ở mỗi request, chặn đứng người dùng trong thời gian dưới 1 giây.

---

## 3. MA TRẬN TIÊU CHÍ ĐÁNH GIÁ NGHIỆM THU (RUBRIC)

| Tiêu chuẩn đánh giá | Mức Đạt (Pass) | Mức Xuất sắc (Distinction) |
|---|---|---|
| **Làm chủ Mã nguồn** | Tự code tay được các bài tập cốt lõi; giải thích được luồng dữ liệu. | Tự giải thích được các trade-off, phân tích được lý do tại sao chọn giải pháp này mà bỏ giải pháp khác. |
| **Kiến trúc & Tối ưu RAM** | Hệ thống chạy ổn định trên GCP Always Free e2-micro (RAM < 650MB). | Chứng minh được chỉ số RAM của FAISS mmap gần như 0MB qua công cụ `pmap` hoặc `htop`. |
| **Bảo mật Multi-Tenant** | Test chéo 2 tenant không bị lộ dữ liệu; chặn đứng BOLA/IDOR. | Hiểu sâu và cài đặt được Transaction-local RLS trong PostgreSQL Connection Pool. |
| **Chất lượng RAG & MCP** | Câu trả lời có trích dẫn đúng số trang; MCP Server cắm được vào Claude Desktop. | Triển khai thành công Hierarchical Parent-Child và Context Filtering giảm 70% prompt tokens. |
| **Kiểm thử & Vận hành** | Chạy được k6 test tải ổn định; có file log kiểm thử. | Phân tích sâu sắc báo cáo k6, giải thích được điểm bão hòa (Saturation point) và diễn tập thành công DB Restore drill. |
