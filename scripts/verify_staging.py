"""
Script kiểm thử xác thực môi trường Staging / Production sau khi triển khai:
Kiểm tra lần lượt 5 chặng thật qua HTTP API:
1. Health check probe (/api/v1/health) kèm kiểm tra kết nối DB thật
2. Authentication (/api/v1/auth/register & /login)
3. Ingestion (/api/v1/documents/upload)
4. Hybrid Search (/api/v1/search/hybrid)
5. RAG Chat (/api/v1/chat/query) & dọn dẹp tài liệu smoke test
"""

import sys
import uuid
import httpx


def verify_staging(base_url: str = "http://localhost:8000") -> None:
    """Chạy kiểm thử smoke end-to-end đầy đủ 5 chặng trên server đang chạy."""
    print(f"🚀 Bắt đầu kiểm tra xác thực Staging tại {base_url}...")
    client = httpx.Client(base_url=base_url, timeout=30.0)
    run_id = uuid.uuid4().hex[:8]
    email = f"staging_{run_id}@enterprise.local"
    password = "StagingPassword123!"

    # 1. Health check (DB + FAISS readiness)
    print("1. Kiểm tra Liveness & Readiness probe...")
    r1 = client.get("/api/v1/health")
    assert r1.status_code == 200, f"Health check thất bại: {r1.status_code} {r1.text}"
    h_data = r1.json()
    assert h_data["status"] == "healthy" and h_data.get("database") == "connected", h_data
    print(f"   ✅ Health: {h_data['status']} | DB: {h_data['database']}")

    # 2. Authentication
    print("2. Kiểm tra Đăng ký & Đăng nhập Tenant...")
    r2_reg = client.post("/api/v1/auth/register", json={
        "email": email,
        "password": password,
        "full_name": "Staging Verifier",
        "tenant_name": f"Staging Corp {run_id}",
        "tenant_slug": f"staging-{run_id}",
    })
    assert r2_reg.status_code == 201, f"Register thất bại: {r2_reg.status_code} {r2_reg.text}"

    r2_log = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r2_log.status_code == 200, f"Login thất bại: {r2_log.status_code} {r2_log.text}"
    headers = {"Authorization": f"Bearer {r2_log.json()['access_token']}"}
    print("   ✅ Auth: Register 201 & Login 200")

    # 3. Ingestion
    print("3. Kiểm tra Tải lên & Index tài liệu...")
    doc_text = (
        "# QUY ĐỊNH SMOKE TEST STAGING\n\n"
        f"Mã xác thực triển khai staging-{run_id}: Nhân viên kỹ thuật được cấp hạn mức 16 ngày nghỉ phép."
    )
    r3 = client.post(
        "/api/v1/documents/upload",
        files={"file": (f"staging_{run_id}.txt", doc_text.encode("utf-8"), "text/plain")},
        headers=headers,
    )
    assert r3.status_code == 201, f"Upload thất bại: {r3.status_code} {r3.text}"
    doc_id = r3.json()["id"]
    print(f"   ✅ Ingestion: doc_id={doc_id}, child_chunks={r3.json()['child_chunk_count']}")

    try:
        # 4. Hybrid Search
        print("4. Kiểm tra Hybrid Search (FAISS mmap + FTS)...")
        r4 = client.post(
            "/api/v1/search/hybrid",
            json={"query": f"staging-{run_id} bao nhiêu ngày nghỉ phép", "top_k": 3},
            headers=headers,
        )
        assert r4.status_code == 200 and r4.json()["total_found"] >= 1, r4.text
        print(f"   ✅ Search: found={r4.json()['total_found']}")

        # 5. RAG Chat (mode=hybrid mặc định)
        print("5. Kiểm tra RAG Chat...")
        r5 = client.post(
            "/api/v1/chat/query",
            json={"query": "Nhân viên kỹ thuật có bao nhiêu ngày nghỉ phép?", "top_k": 3},
            headers=headers,
        )
        assert r5.status_code == 200, f"Chat RAG thất bại: {r5.status_code} {r5.text}"
        c_data = r5.json()
        assert len(c_data.get("retrieved_chunks", [])) >= 1 and len(c_data.get("citations", [])) >= 1, c_data
        print(f"   ✅ Chat RAG: mode={c_data['mode']}, compressed_tokens={c_data['compression_stats']['compressed_tokens']}")
    finally:
        client.delete(f"/api/v1/documents/{doc_id}", headers=headers)

    print("🎉 TẤT CẢ 5 BƯỚC XÁC THỰC STAGING HOÀN TẤT THÀNH CÔNG!")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    verify_staging(url)
