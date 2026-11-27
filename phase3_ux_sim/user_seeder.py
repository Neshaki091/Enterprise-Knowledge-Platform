"""Khởi tạo một lần duy nhất ~100 User Accounts thông qua LLM và lưu xuống Supabase Cloud DB."""

import logging
import math
import uuid
from typing import List, Dict
import httpx

from .config import SimConfig
from .llm_persona import generate_user_profiles_via_llm
from .storage import SimStorage, UxSimUser

logger = logging.getLogger(__name__)

_STARTER_DOC_1 = (
    "# QUY ĐỊNH NGHỈ PHÉP, CÔNG TÁC PHÍ & LÀM VIỆC TỪ XA 2026\n\n"
    "Mỗi nhân viên chính thức được hưởng 14 ngày nghỉ phép có lương mỗi năm; "
    "làm đủ 5 năm được cộng thêm 1 ngày phép thâm niên.\n"
    "Nhân viên kỹ thuật được làm việc từ xa tối đa 2 ngày mỗi tuần và phải bật 2FA.\n\n"
    "## PHỤ CẤP & CÔNG TÁC PHÍ\n\n"
    "Nhân viên đi công tác trong nước được thanh toán phụ cấp lưu trú tối đa 1.200.000 VNĐ/đêm "
    "và trợ cấp ăn trưa 950.000 VNĐ/tháng.\n"
)

_STARTER_DOC_2 = (
    "# QUY TRÌNH PHÊ DUYỆT HỢP ĐỒNG & BẢO MẬT DỮ LIỆU KHÁCH HÀNG\n\n"
    "Mọi hợp đồng giá trị từ 500 triệu VNĐ trở lên bắt buộc có chữ ký của Giám đốc Tài chính và Pháp chế.\n"
    "Dữ liệu khách hàng và chuỗi kết nối DB riêng phải được mã hóa AES-256-GCM Zero-Knowledge; "
    "tuyệt đối không sao chép ra thiết bị cá nhân.\n"
)


async def ensure_users_seeded_once(
    cfg: SimConfig,
    storage: SimStorage,
    client: httpx.AsyncClient,
) -> List[UxSimUser]:
    """
    Kiểm tra số user đã seed trong Supabase/DB trước:
    - Nếu đã đủ `cfg.target_user_count` (mặc định 100), KHÔNG tạo user lại lần 2.
    - Nếu thiếu, gọi LLM sinh đủ phần thiếu, phân bổ qua các Tenants (<= 10 user/tenant)
      và nạp tài liệu tri thức mẫu để user hỏi đáp thật.
    """
    existing_count = await storage.count_users()
    if existing_count >= cfg.target_user_count:
        logger.info("Đã có %d/%d tài khoản trong DB — bỏ qua bước seed lại (1-time only).", existing_count, cfg.target_user_count)
        return await storage.list_users(limit=cfg.target_user_count)

    missing = cfg.target_user_count - existing_count
    run_tag = uuid.uuid4().hex[:6]
    profiles = await generate_user_profiles_via_llm(
        cfg=cfg,
        count_needed=missing,
        start_index=existing_count + 1,
        run_suffix=run_tag,
    )

    per_tenant = max(2, min(cfg.users_per_tenant, 10))
    total_batches = math.ceil(len(profiles) / per_tenant)
    created_rows: List[UxSimUser] = []

    for batch_idx in range(total_batches):
        chunk = profiles[batch_idx * per_tenant : (batch_idx + 1) * per_tenant]
        owner_profile = chunk[0]
        tenant_slug = f"uxsim-{run_tag}-{batch_idx + 1}"
        tenant_name = f"UX Sim Enterprise {batch_idx + 1} ({run_tag})"

        # 1. Đăng ký Owner + Tenant mới (mặc định tối đa 10 user/tenant)
        reg_res = await client.post(
            "/api/v1/auth/register",
            json={
                "email": owner_profile["email"],
                "password": owner_profile["password"],
                "full_name": owner_profile["full_name"],
                "tenant_name": tenant_name,
                "tenant_slug": tenant_slug,
            },
        )
        if reg_res.status_code != 201:
            logger.warning("Không thể tạo tenant batch %d (%s): %s", batch_idx, reg_res.status_code, reg_res.text[:160])
            continue
        owner_user_json = reg_res.json()

        login_res = await client.post(
            "/api/v1/auth/login",
            json={"email": owner_profile["email"], "password": owner_profile["password"]},
        )
        if login_res.status_code != 200:
            continue
        tok_data = login_res.json()
        owner_headers = {"Authorization": f"Bearer {tok_data['access_token']}"}
        tenant_id = str(tok_data["tenant_id"])

        created_rows.append(
            UxSimUser(
                backend_user_id=str(owner_user_json["id"]),
                tenant_id=tenant_id,
                tenant_slug=tenant_slug,
                username=owner_profile["username"],
                email=owner_profile["email"],
                password=owner_profile["password"],
                full_name=owner_profile["full_name"],
                role="OWNER",
                department_name=owner_profile["department_name"],
                persona_goal=owner_profile["persona_goal"],
            )
        )

        # 2. Nạp 2 tài liệu mẫu cho Tenant vừa tạo để mọi user đều tìm kiếm/chat được
        for fname, content in (("policy_2026.md", _STARTER_DOC_1), ("security_rules.md", _STARTER_DOC_2)):
            await client.post(
                "/api/v1/documents/upload",
                files={"file": (fname, content.encode("utf-8"), "text/plain")},
                headers=owner_headers,
            )

        # 3. Tạo phòng ban + thêm tối đa 9 thành viên còn lại vào cùng Tenant
        dept_map: Dict[str, str] = {}
        for emp_profile in chunk[1:]:
            dname = emp_profile["department_name"]
            if dname not in dept_map:
                dcode = f"D{abs(hash(dname)) % 9000 + 1000}"
                d_res = await client.post(
                    "/api/v1/departments",
                    json={"name": dname, "code": dcode},
                    headers=owner_headers,
                )
                if d_res.status_code == 201:
                    dept_map[dname] = d_res.json()["id"]

            add_res = await client.post(
                "/api/v1/tenants/members",
                json={
                    "email": emp_profile["email"],
                    "password": emp_profile["password"],
                    "full_name": emp_profile["full_name"],
                    "role": "EMPLOYEE",
                    "department_id": dept_map.get(dname),
                },
                headers=owner_headers,
            )
            if add_res.status_code == 201:
                m_json = add_res.json()
                created_rows.append(
                    UxSimUser(
                        backend_user_id=str(m_json["user_id"]),
                        tenant_id=tenant_id,
                        tenant_slug=tenant_slug,
                        username=emp_profile["username"],
                        email=emp_profile["email"],
                        password=emp_profile["password"],
                        full_name=emp_profile["full_name"],
                        role="EMPLOYEE",
                        department_name=dname,
                        persona_goal=emp_profile["persona_goal"],
                    )
                )

    await storage.add_users(created_rows)
    return await storage.list_users(limit=cfg.target_user_count)
