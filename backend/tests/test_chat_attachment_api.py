"""Kiểm thử tài liệu đính kèm riêng trong chat: cách ly owner/session, TTL và export."""

import asyncio
import gzip
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.models.chat_attachment import ChatAttachment


async def _register(client: AsyncClient, email: str, tenant: str, slug: str) -> str:
    await client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Password123!",
        "full_name": f"Owner {tenant}",
        "tenant_name": tenant,
        "tenant_slug": slug,
    })
    login = await client.post("/api/v1/auth/login", json={
        "email": email,
        "password": "Password123!",
    })
    return login.json()["access_token"]


async def _add_employee(client: AsyncClient, owner_headers: dict, email: str) -> dict:
    created = await client.post(
        "/api/v1/tenants/members",
        json={
            "email": email,
            "password": "Password123!",
            "full_name": "Attachment Employee",
            "role": "EMPLOYEE",
        },
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _upload_attachment(
    client: AsyncClient,
    headers: dict,
    session_id: str,
    content: str = "Hợp đồng lao động có thời hạn 12 tháng, mức lương 20 triệu đồng mỗi tháng.",
):
    return await client.post(
        "/api/v1/chat/attachments",
        data={"session_id": session_id},
        files={"file": ("hop_dong_rieng.txt", content.encode("utf-8"), "text/plain")},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_chat_attachment_is_private_to_owner_and_session(client: AsyncClient):
    owner_token = await _register(client, "att_owner@acme.com", "Attach Corp", "attach-corp")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    employee_headers = await _add_employee(client, owner_headers, "att_employee@acme.com")

    other_token = await _register(client, "att_other@other.com", "Other Corp", "other-corp")
    other_headers = {"Authorization": f"Bearer {other_token}"}

    session_id = str(uuid.uuid4())
    upload = await _upload_attachment(client, owner_headers, session_id)
    assert upload.status_code == 201, upload.text
    attachment = upload.json()
    assert attachment["chunk_count"] >= 1
    assert attachment["expires_at"]

    owner_list = await client.get(
        f"/api/v1/chat/attachments?session_id={session_id}", headers=owner_headers
    )
    assert owner_list.status_code == 200
    assert owner_list.json()["total"] == 1

    # Cùng tenant, cùng session_id nhưng khác user: không thấy file.
    employee_list = await client.get(
        f"/api/v1/chat/attachments?session_id={session_id}", headers=employee_headers
    )
    assert employee_list.status_code == 200
    assert employee_list.json()["total"] == 0

    other_list = await client.get(
        f"/api/v1/chat/attachments?session_id={session_id}", headers=other_headers
    )
    assert other_list.status_code == 200
    assert other_list.json()["total"] == 0

    # Owner hỏi nội dung file: mock vẫn phải dùng attachment context.
    owner_chat = await client.post(
        "/api/v1/chat/query",
        json={
            "query": "Hợp đồng có thời hạn bao nhiêu tháng?",
            "mode": "mock",
            "session_id": session_id,
        },
        headers=owner_headers,
    )
    assert owner_chat.status_code == 200, owner_chat.text
    owner_data = owner_chat.json()
    assert "12 tháng" in owner_data["answer"]
    assert owner_data["attachment_sources"]
    assert owner_data["attachment_sources"][0]["file_name"] == "hop_dong_rieng.txt"

    # Nhân viên khác dùng đúng session_id cũng không nhận attachment context.
    employee_chat = await client.post(
        "/api/v1/chat/query",
        json={
            "query": "Hợp đồng có thời hạn bao nhiêu tháng?",
            "mode": "mock",
            "session_id": session_id,
        },
        headers=employee_headers,
    )
    assert employee_chat.status_code == 200, employee_chat.text
    assert employee_chat.json()["attachment_sources"] == []
    assert "12 tháng" not in employee_chat.json()["answer"]

    # Chỉ owner xoá được.
    forbidden_delete = await client.delete(
        f"/api/v1/chat/attachments/{attachment['id']}", headers=employee_headers
    )
    assert forbidden_delete.status_code == 404
    owner_delete = await client.delete(
        f"/api/v1/chat/attachments/{attachment['id']}", headers=owner_headers
    )
    assert owner_delete.status_code == 200
    after = await client.get(
        f"/api/v1/chat/attachments?session_id={session_id}", headers=owner_headers
    )
    assert after.json()["total"] == 0


@pytest.mark.asyncio
async def test_chat_attachment_upload_concurrency_is_bounded():
    """10 người upload cùng lúc không được parse/embed quá trần RAM cho phép."""
    from app.core.config import settings
    from app.services.chat_attachment_service import ChatAttachmentService

    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        await ChatAttachmentService.acquire_upload_slot()
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.05)
        finally:
            active -= 1
            ChatAttachmentService.release_upload_slot()

    await asyncio.gather(*(worker() for _ in range(10)))

    assert peak == settings.CHAT_ATTACHMENT_MAX_CONCURRENT_UPLOADS
    assert active == 0


@pytest.mark.asyncio
async def test_chat_attachment_stream_uses_private_context(client: AsyncClient, db_session):
    token = await _register(client, "att_stream@acme.com", "Att Stream Corp", "att-stream")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = str(uuid.uuid4())

    uploaded = await _upload_attachment(client, headers, session_id)
    assert uploaded.status_code == 201, uploaded.text

    stream = await client.post(
        "/api/v1/chat/stream",
        json={
            "query": "Hợp đồng có thời hạn bao nhiêu tháng?",
            "mode": "mock",
            "session_id": session_id,
        },
        headers=headers,
    )
    assert stream.status_code == 200
    body = stream.text
    assert "hop_dong_rieng.txt" in body
    assert "12 tháng" in body
    assert "attachment_sources" in body

    from app.models.chat_history import ChatTurn

    turns = (await db_session.execute(select(ChatTurn))).scalars().all()
    assert len(turns) == 1
    assert turns[0].has_attachment is True
    assert "12 tháng" in turns[0].answer


@pytest.mark.asyncio
async def test_chat_attachment_duplicate_and_expiry(
    client: AsyncClient,
    db_session,
):
    token = await _register(client, "att_ttl@acme.com", "TTL Corp", "ttl-corp")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = str(uuid.uuid4())

    first = await _upload_attachment(client, headers, session_id)
    assert first.status_code == 201, first.text
    duplicate = await _upload_attachment(client, headers, session_id)
    assert duplicate.status_code == 409

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    await db_session.execute(
        update(ChatAttachment)
        .where(ChatAttachment.session_id == uuid.UUID(session_id))
        .values(expires_at=past)
    )
    await db_session.commit()

    listed = await client.get(
        f"/api/v1/chat/attachments?session_id={session_id}", headers=headers
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 0

    chat = await client.post(
        "/api/v1/chat/query",
        json={"query": "Hợp đồng bao nhiêu tháng?", "mode": "mock", "session_id": session_id},
        headers=headers,
    )
    assert chat.status_code == 200
    assert chat.json()["attachment_sources"] == []


@pytest.mark.asyncio
async def test_ragas_export_excludes_attachment_turns(client: AsyncClient):
    token = await _register(client, "att_export@acme.com", "Att Export Corp", "att-export")
    headers = {"Authorization": f"Bearer {token}"}
    session_id = str(uuid.uuid4())

    uploaded = await _upload_attachment(client, headers, session_id)
    assert uploaded.status_code == 201
    private_chat = await client.post(
        "/api/v1/chat/query",
        json={"query": "Hợp đồng có thời hạn bao nhiêu tháng?", "mode": "mock", "session_id": session_id},
        headers=headers,
    )
    assert private_chat.status_code == 200
    assert private_chat.json()["attachment_sources"]

    public_chat = await client.post(
        "/api/v1/chat/query",
        json={"query": "Xin chào hệ thống", "mode": "mock", "session_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert public_chat.status_code == 200

    month = datetime.now(timezone.utc).strftime("%Y-%m")
    export = await client.get(
        f"/api/v1/chat/export?month={month}&include_mock=true",
        headers=headers,
    )
    assert export.status_code == 200
    records = [
        json.loads(line)
        for line in gzip.decompress(export.content).decode("utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    assert records[0]["user_input"] == "Xin chào hệ thống"
    assert records[0]["metadata"]["has_attachment"] is False
    assert records[0]["metadata"]["session_memory"] is not None
    # Không có dấu vết nội dung file private trong dataset.
    assert "12 tháng" not in json.dumps(records, ensure_ascii=False)