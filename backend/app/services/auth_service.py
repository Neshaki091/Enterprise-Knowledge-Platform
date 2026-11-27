"""
MÔ TẢ KIẾN TRÚC:
Service Layer quản lý logic nghiệp vụ Xác thực (Authentication) & Quản lý Phiên.
Triển khai cơ chế xoay vòng Refresh Token (Token Rotation) và Phát hiện Tái sử dụng (Reuse Detection).
Tuân thủ SEC-001, SEC-004, SEC-005.
"""

import asyncio
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from fastapi import HTTPException, status
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.security import hash_password, verify_password, create_jwt_token, decode_jwt_token
from app.models.tenant import Tenant
from app.models.user import User
from app.models.membership import Membership
from app.models.token import RevokedToken
from app.schemas.auth import (
    MembershipSummary,
    UserRegisterRequest,
    UserLoginRequest,
    TokenResponse,
)

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Tạo slug an toàn từ chuỗi văn bản."""
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "-", slug)


class AuthService:
    """Xử lý toàn bộ logic xác thực, phân quyền và xoay vòng tokens."""

    @staticmethod
    async def register(db: AsyncSession, data: UserRegisterRequest) -> Tuple[User, Tenant, Membership]:
        """
        Đăng ký người dùng mới kèm Tenant ban đầu. Người đăng ký đầu tiên có vai trò OWNER.
        """
        # 1. Kiểm tra email đã tồn tại chưa
        existing_user = await db.execute(select(User).where(User.email == data.email))
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{data.email}' đã được đăng ký trong hệ thống",
            )

        # 2. Xử lý slug cho Tenant
        slug = data.tenant_slug or _slugify(data.tenant_name)
        existing_tenant = await db.execute(select(Tenant).where(Tenant.slug == slug))
        if existing_tenant.scalar_one_or_none():
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        # 3. Tạo Tenant mới
        new_tenant = Tenant(
            name=data.tenant_name,
            slug=slug,
            quota_limits={"tier": "FREE", "max_users": 10, "max_documents": 50},
            is_active=True,
        )
        db.add(new_tenant)
        await db.flush()

        # 4. Tạo User mới
        new_user = User(
            email=data.email,
            hashed_password=await asyncio.to_thread(hash_password, data.password),
            full_name=data.full_name,
            is_active=True,
            is_superuser=False,
        )
        db.add(new_user)
        await db.flush()

        # 5. Tạo Membership gắn User là OWNER của Tenant
        membership = Membership(
            user_id=new_user.id,
            tenant_id=new_tenant.id,
            role="OWNER",
            is_active=True,
        )
        db.add(membership)
        await db.commit()
        await db.refresh(new_user)
        await db.refresh(new_tenant)
        await db.refresh(membership)

        return new_user, new_tenant, membership

    @staticmethod
    async def authenticate(db: AsyncSession, data: UserLoginRequest) -> Tuple[User, uuid.UUID, str]:
        """
        Xác thực danh tính người dùng và kiểm tra quyền truy cập vào Tenant.
        """
        # 1. Tìm User qua email
        user_res = await db.execute(select(User).where(User.email == data.email))
        user = user_res.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email hoặc mật khẩu không chính xác",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Bcrypt là CPU-bound (~200ms); chạy trong thread để không chặn event loop
        # của worker duy nhất khi có nhiều người đăng nhập đồng thời.
        if not await asyncio.to_thread(verify_password, data.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email hoặc mật khẩu không chính xác",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tài khoản người dùng đã bị khóa",
            )

        # 2. Xác định Tenant mục tiêu
        if data.tenant_id:
            membership_res = await db.execute(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.tenant_id == data.tenant_id,
                    Membership.is_active.is_(True)
                )
            )
            membership = membership_res.scalar_one_or_none()
            if not membership and not user.is_superuser:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Bạn không có quyền truy cập vào Tenant này",
                )
            tenant_id = data.tenant_id
            role = "SUPER_ADMIN" if user.is_superuser else membership.role
        else:
            first_membership_res = await db.execute(
                select(Membership)
                .join(Tenant, Membership.tenant_id == Tenant.id)
                .where(
                    Membership.user_id == user.id,
                    Membership.is_active.is_(True),
                    Tenant.is_active.is_(True),
                )
            )
            membership = first_membership_res.scalars().first()
            if not membership and not user.is_superuser:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Người dùng chưa thuộc Tenant nào đang hoạt động (hoặc Tenant đã bị tạm khóa)",
                )
            tenant_id = membership.tenant_id if membership else uuid.uuid4()
            role = "SUPER_ADMIN" if user.is_superuser else membership.role

        # 3. Kiểm tra trạng thái hoạt động của Tenant mục tiêu (trừ Super-Admin)
        if not user.is_superuser:
            tenant_obj = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
            if not tenant_obj or not tenant_obj.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Doanh nghiệp (Tenant) đã bị tạm khóa hoạt động",
                )

        return user, tenant_id, role

    @staticmethod
    def create_tokens(
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        role: str,
        family_id: Optional[str] = None,
    ) -> TokenResponse:
        """Sinh cặp Access Token và Refresh Token mới kèm JTI và mã phiên (sid) dùng chung."""
        session_family = family_id or str(uuid.uuid4())
        claims = {
            "tenant_id": str(tenant_id),
            "role": role,
            "sid": session_family,
        }
        access_token, _, _ = create_jwt_token(
            subject=str(user_id),
            token_type="access",
            additional_claims=claims,
        )
        refresh_token, _, _ = create_jwt_token(
            subject=str(user_id),
            token_type="refresh",
            additional_claims=claims,
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in_seconds=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            tenant_id=tenant_id,
            role=role,
        )

    @staticmethod
    async def refresh_tokens(db: AsyncSession, refresh_token_str: str) -> TokenResponse:
        """
        Làm mới Access Token thông qua Refresh Token với quy chuẩn:
        1. Token Rotation: Refresh Token cũ lập tức bị thu hồi sau khi đổi mới.
        2. Reuse Detection: Nếu phát hiện token đã thu hồi bị gửi lại, lập tức hủy phiên toàn bộ.
        """
        try:
            payload = decode_jwt_token(refresh_token_str)
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token đã hết hạn, vui lòng đăng nhập lại",
            )
        except jwt.InvalidTokenError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token không hợp lệ",
            )

        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Yêu cầu cung cấp Refresh Token hợp lệ",
            )

        jti = payload.get("jti")
        family_id = str(payload.get("sid") or jti or "")
        user_id_str = payload.get("sub")
        tenant_id_str = payload.get("tenant_id")
        try:
            user_uuid = uuid.UUID(str(user_id_str))
            tenant_uuid = uuid.UUID(str(tenant_id_str))
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token thiếu định danh người dùng hoặc Tenant hợp lệ",
            )

        # 1. KIỂM TRA MÃ PHIÊN (sid) ĐÃ BỊ THU HỒI CHƯA
        family_check = await db.execute(
            select(RevokedToken).where(
                RevokedToken.jti == family_id,
                RevokedToken.token_type == "family",
            )
        )
        if family_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Phiên đăng nhập đã bị thu hồi (Token Family Revoked). Vui lòng đăng nhập lại.",
            )

        # 2. PHÁT HIỆN TÁI SỬ DỤNG TOKEN (Token Reuse Detection - SEC-005)
        revoked_check = await db.execute(select(RevokedToken).where(RevokedToken.jti == jti))
        if revoked_check.scalar_one_or_none():
            # Kẻ gian tái sử dụng token cũ: thu hồi toàn bộ family (cả refresh token mới nhất và access token).
            # Marker phải sống đủ vòng đời refresh token mới nhất, không dùng exp của token cũ.
            await AuthService._revoke_family(
                db,
                family_id=family_id,
                user_id=user_uuid,
                expires_at=AuthService._family_marker_expiry(),
                reason="reuse_detected",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Phát hiện tái sử dụng Token đã thu hồi (Refresh Token Reuse Detected). Toàn bộ phiên đã bị hủy ngay lập tức!",
            )

        # 2. Kiểm tra User & Membership còn hoạt động không
        user_res = await db.execute(select(User).where(User.id == user_uuid, User.is_active.is_(True)))
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Người dùng không hoạt động")

        membership_res = await db.execute(
            select(Membership).where(
                Membership.user_id == user_uuid,
                Membership.tenant_id == tenant_uuid,
                Membership.is_active.is_(True)
            )
        )
        membership = membership_res.scalar_one_or_none()
        if not membership and not user.is_superuser:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tư cách thành viên trong Tenant không còn hiệu lực")

        if not user.is_superuser:
            tenant_obj = (await db.execute(select(Tenant).where(Tenant.id == tenant_uuid))).scalar_one_or_none()
            if not tenant_obj or not tenant_obj.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Doanh nghiệp (Tenant) đã bị tạm khóa hoạt động",
                )

        role = "SUPER_ADMIN" if user.is_superuser else membership.role

        # 3. Thu hồi Refresh Token hiện tại (Xoay vòng - Rotation)
        exp_ts = payload.get("exp")
        exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc) if exp_ts else datetime.now(timezone.utc)
        revocation = RevokedToken(
            jti=jti,
            token_type="refresh",
            user_id=user_uuid,
            expires_at=exp_dt,
            reason="token_rotation"
        )
        db.add(revocation)
        await db.commit()

        # 4. Cấp phát cặp token hoàn toàn mới nhưng GIỮ NGUYÊN mã phiên (sid)
        #    để Reuse Detection có thể thu hồi cả family nếu token cũ bị phát hiện tái sử dụng.
        return AuthService.create_tokens(user_uuid, tenant_uuid, role, family_id=family_id)

    @staticmethod
    def _family_marker_expiry() -> datetime:
        """Marker family phải tồn tại đủ vòng đời refresh token (7 ngày) kể từ thời điểm thu hồi."""
        return datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    @staticmethod
    async def _revoke_family(
        db: AsyncSession,
        family_id: str,
        user_id: uuid.UUID,
        expires_at: datetime,
        reason: str,
    ) -> None:
        """Thu hồi toàn bộ phiên đăng nhập (token family) và commit ngay lập tức."""
        if not family_id:
            return
        existing = await db.execute(
            select(RevokedToken).where(
                RevokedToken.jti == family_id,
                RevokedToken.token_type == "family",
            )
        )
        if existing.scalar_one_or_none():
            await db.commit()
            return
        db.add(
            RevokedToken(
                jti=family_id,
                token_type="family",
                user_id=user_id,
                expires_at=expires_at,
                reason=reason,
            )
        )
        await db.commit()

    @staticmethod
    async def list_memberships(
        db: AsyncSession,
        user_id: uuid.UUID,
        is_superuser: bool = False,
    ) -> List[MembershipSummary]:
        """Liệt kê các Tenant đang hoạt động mà người dùng có thể chuyển sang."""
        if is_superuser:
            tenants = (await db.execute(
                select(Tenant).where(Tenant.is_active.is_(True)).order_by(Tenant.name.asc())
            )).scalars().all()
            return [
                MembershipSummary(
                    tenant_id=t.id,
                    tenant_name=t.name,
                    tenant_slug=t.slug,
                    role="SUPER_ADMIN",
                    department_id=None,
                    is_active=True,
                )
                for t in tenants
            ]

        rows = (await db.execute(
            select(Membership, Tenant)
            .join(Tenant, Membership.tenant_id == Tenant.id)
            .where(
                Membership.user_id == user_id,
                Membership.is_active.is_(True),
                Tenant.is_active.is_(True),
            )
            .order_by(Tenant.name.asc())
        )).all()
        return [
            MembershipSummary(
                tenant_id=tenant.id,
                tenant_name=tenant.name,
                tenant_slug=tenant.slug,
                role=membership.role,
                department_id=membership.department_id,
                is_active=membership.is_active,
            )
            for membership, tenant in rows
        ]

    @staticmethod
    async def switch_tenant(
        db: AsyncSession,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> TokenResponse:
        """Đổi Tenant làm việc và cấp cặp token mới (phiên mới) sau khi đối soát quyền."""
        user = (await db.execute(
            select(User).where(User.id == user_id, User.is_active.is_(True))
        )).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Người dùng không hoạt động")

        tenant = (await db.execute(
            select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
        )).scalar_one_or_none()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant không tồn tại hoặc đã bị tạm khóa",
            )

        if user.is_superuser:
            return AuthService.create_tokens(user.id, tenant.id, "SUPER_ADMIN")

        membership = (await db.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.tenant_id == tenant.id,
                Membership.is_active.is_(True),
            )
        )).scalar_one_or_none()
        if not membership:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không phải thành viên đang hoạt động của Tenant này",
            )
        return AuthService.create_tokens(user.id, tenant.id, membership.role)

    @staticmethod
    async def logout(
        db: AsyncSession,
        access_token_str: str,
        refresh_token_str: Optional[str] = None
    ) -> None:
        """Đăng xuất, thu hồi access/refresh token và toàn bộ token family của phiên."""
        family_ids: List[str] = []
        token_user_id: Optional[uuid.UUID] = None
        try:
            payload = decode_jwt_token(access_token_str)
            jti = payload.get("jti")
            user_uuid = uuid.UUID(str(payload.get("sub")))
            exp_ts = payload.get("exp")
            exp_dt = datetime.fromtimestamp(exp_ts, tz=timezone.utc) if exp_ts else datetime.now(timezone.utc)
            token_user_id = user_uuid
            if jti:
                db.add(RevokedToken(
                    jti=jti,
                    token_type="access",
                    user_id=user_uuid,
                    expires_at=exp_dt,
                    reason="user_logout",
                ))
            if payload.get("sid"):
                family_ids.append(str(payload["sid"]))
        except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
            # Access token đã được dependency xác thực trước đó; nếu tới đây thì chỉ ghi log,
            # không được phép làm hỏng thao tác đăng xuất của người dùng.
            logger.warning("Không thể giải mã access token khi logout: %s", exc)

        if refresh_token_str:
            try:
                r_payload = decode_jwt_token(refresh_token_str)
                r_jti = r_payload.get("jti")
                r_user_uuid = uuid.UUID(str(r_payload.get("sub")))
                r_exp_ts = r_payload.get("exp")
                r_exp_dt = datetime.fromtimestamp(r_exp_ts, tz=timezone.utc) if r_exp_ts else datetime.now(timezone.utc)
                if token_user_id is None:
                    token_user_id = r_user_uuid
                if r_jti:
                    db.add(RevokedToken(
                        jti=r_jti,
                        token_type="refresh",
                        user_id=r_user_uuid,
                        expires_at=r_exp_dt,
                        reason="user_logout",
                    ))
                if r_payload.get("sid"):
                    family_ids.append(str(r_payload["sid"]))
            except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
                logger.warning("Không thể giải mã refresh token khi logout: %s", exc)

        # Thu hồi family để mọi access token đã phát hành trong phiên chết ngay lập tức
        if token_user_id is not None:
            for family_id in set(family_ids):
                existing = await db.execute(
                    select(RevokedToken).where(
                        RevokedToken.jti == family_id,
                        RevokedToken.token_type == "family",
                    )
                )
                if existing.scalar_one_or_none():
                    continue
                db.add(RevokedToken(
                    jti=family_id,
                    token_type="family",
                    user_id=token_user_id,
                    expires_at=AuthService._family_marker_expiry(),
                    reason="user_logout",
                ))

        await db.commit()
