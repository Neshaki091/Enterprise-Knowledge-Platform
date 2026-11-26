"""
MÔ TẢ KIẾN TRÚC:
Module Security quản lý việc băm mật khẩu an toàn (Bcrypt) và cấp phát/giải mã JWT tokens.
Tuân thủ SEC-001 (Bcrypt work factor 12) và SEC-005 (Access/Refresh Token với JTI định danh duy nhất).
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
import jwt
from passlib.context import CryptContext
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Băm mật khẩu người dùng bằng thuật toán Bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Kiểm tra mật khẩu nhập vào khớp với chuỗi băm Bcrypt."""
    return pwd_context.verify(plain_password, hashed_password)


def create_jwt_token(
    subject: str,
    token_type: str,
    expires_delta: Optional[timedelta] = None,
    additional_claims: Optional[Dict[str, Any]] = None
) -> Tuple[str, str, datetime]:
    """
    Tạo JWT token có mã định danh duy nhất (JTI) để phục vụ kiểm soát thu hồi (Revocation).
    
    Args:
        subject: ID của User (user_id).
        token_type: 'access' hoặc 'refresh'.
        expires_delta: Thời gian hết hạn tùy biến.
        additional_claims: Các thông tin bổ sung (tenant_id, role, department_id,...).
        
    Returns:
        Tuple: (encoded_jwt_str, jti_str, expires_at_datetime)
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expires_at = now + expires_delta
    elif token_type == "access":
        expires_at = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    else:
        expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    jti = str(uuid.uuid4())
    payload: Dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "jti": jti,
        "iat": now,
        "exp": expires_at,
    }
    if additional_claims:
        payload.update(additional_claims)

    encoded_jwt = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt, jti, expires_at


def decode_jwt_token(token: str) -> Dict[str, Any]:
    """
    Giải mã và xác thực chữ ký của JWT token.
    
    Raises:
        jwt.ExpiredSignatureError: Token đã quá hạn.
        jwt.InvalidTokenError: Token không hợp lệ hoặc bị giả mạo.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
