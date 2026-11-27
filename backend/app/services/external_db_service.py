"""
MÔ TẢ KIẾN TRÚC:
Service quản lý kết nối DB riêng của doanh nghiệp theo mô hình Zero-Knowledge:
- Mã hóa DSN bằng AES-256-GCM với khóa dẫn xuất PBKDF2-HMAC-SHA256 từ `tenant_secret`
  (không lưu `tenant_secret` trong DB -> kể cả Platform Owner cũng không thể giải mã).
- Ký `db_jwt` bằng khóa dẫn xuất từ `tenant_secret` để AI Agent xác thực khi gọi MCP.
- Bộ lọc SQL Read-Only nghiêm ngặt: chỉ cho phép `SELECT`, kiểm tra `allowed_tables`, giới hạn 50 dòng.
"""

import os
import re
import uuid
import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Tuple
from urllib.parse import urlparse

import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from fastapi import HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.external_db import ExternalDBConnector
from app.schemas.external_db import ExternalDBConnectorCreate


_FORBIDDEN_SQL_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|EXEC|EXECUTE|COPY|GRANT|REVOKE|VACUUM|INTO\s+OUTFILE)\b",
    re.IGNORECASE,
)


def _derive_aes_key(tenant_id: uuid.UUID, tenant_secret: str, salt: bytes) -> bytes:
    """Dẫn xuất khóa AES-256 từ (tenant_id, tenant_secret, salt)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100_000,
    )
    material = f"{tenant_id}:{tenant_secret}".encode("utf-8")
    return kdf.derive(material)


def _derive_jwt_signing_key(tenant_id: uuid.UUID, tenant_secret: str) -> str:
    """Dẫn xuất khóa ký JWT riêng của doanh nghiệp từ `tenant_secret`."""
    return hashlib.sha256(f"mcp-ext-db:{tenant_id}:{tenant_secret}".encode("utf-8")).hexdigest()


def _mask_dsn_host(db_type: str, connection_uri: str) -> str:
    """Che thông tin user/password trong DSN, chỉ giữ loại DB và host/tên file."""
    if db_type == "sqlite":
        return "sqlite:///***"
    parsed = urlparse(connection_uri)
    host = parsed.hostname or "unknown-host"
    port = f":{parsed.port}" if parsed.port else ""
    db_name = parsed.path.lstrip("/") or "db"
    return f"{db_type}://***@{host}{port}/{db_name}"


class ExternalDBService:
    """Quản lý kết nối DB riêng, cấp phát DB JWT và thực thi truy vấn Read-Only an toàn."""

    MAX_ROWS: int = 50
    QUERY_TIMEOUT_SEC: float = 5.0

    @classmethod
    def encrypt_dsn(cls, dsn: str, tenant_id: uuid.UUID, tenant_secret: str) -> Tuple[str, str]:
        """Mã hóa DSN bằng AES-256-GCM; trả về (encrypted_b64, salt_b64)."""
        salt = os.urandom(16)
        key = _derive_aes_key(tenant_id, tenant_secret, salt)
        nonce = os.urandom(12)
        aesgcm = AESGCM(key)
        ct = aesgcm.encrypt(nonce, dsn.encode("utf-8"), str(tenant_id).encode("utf-8"))
        payload_b64 = base64.urlsafe_b64encode(nonce + ct).decode("ascii")
        salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
        return payload_b64, salt_b64

    @classmethod
    def decrypt_dsn(
        cls,
        encrypted_b64: str,
        salt_b64: str,
        tenant_id: uuid.UUID,
        tenant_secret: str,
    ) -> str:
        """Giải mã DSN trong RAM; từ chối ngay nếu sai `tenant_secret`."""
        try:
            salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
            raw = base64.urlsafe_b64decode(encrypted_b64.encode("ascii"))
            nonce, ct = raw[:12], raw[12:]
            key = _derive_aes_key(tenant_id, tenant_secret, salt)
            aesgcm = AESGCM(key)
            plaintext = aesgcm.decrypt(nonce, ct, str(tenant_id).encode("utf-8"))
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, Exception):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Không thể giải mã kết nối DB: tenant_secret không hợp lệ.",
            )

    @classmethod
    async def create_connector(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        data: ExternalDBConnectorCreate,
    ) -> ExternalDBConnector:
        """Đăng ký kết nối DB riêng đã mã hóa cho Tenant."""
        dup = (
            await db.execute(
                select(ExternalDBConnector).where(
                    ExternalDBConnector.tenant_id == tenant_id,
                    ExternalDBConnector.name == data.name.strip(),
                )
            )
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Kết nối DB '{data.name}' đã tồn tại trong doanh nghiệp.",
            )

        enc_dsn, salt_b64 = cls.encrypt_dsn(data.connection_uri, tenant_id, data.tenant_secret)
        connector = ExternalDBConnector(
            tenant_id=tenant_id,
            created_by=user_id,
            name=data.name.strip(),
            db_type=data.db_type,
            encrypted_dsn=enc_dsn,
            encryption_salt=salt_b64,
            host_masked=_mask_dsn_host(data.db_type, data.connection_uri),
            allowed_tables=data.allowed_tables,
            is_active=True,
        )
        db.add(connector)
        await db.commit()
        await db.refresh(connector)
        return connector

    @classmethod
    async def list_connectors(cls, db: AsyncSession, tenant_id: uuid.UUID) -> List[ExternalDBConnector]:
        """Liệt kê các kết nối DB thuộc riêng Tenant (không lộ DSN)."""
        res = await db.execute(
            select(ExternalDBConnector)
            .where(ExternalDBConnector.tenant_id == tenant_id)
            .order_by(ExternalDBConnector.created_at.desc())
        )
        return list(res.scalars().all())

    @classmethod
    async def delete_connector(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        connector_id: uuid.UUID,
    ) -> None:
        """Xóa kết nối DB riêng thuộc Tenant."""
        connector = (
            await db.execute(
                select(ExternalDBConnector).where(
                    ExternalDBConnector.id == connector_id,
                    ExternalDBConnector.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if not connector:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy kết nối DB.")
        await db.delete(connector)
        await db.commit()

    @classmethod
    async def issue_db_jwt(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        connector_id: uuid.UUID,
        tenant_secret: str,
        expires_minutes: int = 60,
    ) -> Tuple[str, ExternalDBConnector]:
        """Xác thực `tenant_secret` và cấp phát JWT giới hạn quyền truy vấn DB qua MCP."""
        connector = (
            await db.execute(
                select(ExternalDBConnector).where(
                    ExternalDBConnector.id == connector_id,
                    ExternalDBConnector.tenant_id == tenant_id,
                    ExternalDBConnector.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if not connector:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kết nối DB không tồn tại hoặc đã tắt.")

        # Kiểm tra đúng tenant_secret bằng giải mã thử DSN
        cls.decrypt_dsn(connector.encrypted_dsn, connector.encryption_salt, tenant_id, tenant_secret)

        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(connector.id),
            "tenant_id": str(tenant_id),
            "connector_id": str(connector.id),
            "allowed_tables": connector.allowed_tables,
            "scope": "read_only",
            "token_type": "mcp_external_db",
            "iat": now,
            "exp": now + timedelta(minutes=expires_minutes),
        }
        signing_key = _derive_jwt_signing_key(tenant_id, tenant_secret)
        token = jwt.encode(payload, signing_key, algorithm="HS256")
        return token, connector

    @classmethod
    def verify_db_jwt(
        cls,
        db_jwt: str,
        tenant_id: uuid.UUID,
        connector_id: uuid.UUID,
        tenant_secret: str,
    ) -> Dict[str, Any]:
        """Xác thực DB JWT bằng khóa dẫn xuất từ `tenant_secret` của doanh nghiệp."""
        signing_key = _derive_jwt_signing_key(tenant_id, tenant_secret)
        try:
            payload = jwt.decode(db_jwt, signing_key, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="DB JWT đã hết hạn.")
        except jwt.PyJWTError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="DB JWT hoặc tenant_secret không hợp lệ.")

        if (
            payload.get("token_type") != "mcp_external_db"
            or payload.get("scope") != "read_only"
            or payload.get("tenant_id") != str(tenant_id)
            or payload.get("connector_id") != str(connector_id)
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="DB JWT không khớp phạm vi Tenant/Connector.")
        return payload

    @classmethod
    def validate_readonly_sql(cls, sql_query: str, allowed_tables: List[str]) -> str:
        """Kiểm tra nghiêm ngặt câu lệnh SQL chỉ đọc (SELECT) và nằm trong danh sách bảng cho phép."""
        cleaned = sql_query.strip()
        if not cleaned:
            raise ValueError("Câu lệnh SQL không được để trống.")
        if ";" in cleaned.rstrip(";"):
            raise ValueError("Không cho phép nhiều câu lệnh SQL cùng lúc.")
        cleaned = cleaned.rstrip(";").strip()

        if "--" in cleaned or "/*" in cleaned or "*/" in cleaned:
            raise ValueError("Không cho phép comment trong câu lệnh SQL.")
        if not re.match(r"^\s*SELECT\b", cleaned, re.IGNORECASE):
            raise ValueError("Chỉ cho phép câu lệnh SELECT (Read-Only).")
        if _FORBIDDEN_SQL_KEYWORDS.search(cleaned):
            raise ValueError("Câu lệnh chứa từ khóa bị cấm.")

        allowed_lower = {t.lower() for t in allowed_tables}
        referenced = re.findall(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", cleaned, re.IGNORECASE)
        if not referenced:
            raise ValueError("Câu lệnh SELECT phải chỉ rõ bảng trong mệnh đề FROM.")
        for tbl in referenced:
            if tbl.lower() not in allowed_lower:
                raise ValueError(f"Bảng '{tbl}' không nằm trong danh sách allowed_tables.")

        if not re.search(r"\bLIMIT\s+\d+\b", cleaned, re.IGNORECASE):
            cleaned = f"{cleaned} LIMIT {cls.MAX_ROWS}"
        return cleaned

    @classmethod
    async def execute_readonly_query(
        cls,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        connector_id: uuid.UUID,
        db_jwt: str,
        tenant_secret: str,
        sql_query: str,
    ) -> Dict[str, Any]:
        """Giải mã DSN tạm thời trong RAM, xác thực DB JWT và chạy truy vấn SELECT Read-Only."""
        token_payload = cls.verify_db_jwt(db_jwt, tenant_id, connector_id, tenant_secret)
        connector = (
            await db.execute(
                select(ExternalDBConnector).where(
                    ExternalDBConnector.id == connector_id,
                    ExternalDBConnector.tenant_id == tenant_id,
                    ExternalDBConnector.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if not connector:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kết nối DB không tồn tại.")

        allowed_tables = list(set(connector.allowed_tables).intersection(token_payload.get("allowed_tables", [])))
        safe_sql = cls.validate_readonly_sql(sql_query, allowed_tables)
        dsn = cls.decrypt_dsn(connector.encrypted_dsn, connector.encryption_salt, tenant_id, tenant_secret)

        connect_args: Dict[str, Any] = {"statement_cache_size": 0} if "asyncpg" in dsn else {}
        ext_engine = create_async_engine(dsn, echo=False, pool_pre_ping=True, connect_args=connect_args)
        try:
            async with ext_engine.connect() as conn:
                res = await conn.execute(text(safe_sql))
                rows = [dict(r._mapping) for r in res.fetchmany(cls.MAX_ROWS)]
                return {
                    "connector_id": str(connector.id),
                    "connector_name": connector.name,
                    "row_count": len(rows),
                    "rows": rows,
                }
        finally:
            await ext_engine.dispose()
